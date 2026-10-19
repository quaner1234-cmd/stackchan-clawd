"""Local hourly announcements and a visual bedtime; no network or microphone."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import wave
from idle_scenes import BEIJING

ROOT=Path(__file__).resolve().parent


class DailyRoutines:
    def __init__(self, path=None, now=None):
        self.path=Path(path or ROOT/'daily-settings.json')
        now=(now or datetime.now(BEIJING)).astimezone(BEIJING)
        try:settings=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):settings={}
        if not isinstance(settings,dict):settings={}
        self.hourly_enabled=settings.get('hourly_enabled',True) is True
        self.sleep_enabled=settings.get('sleep_enabled',True) is True
        self.last_hour=settings.get('last_hour','')
        if not isinstance(self.last_hour,str):self.last_hour=''
        # First daytime installation must not unexpectedly put the robot to bed.
        default=(now.date()-timedelta(days=1)).isoformat() if 6*60+45<=now.hour*60+now.minute<23*60 else ''
        self.completed_night=settings.get('completed_night',default)
        if not isinstance(self.completed_night,str):self.completed_night=default

    @staticmethod
    def night(now):
        return (now.date() if now.hour>=23 else now.date()-timedelta(days=1)).isoformat()

    def save(self):
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(hourly_enabled=self.hourly_enabled,
            sleep_enabled=self.sleep_enabled,last_hour=self.last_hour,
            completed_night=self.completed_night))+'\n',encoding='utf-8')
        temp.replace(self.path)

    def set_enabled(self, kind, enabled):
        if kind not in ('hourly','sleep'):raise ValueError('Unknown routine')
        setattr(self,kind+'_enabled',bool(enabled));self.save()

    def sleeping(self, now=None):
        now=(now or datetime.now(BEIJING)).astimezone(BEIJING)
        return self.sleep_enabled and self.night(now)>self.completed_night

    def alarm_started(self, now=None):
        now=(now or datetime.now(BEIJING)).astimezone(BEIJING)
        self.completed_night=max(self.completed_night,self.night(now));self.save()

    def hourly_due(self, now=None):
        now=(now or datetime.now(BEIJING)).astimezone(BEIJING)
        if not self.hourly_enabled or self.sleeping(now) or now.minute>=3:return None
        slot=now.replace(minute=0,second=0,microsecond=0).isoformat()
        return slot if slot>self.last_hour else None

    def claim_hour(self, slot):
        self.last_hour=slot;self.save()


def hour_phrase(hour):
    if hour not in range(24):raise ValueError('Invalid hour')
    names=['零','一','两','三','四','五','六','七','八','九','十','十一','十二',
           '十三','十四','十五','十六','十七','十八','十九','二十','二十一','二十二','二十三']
    return '小克报时，现在是'+names[hour]+'点整。'


def hourly_pcm(hour):
    if hour not in range(24):raise ValueError('Invalid hour')
    with wave.open(str(ROOT/f'assets/hourly/{hour:02d}.wav'),'rb') as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,24000):raise ValueError('Hourly PCM format')
        pcm=wav.readframes(24000*10+1)
        if not pcm or len(pcm)>24000*10*2:raise ValueError('Hourly PCM duration')
        return pcm
