"""USB microphone -> cloud ASR -> child chat -> cloud TTS -> robot speaker.

No web server or persistent child conversations/audio. Requested photos may be saved.
"""
import argparse
import binascii
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import re
import struct
import threading
import time
import tkinter as tk
import wave
from tkinter import ttk

import serial
from serial.tools import list_ports
from providers import Cloud, ProviderError
from parent_tasks import ParentTasks
from weather import LOCATION, SOURCE
import camera as camera_module
import wake as wake_module
from idle_scenes import DrinkReminder, drink_pcm
from workday_alarm import WorkdayAlarm, alarm_pcm
from daily_routines import DailyRoutines, hourly_pcm
from device_controls import packet as control_packet


class Bridge:
    def __init__(self, port, notify, model='qwen3.8-flash', provider='dashscope', max_turns=20):
        self.port, self.notify = port, notify
        self.cloud = Cloud(model, provider, max_calls=max_turns*3+3)
        self.max_turns, self.turns = max_turns, 0
        self.history = []
        self.epoch = 0
        self.closed = threading.Event()
        self.write_lock = threading.RLock()
        self.condition = threading.Condition()
        self.upload_ready, self.upload_ack = False, 0
        self.uploading = False
        self.serial = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='clawd-cloud')
        self.future = None
        self.started = time.monotonic()
        self.pending_history = None
        self.state = 'connecting'
        self.last_error = None
        self.continuous_enabled = True
        self.continuous_active = False
        self.next_listen_at = None
        self.idle_since = None
        self.record_finished_at = None
        self.response_started_at = None
        self.parent_handler = None
        self.parent_capture = False
        self.pending_parent_ack = None
        self.phase = 'idle'
        self.task_status = 'idle'
        self.sent_task_status = None
        self.next_listen_cue = True
        self.parent_restart_at = None
        self.parent_expect_cancel = False
        # Camera: the device streams one JPEG per 'g' request, only while idle.
        self.camera = None
        self.photo_waiting = None
        self.photo_pending = False
        self.photo_requested_at = None
        self.device_dance = False
        self.pending_dance = None
        self.dance_token = None
        self.dance_deadline = None
        self.wake_enabled = False
        self.device_wake = False
        self.wake_engine = None
        self.wake_loading = None
        self.wake_requested = False
        self.wake_session = None
        self.wake_retry_at = 0.0
        self.wake_first_record = False
        self.photo_preview = False
        self.wake_last_metrics = 0.0
        self.activity_s = 0.0
        self.activity_tick = time.monotonic()
        self.listen_deadline = None
        self.wake_hit_at = None
        self.device_pcm_status=False
        self.device_scenes=False
        self.drink_reminder=DrinkReminder()
        self.scene_request=None
        self.reminder_token=None
        self.reminder_audio=None
        self.reminder_started=False
        self.reminder_deadline=None
        self.workday_alarm=WorkdayAlarm()
        self.alarm_token=None
        self.alarm_requested=False
        self.alarm_remaining=0
        self.alarm_next_at=0.0
        self.alarm_playing=False
        self.alarm_deadline=None
        self.alarm_audio=None
        self.alarm_scheduled=False
        self.alarm_slot=None
        self.alarm_claimed=False
        self.chat_expired=False
        self.daily=DailyRoutines()
        self.device_sleep=False
        self.sent_sleep=None
        self.sleep_preview_until=0.0
        self.hourly_requested=False
        self.reminder_kind='drink'
        self.device_controls=False
        self.control_requests={}
        self.control_active=False
        self.control_deadline=None
        self.control_status={}
        self.wake_device_active=False
        self.control_wake_ready_at=0.0

    def request_control(self, kind, *values):
        control_packet(kind,values)  # Validate before changing intent.
        if self.state!='ready' or self.closed.is_set() or not self.device_controls:
            self.report('设备未连接或固件不支持手动控制。');return False
        with self.write_lock:self.control_requests[kind]=values
        self.report('等小克空闲后应用'+{'pose':'角度','volume':'音量','status':'设备状态'}[kind]+'。')
        return True

    def service_controls(self):
        if self.control_active:
            if self.control_deadline and time.monotonic()>self.control_deadline:
                self.cancel();self.report('手动控制超时，动作已停止。')
            return
        if not self.control_requests or not self.scene_idle():return
        # Keep draining framed mic output until the native worker has released
        # the codec. Sending G immediately after U can race a blocked wake frame.
        if self.wake_requested or self.wake_device_active:
            self.stop_wake();return
        if time.monotonic()<self.control_wake_ready_at:return
        with self.write_lock:
            if not self.control_requests or not self.scene_idle():return
            kind=next(k for k in ('volume','pose','status') if k in self.control_requests)
            values=self.control_requests.pop(kind)
            self.stop_wake();self.control_active=True
            self.control_deadline=time.monotonic()+6
            self.phase='controlling'
            self.send(control_packet(kind,values))
            self.connection_event('control_request',kind=kind,values=list(values))

    def control_event(self, line):
        if line.startswith(b'CLAWD CONTROL_STATUS '):
            fields={k.decode():int(v) for k,v in re.findall(rb'(\w+)=(-?\d+)',line)}
            self.control_status.update(fields)
            self.connection_event('control_feedback',**fields)
        elif line.startswith((b'CLAWD CONTROL_DONE',b'CLAWD CONTROL_ERROR')):
            self.connection_event('control_result',details=line.decode('ascii').strip())
            if b'reason=frame' in line:raise RuntimeError('手动控制数据校验失败，正在重新连接。')
            self.control_active=False;self.control_deadline=None
            if self.phase=='controlling':self.phase='idle'
            self.wake_retry_at=time.monotonic()+.9
            if b'CONTROL_ERROR' in line:self.report('设备暂时无法调整，请等空闲后重试。')
            elif b'reason=timeout' in line:self.report('动作已停止，未能到达目标角度；可查看实际位置。')
            else:self.report('调整完成。说“你好小克”或点屏幕可聊天。')

    def request_hourly(self):
        self.hourly_requested=True
        self.report('等小克空闲后试播当前整点报时。')

    def preview_sleep(self):
        self.sleep_preview_until=time.monotonic()+12
        self.report('空闲时预览12秒睡觉动效；点屏幕仍可聊天。')

    def service_daily(self, now=None):
        from datetime import datetime
        from idle_scenes import BEIJING
        now=now or datetime.now(BEIJING)
        sleeping=self.daily.sleeping(now) or time.monotonic()<self.sleep_preview_until
        if self.device_sleep and self.state=='ready' and not self.uploading:
            # Retain visual intent through interactions; native UI gives the face priority.
            if self.write_lock.acquire(blocking=False):
                try:
                    if not self.uploading and sleeping!=self.sent_sleep:
                        self.send(b'Z' if sleeping else b'z');self.sent_sleep=sleeping
                finally:self.write_lock.release()
        slot=self.daily.hourly_due(now)
        if not (slot or self.hourly_requested) or not self.scene_idle() or time.monotonic()<self.wake_retry_at:return
        with self.write_lock:
            if not self.scene_idle():return
            self.hourly_requested=False
            try:
                pcm=hourly_pcm(now.astimezone(BEIJING).hour)
                if slot:self.daily.claim_hour(slot)
            except (OSError,ValueError,wave.Error):
                if slot:self.daily.last_hour=slot
                self.connection_event('hourly_asset_or_settings_error')
                self.report('本机报时语音或设置不可用，本次跳过。');return
            self.stop_wake()
            self.reminder_kind='hourly';self.reminder_token=self.epoch
            self.reminder_deadline=time.monotonic()+16
            self.phase='reminding';self.send(b'i')
            self.connection_event('hourly_announcement',hour=now.hour,scheduled=bool(slot))
            self.future=self.pool.submit(self.play_reminder,pcm,self.epoch)

    def set_wake(self, enabled):
        # UI only changes intent. The sole USB reader applies it between frames.
        self.wake_enabled = bool(enabled)

    def wake_eligible(self):
        return (self.wake_enabled and self.device_wake and self.state=='ready'
                and not self.closed.is_set() and self.turns<self.max_turns
                and self.phase in ('idle','paused') and not self.uploading
                and not self.photo_pending and not self.photo_preview
                and self.dance_token is None and self.pending_dance is None
                and not self.parent_capture and self.parent_restart_at is None
                and not self.continuous_active and self.next_listen_at is None
                and self.reminder_token is None
                and self.alarm_token is None and not self.chat_expired
                and not self.control_active and not self.control_requests
                and not (self.future and not self.future.done()))

    def request_alarm(self):
        self.alarm_requested=True
        self.report('等小克空闲后试播起床闹钟。点屏幕或“停止”可结束。')

    def clear_alarm(self):
        self.alarm_token=None;self.alarm_audio=None;self.alarm_remaining=0
        self.alarm_playing=False;self.alarm_deadline=None;self.alarm_scheduled=False
        self.alarm_slot=None;self.alarm_claimed=False

    def claim_alarm(self):
        if self.alarm_token is None or self.alarm_claimed:return
        try:self.workday_alarm.claim(self.alarm_slot)
        except OSError:
            self.connection_event('alarm_settings_save_error')
            self.report('叫醒已开始，但本机状态未保存；请检查设置文件权限。')
        self.alarm_claimed=True
        if self.alarm_scheduled:
            try:self.daily.alarm_started()
            except OSError:self.connection_event('sleep_settings_save_error')
            self.sleep_preview_until=0.0

    def service_alarm(self, now=None):
        slot=self.workday_alarm.due(now)
        if self.alarm_token is not None:
            if ((not self.workday_alarm.enabled and self.alarm_scheduled) or
                    (self.alarm_deadline is not None and time.monotonic()>self.alarm_deadline)):
                self.cancel();return
            if (not self.alarm_playing and time.monotonic()>=self.alarm_next_at
                    and not (self.future and not self.future.done())):
                self.alarm_playing=True
                self.future=self.pool.submit(self.play_alarm,self.alarm_audio,self.alarm_token)
            return
        if (not (slot or self.alarm_requested) or not self.scene_idle()
                or time.monotonic()<self.wake_retry_at):return
        with self.write_lock:
            if not self.scene_idle():return
            try:
                pcm=alarm_pcm()
            except (OSError,ValueError,wave.Error):
                self.workday_alarm.pending=None
                self.workday_alarm.last=slot or self.workday_alarm.last
                self.alarm_requested=False
                self.connection_event('alarm_asset_or_settings_error')
                self.report('起床闹钟未能播放，请检查本机语音文件和设置。');return
            self.alarm_requested=False;self.pause_continuous();self.stop_wake()
            self.alarm_token=self.epoch;self.alarm_audio=pcm;self.alarm_remaining=3
            self.alarm_playing=False;self.alarm_slot=slot;self.alarm_claimed=False
            self.alarm_next_at=time.monotonic();self.alarm_deadline=self.alarm_next_at+90
            self.alarm_scheduled=bool(slot);self.phase='alarming'
            self.send(b'i')  # Stop any forced ambient scene; keep the ordinary speaking face.
            self.connection_event('workday_alarm',scheduled=bool(slot),repetitions=3)
            self.report('起床闹钟开始啦。点屏幕或“停止”可结束。')

    def play_alarm(self, pcm, token):
        try:
            if self.current(token) and self.alarm_token==token:self.play(pcm,token,alarm=True)
        except (OSError,RuntimeError,serial.SerialException):
            if self.current(token):self.cancel()

    def alarm_play_done(self, line):
        if self.alarm_token!=self.epoch:return False
        self.claim_alarm()
        self.alarm_remaining-=1;self.alarm_playing=False
        if b'cancelled=0' not in line or self.alarm_remaining<=0:
            self.clear_alarm();self.phase='idle';self.wake_retry_at=time.monotonic()+.9
            self.report('起床闹钟结束。说“你好小克”或点屏幕可聊天。')
        else:
            self.phase='alarming';self.alarm_next_at=time.monotonic()+5
            self.report('小克还会再喊你。点屏幕或“停止”可结束。')
        return True

    def enforce_chat_limit(self, now=None):
        local_enabled=self.workday_alarm.enabled or self.daily.hourly_enabled or self.daily.sleep_enabled
        if self.chat_expired and not local_enabled:return True
        now=time.monotonic() if now is None else now
        elapsed=self.activity_s if (self.wake_enabled or local_enabled) else now-self.started
        if elapsed<=1800:return False
        if not local_enabled:
            self.report('本次30分钟已结束，麦克风已关闭。');return True
        if not self.chat_expired:
            self.chat_expired=True;self.cancel()
            self.report('本次聊天时间已到，聊天暂停；本机起床闹钟仍保留。重新打开可恢复聊天。')
        return False

    def request_scene(self, kind):
        if kind not in ('love','drink'):return
        with self.write_lock:self.scene_request=kind
        self.report('等小克空闲后'+('送爱心。' if kind=='love' else '播放喝水提醒。'))

    def scene_idle(self):
        return (self.device_scenes and self.state=='ready' and not self.closed.is_set()
                and self.phase in ('idle','paused') and not self.uploading
                and not self.photo_pending and not self.photo_preview
                and not self.parent_capture and self.parent_restart_at is None
                and self.dance_token is None and self.pending_dance is None
                and not self.continuous_active and self.next_listen_at is None
                and self.reminder_token is None
                and self.alarm_token is None
                and not self.control_active
                and not (self.future and not self.future.done()))

    def clear_reminder(self):
        self.reminder_token=None;self.reminder_audio=None
        self.reminder_started=False;self.reminder_deadline=None
        self.reminder_kind='drink'

    def service_scenes(self, now=None):
        slot=None if self.daily.sleeping(now) else self.drink_reminder.due(now)
        if self.daily.sleeping(now):self.drink_reminder.pending=None
        if self.reminder_deadline and time.monotonic()>self.reminder_deadline:
            self.cancel();self.report('本机提醒已超时停止，聊天仍可使用。')
        if not self.scene_idle():return
        with self.write_lock:
            if not self.scene_idle():return
            kind=self.scene_request or ('drink' if slot else None)
            if kind is None:return
            self.scene_request=None
            if kind=='love':
                self.send(b'A');return
            try:
                pcm=drink_pcm()
                self.drink_reminder.claim(slot)
            except (OSError,ValueError,wave.Error):
                # No background cloud fallback. Skip this slot rather than retrying aloud.
                self.drink_reminder.pending=None
                self.drink_reminder.last=slot or self.drink_reminder.last
                self.connection_event('drink_asset_error')
                self.report('本机喝水提醒语音缺失；本次跳过，聊天正常。');return
            self.stop_wake()
            self.reminder_token=self.epoch;self.reminder_audio=pcm
            self.reminder_started=False;self.reminder_deadline=time.monotonic()+16
            self.phase='reminding'
            self.send(b'C')
            self.connection_event('drink_reminder',scheduled=bool(slot))

    def scene_event(self, line):
        if self.reminder_token!=self.epoch or self.reminder_kind!='drink':return
        if line.startswith(b'CLAWD SCENE_START kind=drink') and not self.reminder_started:
            self.reminder_started=True
            token=self.reminder_token;pcm=self.reminder_audio;self.reminder_audio=None
            self.future=self.pool.submit(self.play_reminder,pcm,token)
        elif line.startswith((b'CLAWD SCENE_DONE kind=drink',b'CLAWD SCENE_ERROR')):
            completed=b'reason=complete' in line
            self.clear_reminder()
            if self.phase=='reminding':self.phase='idle'
            if completed:self.report('喝水提醒结束啦。说“你好小克”或点屏幕可聊天。')

    def play_reminder(self, pcm, token):
        try:
            if self.reminder_token==token and self.current(token):self.play(pcm,token,reminder=True)
        except (OSError,RuntimeError,serial.SerialException):
            if self.current(token):self.cancel()

    def stop_wake(self, cooldown=.9):
        with self.write_lock:
            if self.wake_requested and not self.uploading:
                self.send(b'U')
            self.wake_requested=False
            self.wake_session=None
            self.wake_retry_at=time.monotonic()+cooldown
            if self.wake_engine:self.wake_engine.reset()

    def service_wake(self):
        now=time.monotonic()
        if self.phase=='arming' and self.listen_deadline is not None and now>self.listen_deadline:
            self.listen_deadline=None
            self.cancel()
            self.report('设备没有及时进入录音，已暂停；可点屏幕重试。')
        # Idle local listening does not consume the original 30-minute chat guard.
        if (self.alarm_token is None and self.reminder_token is None and not self.control_active and
                (self.phase not in ('idle','paused') or (self.future and not self.future.done()))):
            self.activity_s += now-self.activity_tick
        self.activity_tick=now
        if (self.wake_enabled and self.device_wake and self.state=='ready'
                and self.wake_engine is None and self.wake_loading is None):
            self.wake_loading=wake_module.load_async()
            self.report('正在加载本地唤醒模型，触屏仍可使用……')
        if self.wake_loading is not None and not self.wake_loading.empty():
            result=self.wake_loading.get_nowait();self.wake_loading=None
            if isinstance(result,Exception):
                self.wake_enabled=False
                self.connection_event('wake_load_error',error_type=type(result).__name__)
                self.report('本地唤醒模型加载失败；触屏可继续使用，请检查 wake-word 环境。')
            else:self.wake_engine=result
        eligible=self.wake_eligible()
        if not eligible:
            if self.wake_requested:self.stop_wake()
            return
        if self.wake_engine is None or now<self.wake_retry_at:return
        if not self.wake_requested:
            self.wake_requested=True
            self.wake_retry_at=now+3
            self.send(b'L')
        elif self.wake_session is None and now>=self.wake_retry_at:
            self.stop_wake()
        if self.wake_engine and now-self.wake_last_metrics>=60:
            self.wake_last_metrics=now
            self.connection_event('wake_metrics',**self.wake_engine.metrics())

    def wake_frame(self, session, sequence, pcm):
        epoch=self.epoch
        if not (self.wake_requested and session==self.wake_session and self.wake_eligible()
                and self.wake_engine):return
        try:hit=self.wake_engine.feed(session,sequence,pcm)
        except Exception as exc:
            self.wake_enabled=False;self.stop_wake()
            self.connection_event('wake_decode_error',error_type=type(exc).__name__)
            self.report('本地唤醒检测出错，已关闭；触屏聊天仍可使用。');return
        with self.write_lock:
            if (hit and self.wake_eligible() and self.wake_requested
                    and session==self.wake_session and epoch==self.epoch):
                self.wake_hit_at=time.monotonic()
                self.connection_event('wake_hit',session=session,sequence=sequence,
                                      **self.wake_engine.metrics())
                self.listen(wake=True)

    def request_photo(self, timeout=12):
        """Ask the device for one JPEG and wait for it. Returns bytes or None.

        Only called from the cloud worker while the device is idle; the reply
        loop hands the assembled image back through photo_waiting.
        """
        if self.camera is None or self.closed.is_set() or self.state != 'ready':
            return None
        if self.uploading or self.photo_pending:
            return None
        # photo_waiting: None = idle, False = waiting, bytes = frame arrived.
        token=self.epoch
        self.photo_pending = True
        self.photo_waiting = False
        self.photo_requested_at = time.monotonic()
        with self.write_lock:
            if self.uploading:
                self.photo_waiting = None
                self.photo_pending = False
                return None
            self.send(b'g')
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.photo_waiting is False and not self.closed.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.connection_event('photo_timeout')
                    break
                self.condition.wait(min(remaining, 0.2))
            result = self.photo_waiting
            self.photo_waiting = None
        self.photo_pending = False
        return (result or None) if self.current(token) else None

    def photo_context(self, text, history, token=None):
        """Optionally look at the scene, then return prompt text for the reply."""
        if self.camera is None or not camera_module.wants_photo(text):
            return None
        if self.turns > self.max_turns:
            return None
        self.report('小克正在看看……')
        jpeg = self.request_photo()
        if token is not None and not self.current(token):return None
        if jpeg is None:
            return camera_module.UNAVAILABLE
        # Keep the requested JPEG even if the cloud cannot describe it later.
        saved = self.camera.save(jpeg)
        if saved:
            self.connection_event('photo_saved', file=saved.name)
        described = self.camera.describe(jpeg, text)
        if token is not None and not self.current(token):return None
        if described == camera_module.UNAVAILABLE:
            return ('电脑这次已经拍到照片'+('并保存到电脑' if saved else '')+
                    '，但暂时没能识别内容。不要说没有拍到，也不要描述图片内容；可以请她重试。')
        return ('小克的摄像头刚拍到一张照片'+('，已保存到电脑' if saved else '')+
                '。本轮照片资料：' + described +
                '\n按这个说，用1到3句、20到70个汉字，说清你看到了什么；'
                '照片里没有的不要编，看不清就说看不清。')

    def arm_parent(self):
        if self.chat_expired:
            self.report('本次聊天时间已到，请重新打开后再录家长任务。');return False
        if self.state!='ready' or self.closed.is_set():
            self.report(self.unavailable());return False
        if not self.parent_handler:
            self.report('家长任务入口尚未配置。');return False
        if (self.future and not self.future.done()) or self.phase in ('thinking','speaking','dancing'):
            self.report('请等当前这句话回答结束，再录家长任务。');return False
        # Start a fresh segment, rather than inheriting the child's nearly expired window.
        self.cancel()
        self.parent_restart_at=time.monotonic()+0.5
        self.parent_expect_cancel=True
        self.report('准备录家长任务，请听到轻提示音、亮绿灯后开始说。')
        return True

    def resume_parent_if_due(self):
        if (self.parent_restart_at is None or time.monotonic()<self.parent_restart_at
                or self.state!='ready' or self.closed.is_set() or self.uploading
                or (self.future and not self.future.done())):return
        self.parent_restart_at=None
        self.parent_expect_cancel=False
        self.parent_capture=True
        self.listen()

    def set_task_status(self, status):
        self.task_status=status

    def pause_continuous(self):
        self.continuous_active = False
        self.next_listen_at = None

    def set_continuous(self, enabled):
        self.continuous_enabled = bool(enabled)
        if not enabled:
            was_active = self.continuous_active
            self.pause_continuous()
            if was_active:
                self.cancel()

    def schedule_listen(self, delay=0.45, cue=True):
        if self.continuous_enabled and self.continuous_active:
            self.next_listen_at = time.monotonic()+delay
            self.next_listen_cue = cue

    def resume_if_due(self):
        if (self.next_listen_at is None or time.monotonic()<self.next_listen_at
                or not self.continuous_enabled or not self.continuous_active
                or self.closed.is_set() or self.state!='ready' or self.uploading or self.dance_token is not None
                or (self.future and not self.future.done())):
            return
        self.next_listen_at = None
        self.send(b'r' if self.next_listen_cue else b't')

    @staticmethod
    def wants_dance(text):
        text=re.sub(r'[\s，。！？、,.!?～~]','',text)
        return bool(re.fullmatch(
            r'(?:(?:你好)?(?:小克|小橙|机器人))?(?:请)?(?:你)?(?:请|快|来)?'
            r'(?:能不能|可不可以|可以|能)?(?:给我|给我们|帮我)?'
            r'(?:跳(?:个|支|一(?:个|支|段|下))?舞(?:蹈)?|表演(?:一个|一段)?舞蹈)'
            r'(?:给我看|给我们看|看看|一下)?(?:吧|呀|啦|吗|好不好|呗)?',text))

    def dance_after_playback(self, completed):
        requested=self.pending_dance
        self.pending_dance=None
        if not completed or requested!=self.epoch or not self.device_dance or not self.current(self.epoch):
            return False
        self.dance_token=self.epoch
        self.dance_deadline=time.monotonic()+12
        self.next_listen_at=None
        self.phase='dancing'
        self.send(b'B')
        self.report('小克开始跳8秒小螃蟹舞啦！点屏幕可以停下来。')
        return True

    def dance_event(self, line):
        if self.dance_token!=self.epoch:return
        if line.startswith(b'CLAWD DANCE_START'):
            self.phase='dancing'
        elif line.startswith((b'CLAWD DANCE_DONE',b'CLAWD DANCE_ERROR')):
            self.dance_token=None;self.dance_deadline=None;self.phase='idle'
            if b'DANCE_DONE cancelled=0' in line and self.turns<self.max_turns:
                self.idle_since=time.monotonic()
                self.schedule_listen(delay=.65)
                self.report('跳完啦！看到绿灯后可以继续说。' if self.continuous_active else
                            '跳完啦！点一下屏幕可以继续聊天。')
            else:
                self.pause_continuous()
                self.report('舞蹈已停止。点一下屏幕继续聊天。')

    def check_dance_timeout(self):
        if self.dance_deadline is not None and time.monotonic()>self.dance_deadline:
            self.connection_event('dance_timeout')
            self.cancel()
            self.report('舞蹈状态没有及时传回来，已停止。点一下屏幕再说吧。')

    @staticmethod
    def wants_to_stop(text):
        text=re.sub(r'[\s，。！？、,.!?～~]','',text)
        text=re.sub(r'^(你好)?(小克|小橙|机器人)[啊呀]?','',text)
        return text in {'再见','拜拜','不聊了','不想聊了','我不想聊了','停止','停下',
                        '暂停','停止聊天','暂停聊天','不要聊了','我要睡觉了'}

    def connection_event(self, event, **metadata):
        """Connection metadata only: never audio, transcripts or cloud responses."""
        entry = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'event': event,
                 'port': self.port, **metadata}
        try:
            with (Path(__file__).parent / 'connection.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def unavailable(self):
        if self.last_error:
            return self.last_error
        if self.closed.is_set():
            return '连接已结束，请点击“重新连接”。'
        return '正在连接机器人，请稍等（最多约25秒）。'

    def report(self, message, **metadata):
        self.notify(message, metadata)

    def device_event(self, line):
        """Optional diagnostics hook for device metadata, never PCM or transcripts."""

    def screen_frame(self, metadata, data):
        """Optional diagnostics hook for the robot's rendered display, not its camera."""

    def send(self, data):
        with self.write_lock:
            if self.serial and self.serial.is_open:
                self.serial.write(data)

    def cancel(self, reset=False):
        self.control_requests.clear();self.control_active=False;self.control_deadline=None
        self.hourly_requested=False
        self.sleep_preview_until=0.0
        self.claim_alarm()
        self.clear_alarm();self.alarm_requested=False
        self.clear_reminder();self.scene_request=None
        self.stop_wake(cooldown=1.5)
        self.wake_first_record=False
        self.phase='paused'
        self.listen_deadline=None
        self.parent_restart_at=None
        self.parent_expect_cancel=False
        self.pending_dance=None;self.dance_token=None;self.dance_deadline=None
        self.parent_capture = False
        self.pending_parent_ack = None
        self.pause_continuous()
        self.epoch += 1
        self.pending_history = None
        # Drop any photo in flight; a late frame is then ignored by the waiter.
        with self.condition:
            self.photo_waiting = None
            self.photo_pending = False
            self.condition.notify_all()
        if reset:
            self.history.clear()
        if self.state != 'ready' or self.closed.is_set():
            self.report(self.unavailable())
            return
        # Never inject a command byte into a PCM upload.
        if not self.uploading:
            self.send(b'x')
        self.report('已停止。点一下屏幕或“开始聆听”可以继续。')

    def listen(self, wake=False):
        if self.chat_expired:
            self.report('本次聊天时间已到，重新打开可恢复聊天；闹钟仍保留。');return
        if self.state != 'ready' or self.closed.is_set() or self.serial is None or not self.serial.is_open:
            self.report(self.unavailable())
            return
        if self.future and not self.future.done():
            self.report('上一条请求正在结束，请稍等几秒再点。')
        elif self.photo_pending or self.photo_preview or self.dance_token is not None:
            self.report('请等拍照或舞蹈结束，或点屏幕取消后再说。')
        elif self.turns >= self.max_turns:
            self.report('本次20轮已结束，请家长退出后重新打开。')
        elif not self.uploading and self.phase in ('idle','paused'):
            self.phase='arming'
            self.listen_deadline=time.monotonic()+3
            self.stop_wake()
            self.wake_first_record=wake
            self.continuous_active = self.continuous_enabled
            self.idle_since = time.monotonic()
            self.next_listen_at = None
            self.send(b'r')

    def exact(self, length, timeout=20):
        data = bytearray()
        deadline = time.monotonic() + timeout
        while len(data) < length and time.monotonic() < deadline and not self.closed.is_set():
            chunk = self.serial.read(min(length-len(data),8192))
            data.extend(chunk)
        if len(data) != length:
            raise RuntimeError('USB音频传输超时。')
        return bytes(data)

    def run(self):
        stage = 'opening'
        try:
            self.connection_event(stage)
            device = serial.Serial(baudrate=115200, timeout=0.2, write_timeout=3)
            device.port = self.port
            device.dtr = False; device.rts = False
            device.open()
            self.serial = device
            stage = 'identifying'
            self.connection_event(stage)
            identified = False
            deadline = time.monotonic()+20
            while time.monotonic() < deadline and not self.closed.is_set():
                if device.readline().startswith(b'CLAWD '):
                    identified = True; break
            if not identified:
                if self.closed.is_set():
                    return
                raise RuntimeError('没有识别到 Clawd 固件；未发送音频命令。')
            stage = 'handshaking'
            self.connection_event(stage)
            self.send(b'h')
            last_heartbeat = time.monotonic()
            last_hello=last_heartbeat
            ready_deadline=last_heartbeat+5
            while not self.closed.is_set():
                if self.state!='ready' and time.monotonic()>ready_deadline:
                    raise RuntimeError('机器人仍是离线版或连接未完成，请先检查语音固件。')
                if self.state != 'ready' and time.monotonic()-last_hello > 1:
                    self.send(b'h')
                    last_hello=time.monotonic()
                if self.enforce_chat_limit():break
                self.check_dance_timeout()
                self.resume_if_due()
                self.resume_parent_if_due()
                self.service_alarm()
                self.service_controls()
                self.service_daily()
                self.service_scenes()
                self.service_wake()
                if self.state=='ready' and self.task_status!=self.sent_task_status and not self.uploading:
                    pending_status=self.task_status
                    command={'running':b'W','completed':b'D','failed':b'E','cancelled':b'V','idle':b'V'}.get(pending_status,b'V')
                    if self.write_lock.acquire(blocking=False):
                        try:
                            if not self.uploading:
                                self.send(command);self.sent_task_status=pending_status
                        finally:self.write_lock.release()
                if time.monotonic()-last_heartbeat > 2 and self.write_lock.acquire(blocking=False):
                    try:
                        if not self.uploading:
                            self.send(b'k'); last_heartbeat=time.monotonic()
                    finally:
                        self.write_lock.release()
                line = device.readline()
                if line.startswith(b'CLAWD '):
                    self.device_event(line.decode('ascii',errors='replace').strip())
                    if line.startswith(b'CLAWD UI '):
                        self.connection_event('device_ui',details=line.decode('ascii').strip())
                if line.startswith(b'CLAWD_WAKE '):
                    fields=line.split()
                    if len(fields)!=6:raise RuntimeError('USB待机音频头错误。')
                    session,sequence,rate,length,crc=map(int,fields[1:])
                    if rate!=24000 or length!=4800:raise RuntimeError('USB待机音频尺寸错误。')
                    pcm=self.exact(length,3)
                    footer_ok=self.exact(len(b'\nCLAWD_WAKE_END\n'),3)==b'\nCLAWD_WAKE_END\n'
                    crc_ok=binascii.crc32(pcm)==crc
                    if not footer_ok or not crc_ok:
                        self.connection_event('wake_frame_integrity_error',session=session,sequence=sequence,
                                              footer_ok=footer_ok,crc_ok=crc_ok,bytes=length)
                        raise RuntimeError('USB待机音频完整性检查失败。')
                    self.wake_frame(session,sequence,pcm)
                    pcm=None
                elif line.startswith(b'CLAWD WAKE_STARTED '):
                    self.wake_device_active=True
                    session=int(re.search(rb'session=(\d+)',line).group(1))
                    if self.wake_requested and self.wake_eligible():
                        self.wake_session=session
                        self.connection_event('wake_standby',session=session)
                        self.report('本地待机监听：说“你好小克”，听到提示音、亮绿灯后再说。也可点屏幕。')
                elif line.startswith(b'CLAWD WAKE_STOPPED '):
                    session=int(re.search(rb'session=(\d+)',line).group(1))
                    if self.wake_session==session:self.stop_wake()
                elif line.startswith(b'CLAWD WAKE_ERROR'):
                    self.stop_wake(cooldown=2)
                    self.connection_event('wake_device_busy')
                elif line.startswith(b'CLAWD WAKE_IDLE '):
                    self.wake_device_active=False
                    self.control_wake_ready_at=time.monotonic()+.1
                elif line.startswith(b'CLAWD_PCM '):
                    record_epoch=self.epoch
                    self.wake_first_record=False
                    fields = line.split()
                    if len(fields) != 4 or fields[1] != b'24000':
                        raise RuntimeError('USB音频头格式错误。')
                    length, checksum = int(fields[2]), int(fields[3])
                    if length <= 0 or length > 24000*12*2 or length%2:
                        raise RuntimeError('USB音频长度超限。')
                    pcm = self.exact(length)
                    footer = self.exact(len(b'\nCLAWD_PCM_END\n'), 3)
                    if footer != b'\nCLAWD_PCM_END\n' or binascii.crc32(pcm) != checksum:
                        raise RuntimeError('USB音频完整性检查失败。')
                    cancelled=False
                    if self.device_pcm_status:
                        status=self.exact(len(b'CLAWD PCM_STATUS cancelled=0\n'),3)
                        if status not in (b'CLAWD PCM_STATUS cancelled=0\n',b'CLAWD PCM_STATUS cancelled=1\n'):
                            raise RuntimeError('USB录音取消状态错误。')
                        cancelled=status==b'CLAWD PCM_STATUS cancelled=1\n'
                    if cancelled or record_epoch!=self.epoch:
                        pcm=None
                        self.connection_event('record_transfer_discarded',cancelled=cancelled)
                        self.phase='paused';self.pause_continuous()
                        continue
                    self.response_started_at = time.monotonic()
                    if self.future and not self.future.done():
                        self.send(b'x')
                        self.report('上一条请求正在结束，请稍后再说。')
                    elif self.turns < self.max_turns and not self.chat_expired:
                        self.turns += 1
                        token = self.epoch
                        parent=self.parent_capture
                        self.parent_capture=False
                        self.future = self.pool.submit(self.respond_parent if parent else self.respond, pcm, token)
                    else:
                        self.send(b'x')
                        self.report('本次20轮已结束，请家长退出后重新打开。')
                elif line.startswith(b'CLAWD_FRAME '):
                    fields=line.split()
                    if len(fields)!=5:
                        raise RuntimeError('屏幕帧头错误。')
                    width,height,stride,length=map(int,fields[1:])
                    if (width,height,stride,length)!=(320,240,640,153600):
                        raise RuntimeError('屏幕帧尺寸错误。')
                    data=self.exact(length)
                    if self.exact(len(b'\nCLAWD_FRAME_END\n'),3)!=b'\nCLAWD_FRAME_END\n':
                        raise RuntimeError('屏幕帧结束标记错误。')
                    self.screen_frame({'width':width,'height':height,'stride':stride,'bytes':length},data)
                elif line.startswith(b'CLAWD_PHOTO '):
                    fields=line.split()
                    if len(fields)!=5:
                        raise RuntimeError('照片帧头错误。')
                    width,height,length,crc=map(int,fields[1:])
                    if width<=0 or height<=0 or length<=0 or length>camera_module.MAX_JPEG:
                        raise RuntimeError('照片长度超限。')
                    jpeg=self.exact(length)
                    if self.exact(len(b'\nCLAWD_PHOTO_END\n'),3)!=b'\nCLAWD_PHOTO_END\n':
                        raise RuntimeError('照片帧结束标记错误。')
                    if binascii.crc32(jpeg)!=crc:
                        raise RuntimeError('照片完整性检查失败。')
                    with self.condition:
                        if self.photo_waiting is False:
                            self.photo_waiting=jpeg
                            self.photo_pending=False
                            self.condition.notify_all()
                elif line.startswith(b'CLAWD PHOTO_ERROR'):
                    self.connection_event('photo_error',details=line.decode('ascii',errors='replace').strip())
                    with self.condition:
                        self.photo_waiting=None
                        self.photo_pending=False
                        self.condition.notify_all()
                    self.report('这次没拍到照片，小克看不到。')
                elif line.startswith(b'CLAWD PHOTO_PREVIEW '):
                    self.photo_preview=b' shown ' in line
                    if not self.photo_preview:self.wake_retry_at=time.monotonic()+.9
                elif line.startswith(b'CLAWD PHOTO_DONE'):
                    self.connection_event('photo_done',details=line.decode('ascii',errors='replace').strip())
                elif line.startswith(b'CLAWD UPLOAD_READY'):
                    with self.condition:
                        self.upload_ready = True; self.condition.notify_all()
                elif line.startswith(b'CLAWD UPLOAD_ACK '):
                    with self.condition:
                        self.upload_ack = int(line.split()[-1]); self.condition.notify_all()
                elif line.startswith(b'CLAWD DANCE_'):
                    self.dance_event(line)
                elif line.startswith(b'CLAWD SCENE_'):
                    self.scene_event(line)
                elif line.startswith(b'CLAWD CONTROL_'):
                    self.control_event(line)
                elif line.startswith((b'CLAWD CANCELLED', b'CLAWD CHAT_RESET')):
                    self.control_requests.clear();self.control_active=False;self.control_deadline=None
                    self.hourly_requested=False;self.sleep_preview_until=0.0
                    self.claim_alarm()
                    self.clear_alarm()
                    if line.startswith(b'CLAWD CHAT_RESET'):self.alarm_requested=False
                    self.clear_reminder();self.scene_request=None
                    self.pending_dance=None;self.dance_token=None;self.dance_deadline=None
                    if line.strip()==b'CLAWD CANCELLED':
                        if self.parent_expect_cancel:self.parent_expect_cancel=False
                        else:self.parent_restart_at=None
                    if line.startswith(b'CLAWD CHAT_RESET'):self.parent_restart_at=None
                    self.parent_capture = False
                    self.pending_parent_ack = None
                    self.phase = 'paused'
                    self.stop_wake(cooldown=1.5)
                    self.wake_first_record=False
                    self.pause_continuous()
                    self.epoch += 1
                    self.pending_history = None
                    if line.startswith(b'CLAWD CHAT_RESET'):
                        self.history.clear()
                    self.report('已停止；长按会清空本次对话。点一下可重新说。')
                elif line.startswith(b'CLAWD PLAY_DONE'):
                    if self.alarm_play_done(line):continue
                    if self.reminder_token==self.epoch and self.reminder_kind=='hourly':
                        self.clear_reminder();self.phase='idle';self.wake_retry_at=time.monotonic()+.9
                        self.report('整点报时结束。');continue
                    self.wake_retry_at=time.monotonic()+.9
                    if self.reminder_token==self.epoch:
                        self.phase='reminding'
                        self.report('小克正在陪你喝一口水。点屏幕可以开始聊天。')
                        continue
                    completed = False
                    self.phase = 'idle'
                    if b'cancelled=0' in line and self.pending_history:
                        epoch, question, answer = self.pending_history
                        if epoch == self.epoch:
                            self.history.extend([{'role':'user','content':question},
                                                 {'role':'assistant','content':answer}])
                            self.history = self.history[-12:]
                            completed = True
                    if b'cancelled=0' in line and self.pending_parent_ack==self.epoch:
                        completed=True
                    self.pending_parent_ack=None
                    self.pending_history = None
                    if self.dance_after_playback(completed):continue
                    if completed and self.continuous_active and self.turns<self.max_turns:
                        self.idle_since = time.monotonic()
                        self.schedule_listen()
                        self.report(f'马上继续听，直接说下一句即可。已完成 {self.turns}/{self.max_turns} 轮。')
                    else:
                        self.pause_continuous()
                        self.report(f'点一下屏幕再说。已完成 {self.turns}/{self.max_turns} 轮。')
                elif line.startswith(b'CLAWD CHAT_READY'):
                    self.device_controls=b'controls=pose1' in line
                    if self.device_controls:self.control_requests['status']=()
                    self.device_sleep=b'sleep_scene=1' in line;self.sent_sleep=None
                    self.device_scenes=b'idle_scenes=love_drink1' in line
                    self.device_dance=b'dance=crab1' in line
                    self.device_wake=b'wake=pcm1' in line
                    self.device_pcm_status=b'pcm_status=1' in line
                    self.state = 'ready'
                    stage = 'ready'
                    self.connection_event(stage)
                    self.report('已连接。点一下屏幕开始聊天；开启连续聊天后，回答完会自动继续听。')
                elif line.startswith(b'CLAWD READY '):
                    if self.state=='ready':raise RuntimeError('机器人已重启，请等待重新连接。')
                    # Normal boot output can arrive after identification but before CHAT_READY.
                elif line.startswith(b'CLAWD HOST_TIMEOUT'):
                    raise RuntimeError('机器人连接已超时，请等待重新连接。')
                elif line.startswith(b'CLAWD LISTENING'):
                    if self.chat_expired or self.alarm_token is not None:
                        self.claim_alarm()
                        self.epoch+=1;self.clear_alarm();self.alarm_requested=False
                        self.phase='paused';self.pause_continuous();self.stop_wake();self.send(b'x')
                        self.report('起床闹钟已停止。' if not self.chat_expired else '聊天已暂停，重新打开可恢复。')
                        continue
                    if self.reminder_token is not None:self.epoch+=1
                    self.clear_reminder();self.scene_request=None
                    self.stop_wake()
                    self.listen_deadline=None
                    if self.wake_hit_at is not None:
                        self.connection_event('wake_to_listen',
                            detection_to_green_ms=round((time.monotonic()-self.wake_hit_at)*1000))
                        self.wake_hit_at=None
                    self.phase = 'listening'
                    if self.continuous_enabled and not self.continuous_active:
                        self.continuous_active = True
                        self.idle_since = time.monotonic()
                    self.next_listen_at = None
                    self.report('正在听你说。再点一下可取消。')
                elif line.startswith(b'CLAWD RECORD_DONE'):
                    self.phase = 'thinking'
                    self.record_finished_at = time.monotonic()
                    self.connection_event('record_done', details=line.decode('ascii').strip())
                elif line.startswith(b'CLAWD SPEAKING'):
                    self.claim_alarm()
                    self.phase = 'speaking'
                    if self.response_started_at is not None:
                        now=time.monotonic()
                        self.connection_event('response_latency',
                            pcm_received_to_speaking_ms=round((now-self.response_started_at)*1000),
                            record_done_to_speaking_ms=round((now-self.record_finished_at)*1000)
                            if self.record_finished_at else None)
                    self.report('小克正在说话。点一下屏幕可以打断。')
                elif line.startswith(b'CLAWD NO_SPEECH'):
                    self.phase='idle';self.parent_capture=False
                    if self.wake_first_record:
                        self.wake_first_record=False;self.pause_continuous()
                        self.report('没有听到后续话，已恢复本地唤醒待机。没有调用云端。')
                    elif self.continuous_active and self.idle_since is not None and time.monotonic()-self.idle_since<60:
                        self.schedule_listen(cue=False)
                        self.report('还在等你说话，没有调用云端。点屏幕可暂停。')
                    else:
                        was_active=self.continuous_active
                        self.pause_continuous()
                        self.report('一分钟没听到说话，已暂停。点一下屏幕继续。' if was_active else
                                    '刚才没听到话，没有调用云端。点一下再试。')
                elif line.startswith(b'CLAWD AUDIO_ERROR'):
                    self.epoch += 1
                    raise RuntimeError('设备音频传输失败，请退出，等3秒后重新打开。')
        except (OSError, RuntimeError, ValueError, serial.SerialException) as exc:
            self.state = 'error'
            self.last_error = ((str(exc) if isinstance(exc, RuntimeError) else
                               'USB连接断开或被其他程序占用。') + ' 请点击“重新连接”。')
            self.connection_event('error', stage=stage, error_type=type(exc).__name__)
            self.report(self.last_error)
        finally:
            if self.wake_engine:
                self.connection_event('wake_metrics_final',**self.wake_engine.metrics())
                self.wake_engine.reset()
            self.pause_continuous()
            self.closed.set()
            self.epoch += 1
            with self.condition:
                self.condition.notify_all()
            try:
                if not self.uploading and stage in ('handshaking','ready'):
                    self.send(b'q')
            except (OSError, serial.SerialException):
                pass
            if self.serial:
                self.serial.close()
            self.history.clear()
            self.pool.shutdown(wait=False, cancel_futures=True)
            if self.state != 'error':
                self.state = 'closed'
            self.connection_event('closed', previous_stage=stage)

    def current(self, token):
        return not self.closed.is_set() and token == self.epoch

    def wait(self, predicate, timeout=5):
        deadline=time.monotonic()+timeout
        with self.condition:
            while not predicate():
                remaining=deadline-time.monotonic()
                if remaining <= 0 or self.closed.is_set():
                    raise RuntimeError('USB播放握手超时。')
                self.condition.wait(min(remaining,0.2))

    def play(self, pcm, token, reminder=False, alarm=False):
        if not self.current(token):
            return
        with self.write_lock:
            if (not self.current(token) or (reminder and self.reminder_token!=token)
                    or (alarm and self.alarm_token!=token)):return
            self.phase='uploading'
            self.stop_wake()
            self.uploading = True
            try:
                self.upload_ready=False; self.upload_ack=0
                self.send(b'p')
                self.wait(lambda:self.upload_ready)
                self.send(struct.pack('<II',len(pcm),binascii.crc32(pcm)))
                for offset in range(0,len(pcm),2048):
                    chunk=pcm[offset:offset+2048]
                    self.send(chunk)
                    self.wait(lambda:self.upload_ack>=offset+len(chunk))
                # Complete the framed transfer even after cancellation, then stop playback.
                if not self.current(token):
                    self.send(b'x')
            finally:
                self.uploading=False

    def respond(self, pcm, token):
        try:
            self.report('听到了，正在理解……')
            text=self.cloud.recognize(pcm)
            pcm=None
            if not self.current(token): return
            if not text or len(text)>400:
                self.pause_continuous()
                self.phase='idle'
                self.send(b'i'); self.report('这句没听清，已暂停。点一下再说一句吧。'); return
            if self.wants_to_stop(text):
                self.pause_continuous()
            if self.device_dance and self.wants_dance(text):
                answer='好呀，看我的小螃蟹摇摆舞！'
                speech=self.cloud.synthesize(answer)
                if not self.current(token):return
                self.pending_history=(token,text,answer)
                self.pending_dance=token
                self.play(speech,token)
                return
            # Only for questions that ask the robot to look; ordinary chat stays one call.
            sight=self.photo_context(text,list(self.history),token)
            if not self.current(token): return
            self.report('正在想怎么回答……')
            context=camera_module.capability_context(self.camera is not None)
            context+='\n'+('电脑确认小克能跳一段8秒的小螃蟹舞，说跳个舞就能开始，点屏幕可停。' if self.device_dance else '电脑确认当前舞蹈功能未启用，不要声称已经跳舞。')
            context+='\n'+(sight or '本轮未拍照，不要描述当前眼前的内容。')
            answer=self.cloud.reply(text,list(self.history),extra=context)
            if not self.current(token): return
            self.report('正在准备声音……')
            speech=self.cloud.synthesize(answer)
            if not self.current(token): return
            self.pending_history=(token,text,answer)
            self.play(speech,token)
        except (ProviderError, RuntimeError, KeyError, ValueError, OSError, wave.Error) as exc:
            if self.current(token):
                self.pause_continuous()
                self.phase='idle'
                self.send(b'i')
                self.report(str(exc) if isinstance(exc,(ProviderError,RuntimeError)) else '语音服务返回了无法播放的内容。')

    def respond_parent(self, pcm, token):
        try:
            self.report('正在识别家长任务……')
            text=self.cloud.recognize(pcm)
            if not self.current(token):return
            if not text or len(text)>400:raise RuntimeError('家长任务没听清，请重新录一句。')
            self.parent_handler(text)
            self.report('Codex已在后台处理，孩子可以继续聊天。')
            speech=self.cloud.synthesize('收到，电脑开始处理任务啦。现在可以继续聊天。')
            if not self.current(token):return
            self.continuous_active=self.continuous_enabled
            self.pending_parent_ack=token
            self.play(speech,token)
        except (ProviderError,RuntimeError,OSError,ValueError,KeyError,wave.Error) as exc:
            if self.current(token):
                self.phase='idle'
                self.pending_parent_ack=None;self.pause_continuous();self.send(b'i')
                self.report(str(exc) if isinstance(exc,RuntimeError) else '家长任务没有成功录入，请在电脑检查。')

    def close(self):
        self.cancel(reset=True)
        self.closed.set()


def main():
    from panel_design import shell, card, ORANGE, DEEP, BG as BG_SIDE
    parser=argparse.ArgumentParser()
    parser.add_argument('--port')
    parser.add_argument('--provider',choices=['dashscope','deepseek'],default='dashscope')
    parser.add_argument('--model',default='qwen3.8-flash')
    parser.add_argument('--preview-ui',action='store_true',help='Local UI preview without opening USB')
    args=parser.parse_args()
    root=tk.Tk();root.title('小克 Clawd · 家长控制'+(' · 界面预览' if args.preview_ui else ''))
    header,banner,footer,tabs,pages=shell(root)
    chat_page,control_page,routine_page,parent_page=pages
    status=tk.StringVar(value='界面预览 · 未连接设备' if args.preview_ui else '正在连接机器人……')
    ttk.Label(banner,textvariable=status,wraplength=760).pack(anchor='w')
    connection_status=tk.StringVar(value='等待连接')
    # A drawn dot reads as a status light; the old '● / ○' glyphs scaled with the
    # font and clashed with the pixel-art mascot.
    status_row=ttk.Frame(footer)
    status_row.pack(pady=(0,12),anchor='w')
    connection_dot=tk.Canvas(status_row,width=9,height=9,bg=BG_SIDE,highlightthickness=0)
    connection_dot.pack(side='left',padx=(0,6))
    connection_dot_id=connection_dot.create_oval(1,1,8,8,fill='#CFC7BC',outline='')
    ttk.Label(status_row,textvariable=connection_status,foreground=DEEP).pack(side='left')
    events=queue.Queue();parent_events=queue.Queue()
    bridge=None;worker=None;generation=0;reconnect_at=0.0;last_feedback=None
    def parent_notice(state,metadata):
        parent_events.put((state,metadata))
        if bridge:bridge.set_task_status(state)
    tasks=ParentTasks(parent_notice)
    stop_button=ttk.Button(header,text='■ 停止',style='Stop.TButton',command=lambda:bridge and bridge.cancel())
    stop_button.pack(side='right',padx=(12,0))
    chat_columns=ttk.Frame(chat_page);chat_columns.pack(fill='both',expand=True)
    chat_left=ttk.Frame(chat_columns);chat_left.pack(side='left',fill='both',expand=True,padx=(0,14))
    chat_right=ttk.Frame(chat_columns,width=255);chat_right.pack(side='right',fill='y')
    companion=card(chat_left,'和小克聊聊天','说“你好小克”，或轻点机器人屏幕。\n听到叮声、看到绿灯，就可以说话。')
    row=ttk.Frame(companion,style='Inner.TFrame');row.pack(fill='x',pady=(2,12))
    listen_button=ttk.Button(row,text='开始聆听',style='Accent.TButton',command=lambda:bridge and bridge.listen())
    listen_button.pack(side='left',padx=(0,10))
    reset_button=ttk.Button(row,text='清空对话',command=lambda:bridge and bridge.cancel(reset=True))
    reset_button.pack(side='left')
    continuous=tk.BooleanVar(value=True)
    ttk.Checkbutton(companion,text='连续聊天，回答后继续听',variable=continuous,
        command=lambda:bridge and bridge.set_continuous(continuous.get())).pack(anchor='w')
    settings=Path(__file__).parent/'wake-settings.json'
    try:wake_default=json.loads(settings.read_text(encoding='utf-8')).get('enabled',True) is True
    except (OSError,ValueError,AttributeError):wake_default=True
    wake_enabled=tk.BooleanVar(value=wake_default)
    def change_wake():
        if bridge:bridge.set_wake(wake_enabled.get())
        try:settings.write_text(json.dumps({'enabled':wake_enabled.get()})+'\n',encoding='utf-8')
        except OSError:status.set('开关本次已生效，但未能保存到本机。')
    ttk.Checkbutton(companion,text='语音唤醒“你好小克”',variable=wake_enabled,command=change_wake).pack(anchor='w')
    wake_status=tk.StringVar(value='本地唤醒：等待连接')
    ttk.Label(companion,textvariable=wake_status,style='MutedCard.TLabel',wraplength=405).pack(anchor='w',pady=(6,0))
    photo_card=card(chat_left,'看看这个','说“拍张照”或“看看这个”，小克会拍照、显示5秒，再告诉你看到了什么。')
    keep_photos=tk.BooleanVar(value=True)
    ttk.Checkbutton(photo_card,text='拍照后在电脑留一份照片',variable=keep_photos).pack(anchor='w')
    ttk.Label(photo_card,text='识别照片时会发送至百炼；取消勾选后只在内存中处理。',style='MutedCard.TLabel',wraplength=400).pack(anchor='w',pady=5)
    ttk.Label(companion,text='聊天会使用云服务；待机检测在本机，录音不保存。\n静默一分钟自动待机；长按屏幕清空对话。',style='MutedCard.TLabel',wraplength=440,justify='left').pack(anchor='w',pady=(14,0))
    hero=card(chat_right,'试试这些')
    hint=ttk.Frame(hero,style='Inner.TFrame');hint.pack(fill='x',pady=(2,4))
    ttk.Label(hint,text='对小克说一句，或点下面的按钮，\n它会用动作回答你。',
              style='MutedCard.TLabel',wraplength=210,justify='left').pack(anchor='w')
    for title,kind in [('送爱心','love'),('喝水提醒','drink')]:
        ttk.Button(hero,text=title,command=lambda k=kind:bridge and bridge.request_scene(k)).pack(fill='x',pady=5)
    ttk.Button(hero,text='预览睡觉',command=lambda:bridge and bridge.preview_sleep()).pack(fill='x',pady=5)
    ttk.Label(chat_right,text='天气地点 · '+LOCATION,style='Muted.TLabel',wraplength=235).pack(anchor='w',pady=4)
    # Hardware controls: preview values while dragging, submit only on release/key release.
    manual_widgets=[];editing=set()
    yaw_value=tk.DoubleVar(value=0);pitch_value=tk.DoubleVar(value=15);volume_value=tk.DoubleVar(value=100)
    pose_preview=tk.StringVar();volume_preview=tk.StringVar()
    pose_readback=tk.StringVar(value='实际位置 · 等待设备反馈')
    device_volume=tk.StringVar(value='设备音量 · 等待设备反馈')
    control_note=tk.StringVar(value='连接后即可调整。')
    last_audible=[100]
    def show_pose(*_):pose_preview.set(f'目标：左右 {yaw_value.get():.1f}°  /  抬头 {pitch_value.get():.1f}°')
    def show_volume(*_):volume_preview.set(f'{round(volume_value.get())} / 100')
    def apply_pose(*_):
        editing.discard('pose')
        if bridge:bridge.request_control('pose',round(yaw_value.get()*10),round(pitch_value.get()*10))
    def move_step(dy,dp):
        feedback=bridge.control_status if bridge else {}
        yaw_value.set(max(-30,min(30,feedback.get('yaw',round(yaw_value.get()*10))/10+dy)))
        pitch_value.set(max(3,min(45,feedback.get('pitch',round(pitch_value.get()*10))/10+dp)))
        show_pose();apply_pose()
    def home_pose():yaw_value.set(0);pitch_value.set(15);show_pose();apply_pose()
    def apply_volume(*_):
        editing.discard('volume');value=round(volume_value.get())
        if value>0:last_audible[0]=value
        if bridge:bridge.request_control('volume',value)
    def preset_volume(value):volume_value.set(value);show_volume();apply_volume()
    def mute():
        current=round(volume_value.get())
        if current>0:last_audible[0]=current
        preset_volume(last_audible[0] if current==0 else 0)
    controls=ttk.Frame(control_page);controls.pack(fill='x')
    motion=ttk.Frame(controls);motion.pack(side='left',fill='both',expand=True,padx=(0,14))
    sound=ttk.Frame(controls);sound.pack(side='left',fill='both',expand=True)
    move_card=card(motion,'调整小克的方向','轻点方向，或拖动角度滑杆后松手。')
    pad=ttk.Frame(move_card,style='Inner.TFrame');pad.pack(pady=(0,14))
    for title,rr,cc,action in [('抬头 ↑',0,1,lambda:move_step(0,3)),('← 向左',1,0,lambda:move_step(-5,0)),('回正',1,1,home_pose),('向右 →',1,2,lambda:move_step(5,0)),('低头 ↓',2,1,lambda:move_step(0,-3))]:
        button=ttk.Button(pad,text=title,width=8,command=action);button.grid(row=rr,column=cc,padx=3,pady=3);manual_widgets.append(button)
    ttk.Label(move_card,textvariable=pose_readback,style='MutedCard.TLabel',wraplength=400).pack(anchor='w',pady=(0,8))
    for title,var,low,high in [('左右角度  −30° ～ 30°',yaw_value,-30,30),('抬头角度  3° ～ 45°',pitch_value,3,45)]:
        ttk.Label(move_card,text=title,style='Card.TLabel').pack(anchor='w',pady=(7,3))
        slider=ttk.Scale(move_card,from_=low,to=high,variable=var,command=show_pose);slider.pack(fill='x')
        slider.bind('<ButtonPress-1>',lambda _:editing.add('pose'))
        slider.bind('<ButtonRelease-1>',apply_pose);slider.bind('<KeyRelease>',apply_pose);manual_widgets.append(slider)
    ttk.Label(move_card,textvariable=pose_preview,style='MutedCard.TLabel').pack(anchor='w',pady=9)
    refresh=ttk.Button(move_card,text='刷新实际位置',command=lambda:bridge and bridge.request_control('status'))
    refresh.pack(anchor='w');manual_widgets.append(refresh)
    vol_card=card(sound,'声音大小','聊天、报时、提示音和快门都会跟随音量。',subtitle_wrap=270)
    ttk.Label(vol_card,textvariable=volume_preview,style='CardTitle.TLabel').pack(anchor='w',pady=(3,10))
    volume_slider=ttk.Scale(vol_card,from_=0,to=100,variable=volume_value,command=show_volume);volume_slider.pack(fill='x',pady=8)
    volume_slider.bind('<ButtonPress-1>',lambda _:editing.add('volume'))
    volume_slider.bind('<ButtonRelease-1>',apply_volume);volume_slider.bind('<KeyRelease>',apply_volume);manual_widgets.append(volume_slider)
    presets=ttk.Frame(vol_card,style='Inner.TFrame');presets.pack(fill='x',pady=12)
    for title,action in [('静音 / 恢复',mute),('50',lambda:preset_volume(50)),('100',lambda:preset_volume(100))]:
        button=ttk.Button(presets,text=title,width=7,command=action);button.pack(side='left',padx=(0,7));manual_widgets.append(button)
    ttk.Label(vol_card,textvariable=device_volume,style='MutedCard.TLabel').pack(anchor='w',pady=8)
    test_sound=ttk.Button(vol_card,text='测试声音',command=lambda:bridge and bridge.request_hourly());test_sound.pack(anchor='w');manual_widgets.append(test_sound)
    ttk.Label(vol_card,text='音量会保存在机器人上。\n静音时，起床闹铃也不会出声。',style='MutedCard.TLabel',wraplength=310).pack(anchor='w',pady=(16,0))
    ttk.Label(control_page,textvariable=control_note,style='Muted.TLabel',wraplength=760).pack(anchor='w',pady=9)
    ttk.Label(control_page,text='回正：左右 0°、抬头 15°。动作到位后自动放松电机；上方“停止”随时可用。',style='Muted.TLabel',wraplength=760).pack(anchor='w')
    show_pose();show_volume()
    drink_settings=DrinkReminder();drink_enabled=tk.BooleanVar(value=drink_settings.enabled)
    def change_drink():
        target=bridge.drink_reminder if bridge else drink_settings
        try:target.set_enabled(drink_enabled.get())
        except OSError:status.set('喝水提醒开关本次已生效，但未能保存。')
    drink_card=card(routine_page,'记得喝一口水','每天 9:00–20:00，整点、半点提醒；忙时等空闲。')
    ttk.Checkbutton(drink_card,text='开启喝水提醒',variable=drink_enabled,command=change_drink).pack(side='left')
    ttk.Button(drink_card,text='试试喝水提醒',command=lambda:bridge and bridge.request_scene('drink')).pack(side='right')
    alarm_settings=WorkdayAlarm();alarm_enabled=tk.BooleanVar(value=alarm_settings.enabled)
    def change_alarm():
        target=bridge.workday_alarm if bridge else alarm_settings
        try:target.set_enabled(alarm_enabled.get())
        except OSError:status.set('叫醒开关本次已生效，但未能保存。')
    alarm_card=card(routine_page,'早上好，该起床啦','中国法定工作日 6:40，包含调休上班的周末。')
    alarm_row=ttk.Frame(alarm_card,style='Inner.TFrame');alarm_row.pack(fill='x')
    ttk.Checkbutton(alarm_row,text='开启工作日叫醒',variable=alarm_enabled,command=change_alarm).pack(side='left')
    ttk.Button(alarm_row,text='试播叫醒',command=lambda:bridge and bridge.request_alarm()).pack(side='right')
    alarm_status=tk.StringVar(value='叫醒：等待连接')
    ttk.Label(alarm_card,textvariable=alarm_status,style='MutedCard.TLabel',wraplength=710).pack(anchor='w',pady=(5,0))
    daily_settings=DailyRoutines();hourly_enabled=tk.BooleanVar(value=daily_settings.hourly_enabled);sleep_enabled=tk.BooleanVar(value=daily_settings.sleep_enabled)
    def change_daily(kind):
        target=bridge.daily if bridge else daily_settings
        try:target.set_enabled(kind,hourly_enabled.get() if kind=='hourly' else sleep_enabled.get())
        except OSError:status.set('开关本次已生效，但未能保存到本机。')
    daily_card=card(routine_page,'小克的日常节奏','白天轮播敲键盘、爱心、喝水、小睡与三幅名画；夜里安静陪伴。')
    for title,var,kind in [('整点报时，夜间睡觉时静音',hourly_enabled,'hourly'),('23:00 后常驻睡觉，工作日闹铃响起后结束',sleep_enabled,'sleep')]:
        ttk.Checkbutton(daily_card,text=title,variable=var,command=lambda k=kind:change_daily(k)).pack(anchor='w')
    daily_buttons=ttk.Frame(daily_card,style='Inner.TFrame');daily_buttons.pack(anchor='w',pady=6)
    ttk.Button(daily_buttons,text='试播报时',command=lambda:bridge and bridge.request_hourly()).pack(side='left',padx=(0,8))
    ttk.Button(daily_buttons,text='预览睡觉',command=lambda:bridge and bridge.preview_sleep()).pack(side='left')
    daily_status=tk.StringVar(value='作息：等待连接')
    ttk.Label(daily_card,textvariable=daily_status,style='MutedCard.TLabel',wraplength=700).pack(anchor='w',pady=(6,0))
    def connect():
        nonlocal bridge,worker,generation,reconnect_at
        if args.preview_ui:
            status.set('界面预览 · 不连接设备');return
        if worker and worker.is_alive():
            return
        if bridge and bridge.future and not bridge.future.done():
            status.set('上一条请求正在结束，请稍等几秒再重新连接。')
            return
        reconnect_at=time.monotonic()+15
        port=args.port
        if not port:
            candidates=[p.device for p in list_ports.comports() if (p.vid,p.pid)==(0x303a,0x1001)]
            if len(candidates)==1: port=candidates[0]
        if not port:
            status.set('没有找到唯一的机器人USB设备。接好数据线后点击“重新连接”。')
            return
        generation += 1
        attempt=generation
        status.set(f'正在连接机器人（{port}，最多约25秒）……')
        bridge=Bridge(port,lambda message,metadata:events.put((attempt,message)),args.model,args.provider)
        bridge.camera=camera_module.Camera(keep_photos=keep_photos.get())
        bridge.set_continuous(continuous.get())
        bridge.set_wake(wake_enabled.get())
        bridge.drink_reminder.enabled=drink_enabled.get()
        bridge.workday_alarm.enabled=alarm_enabled.get()
        bridge.daily.hourly_enabled=hourly_enabled.get();bridge.daily.sleep_enabled=sleep_enabled.get()
        bridge.parent_handler=tasks.submit
        bridge.set_task_status(tasks.state)
        worker=threading.Thread(target=bridge.run,daemon=True,name='clawd-usb')
        worker.start()
    reconnect_button=ttk.Button(footer,text='重新连接',style='Quiet.TButton',command=connect);reconnect_button.pack(fill='x',pady=4)
    def close():
        tasks.cancel()
        if bridge: bridge.close()
        root.destroy()
    ttk.Button(footer,text='退出',style='Quiet.TButton',command=close).pack(fill='x',pady=4)
    root.protocol('WM_DELETE_WINDOW',close)
    parent_frame=ttk.Frame(parent_page,style='Card.TFrame',padding=20);parent_frame.pack(fill='both',expand=True)
    ttk.Label(parent_frame,text='家长的 Codex 任务',style='CardTitle.TLabel').pack(anchor='w')
    parent_status=tk.StringVar(value='录下一句家长任务，电脑后台整理计划，随后恢复孩子聊天。')
    ttk.Label(parent_frame,textvariable=parent_status,style='MutedCard.TLabel',wraplength=730).pack(anchor='w',pady=(10,5))
    ttk.Label(parent_frame,text='结果保存在电脑，不自动念给孩子；这个入口不改文件、不执行电脑操作。',style='MutedCard.TLabel',wraplength=730).pack(anchor='w')
    parent_row=ttk.Frame(parent_frame,style='Inner.TFrame');parent_row.pack(anchor='w',pady=14)
    parent_button=ttk.Button(parent_row,text='录家长任务',style='Accent.TButton',command=lambda:bridge and bridge.arm_parent());parent_button.pack(side='left',padx=(0,10))
    ttk.Button(parent_row,text='取消家长任务',command=tasks.cancel).pack(side='left')
    result_box=tk.Text(parent_frame,height=16,wrap='word',font=('Microsoft YaHei UI',10),bg='#FAF7F2',fg='#51463F',relief='flat',state='disabled',padx=12,pady=12)
    result_box.pack(fill='both',expand=True)
    def update():
        nonlocal reconnect_at,last_feedback
        while not events.empty():
            attempt,message=events.get_nowait()
            if attempt==generation: status.set(message)
        ready=bridge is not None and bridge.state=='ready' and not bridge.closed.is_set()
        if ready:reconnect_at=time.monotonic()+5
        elif (not args.preview_ui and (alarm_enabled.get() or hourly_enabled.get() or sleep_enabled.get()) and (worker is None or not worker.is_alive())
              and time.monotonic()>=reconnect_at):
            reconnect_at=time.monotonic()+15
            connect()
        manual_ready=ready and bridge.device_controls and not bridge.control_active
        for widget in manual_widgets:widget.configure(state='normal' if manual_ready else 'disabled')
        if bridge:
            feedback=bridge.control_status
            if feedback:
                actual=(feedback.get('yaw',0),feedback.get('pitch',150),feedback.get('volume',100))
                pose_readback.set(f'实际位置  左右 {actual[0]/10:.1f}°   抬头 {actual[1]/10:.1f}°')
                device_volume.set(f'设备音量 {actual[2]} / 100'+(' · 已静音' if actual[2]==0 else ''))
                if actual!=last_feedback:
                    if 'pose' not in editing:yaw_value.set(actual[0]/10);pitch_value.set(actual[1]/10);show_pose()
                    if 'volume' not in editing:volume_value.set(actual[2]);show_volume()
                    last_feedback=actual
            control_note.set('动作执行中，随时可以停止。' if bridge.control_active else
                '设备暂未连接。' if not ready else '固件不支持手动控制。' if not bridge.device_controls else
                '空闲时应用角度与音量；动作结束后电机会自动放松。')
            connection_status.set('已连接' if ready else '等待连接')
            connection_dot.itemconfigure(connection_dot_id,fill=ORANGE if ready else '#CFC7BC')
            daily_status.set('作息：'+('睡觉中，点屏幕仍能聊天' if bridge.daily.sleeping() else '白天待机')+
                ('；整点报时开启' if bridge.daily.hourly_enabled else '；整点报时关闭')+
                ('；叫醒已关闭，睡觉需手动关闭' if bridge.daily.sleep_enabled and not bridge.workday_alarm.enabled else '')+
                ('；固件未支持睡觉动效' if ready and not bridge.device_sleep else ''))
            next_alarm=bridge.workday_alarm.next_alarm()
            alarm_status.set('叫醒：'+('已关闭' if not bridge.workday_alarm.enabled else
                '日历未覆盖下一工作日，请更新年度调休表' if next_alarm is None else
                '下次 '+next_alarm.strftime('%m月%d日 %H:%M')+'（北京时间）')+
                ' · 电脑须开机、未休眠并保持连接；当前日历到2026年。')
            wake_status.set('本地唤醒：'+('已关闭' if not bridge.wake_enabled else
                '等待连接' if not ready else '固件未支持，请使用触屏' if not bridge.device_wake else
                '本次聊天时间已到，闹钟仍保留；重开恢复聊天' if bridge.chat_expired else
                '本次20轮已结束，请退出后重开' if bridge.turns>=bridge.max_turns else
                '正在本机加载模型' if bridge.wake_engine is None else
                '待机监听中 · 无云调用 · 不保存音频' if bridge.wake_session is not None else
                '聊天/操作中暂停，结束后恢复待机'))
        for button in (listen_button,stop_button,reset_button):
            button.configure(state='normal' if ready else 'disabled')
        reconnect_button.configure(state='disabled' if worker and worker.is_alive() else 'normal')
        parent_button.configure(state='normal' if ready and not tasks.busy else 'disabled')
        while not parent_events.empty():
            task_state,metadata=parent_events.get_nowait()
            if task_state=='running':
                parent_status.set('Codex正在后台整理。孩子可以继续聊天，工作动画会在空闲时显示。')
            elif task_state=='completed':
                parent_status.set('家长任务已完成，结果保存在：'+metadata['result'])
                result_box.configure(state='normal');result_box.delete('1.0','end')
                result_box.insert('1.0',metadata['text']);result_box.configure(state='disabled')
            else:parent_status.set(metadata.get('message','家长任务已结束。'))
        root.after(100,update)
    if not args.preview_ui:connect()
    update()
    root.mainloop()


if __name__=='__main__':
    main()
