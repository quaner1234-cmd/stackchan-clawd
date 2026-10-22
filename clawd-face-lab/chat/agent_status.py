"""Local, metadata-only agent status. No prompts, tool arguments or credentials.

Observers never authorize an operation. The USB Bridge remains the only actuator.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import threading
import time

ROOT = Path(__file__).resolve().parent
EVENTS = ROOT / 'agent-events'
SETTINGS = ROOT / 'agent-status-settings.json'
VALID = {'running', 'waiting', 'completed', 'failed', 'cancelled', 'idle'}


def load_enabled():
    try:return json.loads(SETTINGS.read_text(encoding='utf-8')).get('enabled', True) is True
    except (OSError, ValueError):return True


def save_enabled(enabled):
    temporary=SETTINGS.with_suffix('.tmp')
    temporary.write_text(json.dumps({'enabled':bool(enabled)})+'\n',encoding='utf-8')
    temporary.replace(SETTINGS)


def emit(agent, job, status):
    """Atomic last-value mailbox per job; safe for concurrent agent processes."""
    if agent not in {'codex','hermes','opencode'} or status not in VALID:return
    job=hashlib.sha256(str(job).encode()).hexdigest()[:24]
    EVENTS.mkdir(exist_ok=True)
    path=EVENTS/(agent+'-'+job+'.json')
    temp=path.with_suffix('.'+str(os.getpid())+'.tmp')
    temp.write_text(json.dumps(dict(agent=agent,job=job,status=status,at=time.time())),encoding='utf-8')
    os.replace(temp,path)


@dataclass(frozen=True)
class Snapshot:
    status: str = 'idle'
    key: str = ''
    count: int = 0


class StatusBook:
    """Priority and expiry are independent of any particular agent's SDK."""
    def __init__(self):self.jobs={}

    def put(self, agent, job, status, at=None):
        if status not in VALID:return
        at=time.time() if at is None else float(at)
        key=(agent,str(job));old=self.jobs.get(key)
        if old and old[1]>at:return
        # Repeated busy heartbeats must not generate a new attention event.
        if old and old[0]==status:
            self.jobs[key]=(status,at,old[2]);return
        self.jobs[key]=(status,at,f'{agent}:{job}:{at:.6f}:{status}')

    def snapshot(self, now=None):
        now=time.time() if now is None else now
        self.jobs={k:v for k,v in self.jobs.items() if 0<=now-v[1]<21600}
        for status,seconds in [('waiting',21600),('failed',120),('running',21600),('completed',120)]:
            candidates=[v for v in self.jobs.values() if v[0]==status and now-v[1]<seconds]
            if candidates:
                latest=max(candidates,key=lambda v:v[1])
                return Snapshot(status,latest[2],len(candidates))
        return Snapshot()


class LineTail:
    def __init__(self, path):self.path=Path(path);self.offset=None;self.partial=b'';self.discarding=False
    def read(self, bootstrap=0):
        try:
            size=self.path.stat().st_size
            if self.offset is None:
                self.offset=max(0,size-bootstrap)
                skip=self.offset>0
            else:skip=False
            if size<self.offset:self.offset=0;self.partial=b'';self.discarding=False
            with self.path.open('rb') as f:
                f.seek(self.offset);data=f.read(2*1024*1024);self.offset=f.tell()
            lines=(self.partial+data).split(b'\n');self.partial=lines.pop()
            if (skip or self.discarding) and lines:
                lines.pop(0);self.discarding=False
            if len(self.partial)>128*1024:
                self.partial=b'';self.discarding=True
            return lines
        except OSError:return []


