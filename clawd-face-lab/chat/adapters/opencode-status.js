// Auto-loaded native OpenCode observer. No approvals or tool policy changes.
import {mkdir, writeFile, rename} from 'node:fs/promises';
import {createHash, randomUUID} from 'node:crypto';
import {join} from 'node:path';

export const XiaokeStatus = async () => {
  const directory = process.env.XIAOKE_AGENT_EVENTS || 'D:/Projects/StackChan-prep/clawd-face-lab/chat/agent-events';
  const failed = new Set();
  const running = new Set(), waiting = new Map();
  async function emit(id, status) {
    if (!id) return;
    const job = createHash('sha256').update(id).digest('hex').slice(0,24);
    await mkdir(directory,{recursive:true});
    const dest=join(directory,`opencode-${job}.json`), temp=dest+`.${randomUUID()}.tmp`;
    await writeFile(temp,JSON.stringify({agent:'opencode',job,status,at:Date.now()/1000}));
    await rename(temp,dest);
  }
  return {event:async ({event}) => {
    try {
      const p=event.properties || {}, id=p.sessionID || p.session?.id || p.info?.id;
      if (event.type==='permission.asked') {
        const pending=waiting.get(id) || new Set();pending.add(p.id);waiting.set(id,pending);
        running.add(id);await emit(id,'waiting');
      } else if (event.type==='permission.replied') {
        const pending=waiting.get(id);pending?.delete(p.requestID || p.id);
        if (!pending?.size) {waiting.delete(id);await emit(id,'running');}
      }
      else if (event.type==='session.error') {failed.add(id);await emit(id,'failed');}
      else if (event.type==='session.status') {
        const status=p.status?.type;
        if (status==='busy' || status==='retry') {
          running.add(id);failed.delete(id);if (!waiting.has(id)) await emit(id,'running');
        } else if (status==='idle' && running.has(id)) {
          running.delete(id);waiting.delete(id);
          if (!failed.has(id)) await emit(id,'completed');
        }
      } else if (event.type==='session.deleted') {
        failed.delete(id);running.delete(id);waiting.delete(id);await emit(id,'idle');
      }
    } catch {} // Pure observer; filesystem trouble cannot fail a task.
  }};
};
