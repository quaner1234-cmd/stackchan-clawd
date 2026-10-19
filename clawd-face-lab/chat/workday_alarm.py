"""China mainland adjusted workday alarm. Fixed local audio, no runtime network."""
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path
import wave
from idle_scenes import BEIJING

ROOT=Path(__file__).resolve().parent
PHRASE='早上好，现在六点四十分啦。该起床啦，小克陪你开始新的一天！'


class ChinaWorkdays:
    def __init__(self, path=None):
        self.years={}
        try:
            data=json.loads(Path(path or ROOT/'calendars/china-workdays.json').read_text(encoding='utf-8'))
            for year,info in data['years'].items():
                holidays=set();year=int(year)
                for first,last in info['holiday_ranges']:
                    first,last=date.fromisoformat(first),date.fromisoformat(last)
                    if first.year!=year or last.year!=year or not 0<=(last-first).days<20:raise ValueError('Invalid range')
                    holidays.update(first+timedelta(days=i) for i in range((last-first).days+1))
                makeup={date.fromisoformat(x) for x in info['makeup_workdays']}
                if any(x.year!=year for x in makeup) or holidays&makeup:raise ValueError('Invalid makeup dates')
                self.years[year]=(holidays,makeup)
        except (OSError,ValueError,TypeError,KeyError,AttributeError):self.years={}

    def is_workday(self, day):
        if day.year not in self.years:return None
        holidays,makeup=self.years[day.year]
        return day in makeup or (day.weekday()<5 and day not in holidays)


class WorkdayAlarm:
    def __init__(self, path=None, calendar=None):
        self.path=Path(path or ROOT/'alarm-settings.json')
        self.calendar=calendar if calendar is not None else ChinaWorkdays()
        try:settings=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):settings={}
        if not isinstance(settings,dict):settings={}
        self.enabled=settings.get('enabled',True) is True
        self.last=settings.get('last_date','')
        if not isinstance(self.last,str):self.last=''
        self.pending=None

    def save(self):
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps({'enabled':self.enabled,'last_date':self.last})+'\n',encoding='utf-8')
        temp.replace(self.path)

    def set_enabled(self, enabled):
        self.enabled=bool(enabled)
        if not self.enabled:self.pending=None
        self.save()

    def due(self, now=None):
        now=(now or datetime.now(BEIJING)).astimezone(BEIJING)
        if not self.enabled or self.calendar.is_workday(now.date()) is not True:
            self.pending=None;return None
        if self.pending and not 0<=(now-self.pending).total_seconds()<300:self.pending=None
        target=now.replace(hour=6,minute=40,second=0,microsecond=0)
        if 0<=(now-target).total_seconds()<300 and now.date().isoformat()>self.last:
            self.pending=target
        return self.pending.date().isoformat() if self.pending else None

    def claim(self, day):
        if day is not None:
            self.last=day;self.pending=None;self.save()

    def next_alarm(self, now=None):
        now=(now or datetime.now(BEIJING)).astimezone(BEIJING)
        if not self.enabled:return None
        for offset in range(370):
            day=now.date()+timedelta(days=offset)
            if self.calendar.is_workday(day) is None:return None
            target=datetime.combine(day,time(6,40),tzinfo=BEIJING)
            if target>=now and day.isoformat()>self.last and self.calendar.is_workday(day):return target
        return None


def alarm_pcm():
    with wave.open(str(ROOT/'assets/workday-alarm.wav'),'rb') as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,24000):raise ValueError('Alarm format')
        pcm=wav.readframes(24000*12+1)
        if not pcm or len(pcm)>24000*12*2:raise ValueError('Alarm duration')
        return pcm
