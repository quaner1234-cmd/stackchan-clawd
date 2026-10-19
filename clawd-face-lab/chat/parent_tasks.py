"""One bounded planning task through the existing signed-in Codex CLI.

The shared job executor pins a removed CLI path. Keep this project adapter small;
do not modify shared tools or grant filesystem/connector access to spoken tasks.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parent


def codex_path():
    found=shutil.which('codex.exe')
    if found:return found
    folder=Path(os.environ['LOCALAPPDATA'])/'OpenAI/Codex/bin'
    candidates=list(folder.glob('*/codex.exe'))
    if not candidates:raise RuntimeError('没有找到本机 Codex，请先安装并登录 Codex。')
    return str(max(candidates,key=lambda path:path.stat().st_mtime))


class ParentTasks:
    def __init__(self, notify, folder=None):
        self.notify=notify
        self.folder=Path(folder or ROOT/'parent-tasks')
        self.state='idle'
        self.process=None
        self.thread=None
        self.cancelled=threading.Event()
        self.task_id=None
        self.result_path=None
        # Restore only this adapter's completed result; never restart interrupted work.
        completed=[]
        for path in self.folder.glob('*/task.json'):
            if not re.fullmatch(r'[0-9a-f]{32}',path.parent.name):continue
            try:
                record=json.loads(path.read_text(encoding='utf-8'))
                result=path.parent/'result.md'
                if (record.get('status')=='completed' and result.is_file()
                        and result.resolve().is_relative_to(self.folder.resolve())
                        and result.stat().st_size<=16384):
                    completed.append((record.get('finished_at',''),record,result))
            except (OSError,ValueError):continue
        if completed:
            _,record,result=max(completed,key=lambda item:item[0])
            self.state='completed';self.task_id=record['task_id'];self.result_path=result
            self.notify('completed',{'task_id':self.task_id,'result':str(result),
                                     'text':result.read_text(encoding='utf-8')})

    @property
    def busy(self):return bool(self.thread and self.thread.is_alive())

    def submit(self,prompt):
        if self.busy:raise RuntimeError('上一条家长任务还在工作，请等完成后再录入。')
        if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=1200:
            raise RuntimeError('家长任务为空或过长，请简短描述。')
        if re.search(r'\b(sk-[A-Za-z0-9_-]{15,}|Bearer\s+\S+)',prompt):
            raise RuntimeError('请勿在任务里输入凭据。')
        self.cancelled.clear()
        self.task_id=uuid.uuid4().hex
        self.state='running'
        self.result_path=None
        self.thread=threading.Thread(target=self._run,args=(prompt.strip(),self.task_id),daemon=True)
        self.notify('running',{'task_id':self.task_id})
        self.thread.start()
        return self.task_id

    def _run(self,prompt,task_id):
        try:self._execute(prompt,task_id)
        except Exception:
            self.state='failed'
            if self.process and self.process.poll() is None:self.process.terminate()
            self.notify('failed',{'task_id':task_id,'message':'家长任务或本机记录写入失败，未自动重试。'})

    def _execute(self,prompt,task_id):
        folder=self.folder/task_id
        folder.mkdir(parents=True,exist_ok=False)
        record={'task_id':task_id,'status':'running','kind':'parent_planning',
                'sandbox':'read-only','started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'prompt':prompt,'automatic_retry':False}
        (folder/'task.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        messages=[]
        completed=False
        size=0
        timer=None
        try:
            args=[codex_path(),'--no-daemon','--ask-for-approval','never','exec',
                  '--ignore-user-config','--sandbox','read-only','--skip-git-repo-check','--ephemeral',
                  '-C',str(ROOT.parent),'--color','never','--json']
            for feature in ['apps','browser_use','browser_use_external','computer_use','plugins',
                            'remote_plugin','skill_search','multi_agent']:
                args+=['--disable',feature]
            args+=['-']
            allowed={'SYSTEMROOT','WINDIR','USERPROFILE','LOCALAPPDATA','APPDATA','TEMP','TMP','PATH',
                     'HOMEDRIVE','HOMEPATH','COMSPEC','PATHEXT','HOME','CODEX_HOME','LANG',
                     'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY'}
            env={key:value for key,value in os.environ.items() if key.upper() in allowed}
            env['NO_COLOR']='1'
            framed=('这是家长主动录入的计划/思路整理任务，与孩子聊天完全独立。'
                    '只基于下面的信息用中文提供实用建议。不要读写本机文件，不调用工具、连接器或其他Agent，'
                    '不要执行代码、发送信息或做购买。不能实际执行的事情请明确说明。'
                    '不要声称已经修改文件或完成外部操作。最多500个汉字，末尾给一句完成摘要。\n家长任务：\n'+prompt)
            self.process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,cwd=ROOT.parent,env=env,text=True,encoding='utf-8',
                errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            timer=threading.Timer(600,self.process.terminate);timer.daemon=True;timer.start()
            if self.cancelled.is_set():self.process.terminate()
            else:
                self.process.stdin.write(framed);self.process.stdin.close()
            for line in self.process.stdout:
                size+=len(line)
                if size>256*1024:
                    self.process.terminate();raise RuntimeError('任务输出超过上限，已停止。')
                try:event=json.loads(line)
                except ValueError:continue
                if event.get('type')=='thread.started':record['thread_id']=event.get('thread_id')
                if event.get('type')=='item.completed':
                    item=event.get('item',{})
                    if item.get('type')=='agent_message':messages.append(item.get('text',''))
                if event.get('type')=='turn.completed':
                    completed=True;record['usage']=event.get('usage')
                if event.get('type') in ('turn.failed','error'):record['upstream_failed']=True
            code=self.process.wait(timeout=5)
            record['exit_code']=code
            if self.cancelled.is_set():raise RuntimeError('家长任务已取消。')
            if code!=0 or not completed or record.get('upstream_failed') or not messages:
                raise RuntimeError('Codex没有完成任务；请检查登录、额度或权限。未自动重试。')
            result='\n\n'.join(messages).strip()
            if re.search(r'\b(sk-[A-Za-z0-9_-]{15,}|Bearer\s+\S+)',result):
                raise RuntimeError('结果疑似含凭据，已隐藏输出。')
            self.result_path=folder/'result.md'
            self.result_path.write_text(result,encoding='utf-8')
            self.state='completed';record['status']=self.state
            self.notify(self.state,{'task_id':task_id,'result':str(self.result_path),'text':result})
        except (OSError,RuntimeError,subprocess.SubprocessError) as exc:
            self.state='cancelled' if self.cancelled.is_set() else 'failed'
            record['status']=self.state;record['error_type']=type(exc).__name__
            self.notify(self.state,{'task_id':task_id,'message':str(exc) if isinstance(exc,RuntimeError)
                                   else 'Codex任务启动或运行失败，请在电脑检查。'})
        finally:
            if timer:timer.cancel()
            record['finished_at']=time.strftime('%Y-%m-%dT%H:%M:%S%z')
            (folder/'task.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            self.process=None

    def cancel(self):
        self.cancelled.set()
        if self.process and self.process.poll() is None:self.process.terminate()


if __name__=='__main__':
    done=threading.Event()
    def report(state,metadata):
        print(json.dumps({'state':state,**metadata},ensure_ascii=False),flush=True)
        if state!='running':done.set()
    task=ParentTasks(report)
    task.submit('给3岁9个月孩子制定30分钟雨天室内亲子游戏计划，3项活动，每项一句话。')
    assert done.wait(120),'Planning test timed out'
    task.thread.join(2)
    assert task.state=='completed','Planning test did not complete'