class AgentMonitor:
    """One slow local observer; no extra server, agent processes or model calls.

    Native hooks carry approvals. Version-specific read-only lifecycle fallbacks
    keep Codex/Hermes start/finish visible without editing their core sources.
    Historical terminal events are never celebrated on startup.
    """
    def __init__(self, home=None, hermes=None, events=None):
        self.home=Path(home or Path.home()/'.codex')
        self.hermes=Path(hermes or Path(os.environ.get('LOCALAPPDATA',''))/'hermes')
        self.events=Path(events or EVENTS)
        self.book=StatusBook();self.lock=threading.Lock();self.closed=threading.Event()
        self.thread=None;self.tails={};self.seen={};self.next_discovery=0
        self.hermes_tail=LineTail(self.hermes/'logs'/'agent.log')
        self.hermes_runs={};self.hermes_alias={};self.booted=False;self.error=None

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='xiaoke-agent-status');self.thread.start()

    def stop(self):self.closed.set()

    def snapshot(self):
        with self.lock:return self.book.snapshot()

    def _put(self,agent,job,status,at=None,bootstrap=False):
        if bootstrap and status in ('completed','failed','cancelled'):status='idle'
        with self.lock:self.book.put(agent,job,status,at)

    def codex(self):
        if time.monotonic()>=self.next_discovery:
            self.next_discovery=time.monotonic()+4
            db=self.home/'state_5.sqlite'
            if db.exists():
                with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.2) as conn:
                    rows=conn.execute('select id,rollout_path from threads where archived=0 order by updated_at desc limit 24').fetchall()
                for sid,path in rows:
                    if path and sid not in self.tails:self.tails[sid]=LineTail(path)
                self.tails={k:v for k,v in self.tails.items() if k in {r[0] for r in rows}}
        for sid,tail in self.tails.items():
            initial=tail.offset is None
            for raw in tail.read(bootstrap=2*1024*1024):
                if b'"event_msg"' not in raw:continue
                try:
                    event=json.loads(raw);p=event.get('payload',{});kind=p.get('type')
                    state={'task_started':'running','task_complete':'completed','turn_aborted':'cancelled'}.get(kind)
                    if state:
                        from datetime import datetime
                        at=datetime.fromisoformat(event['timestamp'].replace('Z','+00:00')).timestamp()
                        self._put('codex',sid,state,at,bootstrap=initial)
                except (ValueError,TypeError,KeyError):continue

    def hermes_logs(self):
        import re
        initial=self.hermes_tail.offset is None
        for raw in self.hermes_tail.read(bootstrap=256*1024):
            # Filter before decoding: never parse/store chat or arbitrary errors.
            if b'tui prompt accepted:' not in raw and b'tui turn finished:' not in raw:continue
            m=re.search(rb'tui (prompt accepted|turn finished): ui_session=(\S+) session_key=\S+ agent_session_id=(\S+)(.*)',raw)
            if not m:continue
            kind,sid,alias,rest=m.groups();sid=sid.decode();alias=alias.decode()
            self.hermes_alias[hashlib.sha256(alias.encode()).hexdigest()[:24]]=sid
            if kind==b'prompt accepted':
                self.hermes_runs[sid]=True
                self._put('hermes',sid,'running',bootstrap=initial)
            else:
                status=re.search(rb'\bstatus=(\w+)',rest)
                if status:
                    state={b'complete':'completed',b'interrupted':'cancelled',b'error':'failed'}.get(status[1])
                    if state:
                        self._put('hermes',sid,state,bootstrap=initial)
                        self.hermes_runs.pop(sid,None)

    def mailboxes(self):
        try:paths=list(self.events.glob('*.json'))[-512:]
        except OSError:return
        for path in paths:
            try:
                stamp=path.stat().st_mtime_ns
                if self.seen.get(path)==stamp:continue
                self.seen[path]=stamp
                if path.stat().st_size>2048:continue
                p=json.loads(path.read_text(encoding='utf-8'))
                agent=p['agent'];job=p['job'];at=float(p['at'])
                if agent not in ('codex','hermes','opencode') or not 0<=time.time()-at<21600:continue
                if agent=='hermes':job=self.hermes_alias.get(job,job)
                # Codex hooks hash their session ID; unify with transcript records.
                if agent=='codex':
                    job=next((sid for sid in self.tails if hashlib.sha256(sid.encode()).hexdigest()[:24]==job),job)
                self._put(agent,job,p['status'],at,bootstrap=not self.booted)
            except (OSError,ValueError,KeyError,TypeError):continue
        self.seen={k:v for k,v in self.seen.items() if k in paths}

    def poll(self):
        self.codex();self.hermes_logs();self.mailboxes();self.booted=True

    def run(self):
        while not self.closed.is_set():
            try:self.poll();self.error=None
            except (OSError,sqlite3.Error,ValueError):self.error='本地状态源暂不可用，正在重试'
            self.closed.wait(.75)


def attention_pcm():
    """Soft two-note local cue, 24 kHz signed mono, no cloud/TTS."""
    output=bytearray()
    for frequency in (660,880):
        for i in range(3360):
            gain=min(1,i/240,(3359-i)/480)
            output.extend(struct.pack('<h',int(4500*gain*math.sin(2*math.pi*frequency*i/24000))))
        output.extend(bytes(1440))
    return bytes(output)
