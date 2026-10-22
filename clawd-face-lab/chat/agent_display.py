"""Arbitrate metadata notifications through the existing single USB owner."""
import time
from agent_status import AgentMonitor, Snapshot, StatusBook, attention_pcm


class AgentDisplay:
    def __init__(self, bridge):
        self.bridge=bridge;self.enabled=True;self.monitor=None
        self.handled=set();self.action=None;self.dancing=False;self.last_dance=-100
        self.status=Snapshot();self.original_pose=None
        self.parent_book=StatusBook();self.parent_seen=False

    def parent_status(self,status):
        # Existing planning tasks intentionally use ephemeral Codex sessions.
        # Include their live callback; never replay a restored result on startup.
        if self.parent_seen or status not in ('completed','failed','cancelled'):
            self.parent_book.put('parent','planning',status)
        self.parent_seen=True

    def cancel(self):
        self.action=None;self.dancing=False;self.original_pose=None

    def stop(self):
        if self.monitor:self.monitor.stop()

    def snapshot(self):
        if not self.enabled:return Snapshot()
        result=self.monitor.snapshot() if self.monitor else Snapshot()
        parent=self.parent_book.snapshot()
        priority={'waiting':4,'failed':3,'running':2,'completed':1,'idle':0}
        if priority.get(parent.status,0)>priority.get(result.status,0):return parent
        return result

    def service(self):
        b=self.bridge
        if self.monitor is None:
            if not self.enabled:return
            self.monitor=AgentMonitor();self.monitor.start()
        self.status=self.snapshot()
        if not self.enabled:
            if self.action or self.dancing:b.cancel()
            self.stop();self.monitor=None
            return
        if self.action:
            key,stage=self.action
            if self.status.status!='waiting' or self.status.key!=key:
                self.action=None;return
            if stage=='pose' and not b.control_active and not b.control_requests and b.scene_idle():
                if b.wake_requested or b.wake_device_active:b.stop_wake();return
                if time.monotonic()<b.control_wake_ready_at:return
                self.action=(key,'sound')
                b.reminder_kind='agent';b.reminder_token=b.epoch
                b.reminder_deadline=time.monotonic()+6;b.phase='reminding'
                b.future=b.pool.submit(b.play_reminder,attention_pcm(),b.epoch)
            return
        if (not b.scene_idle() or b.control_requests or b.scene_request or b.hourly_requested
                or b.alarm_requested or time.monotonic()<b.wake_retry_at):return
        status=self.status
        if status.key in self.handled or not status.key:return
        if status.status not in ('waiting','completed'):return
        # Stop/drain microphone frames before using the codec or motors.
        if b.wake_requested or b.wake_device_active:b.stop_wake();return
        if time.monotonic()<b.control_wake_ready_at:return
        if len(self.handled)>256:self.handled.clear()
        self.handled.add(status.key)
        if status.status=='waiting':
            b.connection_event('agent_attention',status='waiting')
            self.action=(status.key,'pose')
            feedback=b.control_status
            self.original_pose=(feedback.get('yaw',0),feedback.get('pitch',150))
            # Same bounded servo path as the existing manual controls.
            b.request_control('pose',feedback.get('yaw',0),min(450,feedback.get('pitch',150)+60))
            b.report('Agent 正在等你处理：小克抬头提醒，请到对应 Agent 窗口批准或回复。')
        elif b.device_dance and time.monotonic()-self.last_dance>=30:
            self.last_dance=time.monotonic();self.dancing=True
            b.dance_token=b.epoch;b.dance_deadline=time.monotonic()+12
            b.phase='dancing';b.send(b'B')
            b.connection_event('agent_completion_dance')

    def play_done(self,line):
        b=self.bridge
        if b.reminder_token!=b.epoch or b.reminder_kind!='agent':return False
        b.clear_reminder();b.phase='idle';self.action=None
        if self.original_pose and self.status.status=='waiting' and not b.control_requests:
            b.request_control('pose',*self.original_pose)
        self.original_pose=None
        b.wake_retry_at=time.monotonic()+.9
        return True

    def dance_done(self):
        if not self.dancing:return False
        self.dancing=False
        self.bridge.wake_retry_at=time.monotonic()+.9
        return True
