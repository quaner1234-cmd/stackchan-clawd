import assert from 'node:assert/strict';
import {mkdtemp, readdir, readFile, unlink, rmdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join, dirname} from 'node:path';
import {XiaokeStatus} from './adapters/opencode-status.js';
const temp=await mkdtemp(join(dirname(fileURLToPath(import.meta.url)),'agent-plugin-test-'));
process.env.XIAOKE_AGENT_EVENTS=temp;
try {
  const plugin=await XiaokeStatus();
  const event=(type,properties)=>plugin.event({event:{type,properties}});
  const state=async()=>JSON.parse(await readFile(join(temp,(await readdir(temp))[0]),'utf8')).status;
  await event('session.status',{sessionID:'test',status:{type:'idle'}});
  assert.equal((await readdir(temp)).length,0,'Old idle sessions must not dance');
  await event('session.status',{sessionID:'test',status:{type:'busy'}});assert.equal(await state(),'running');
  await event('permission.asked',{sessionID:'test',id:'a'});assert.equal(await state(),'waiting');
  await event('permission.asked',{sessionID:'test',id:'b'});
  await event('session.status',{sessionID:'test',status:{type:'busy'}});assert.equal(await state(),'waiting');
  await event('permission.replied',{sessionID:'test',requestID:'a'});assert.equal(await state(),'waiting');
  await event('permission.replied',{sessionID:'test',requestID:'b'});assert.equal(await state(),'running');
  await event('session.error',{sessionID:'test'});assert.equal(await state(),'failed');
  await event('session.status',{sessionID:'test',status:{type:'idle'}});assert.equal(await state(),'failed');
  await event('session.status',{sessionID:'test',status:{type:'busy'}});
  await event('session.status',{sessionID:'test',status:{type:'idle'}});assert.equal(await state(),'completed');
  await event('session.deleted',{info:{id:'test'}});assert.equal(await state(),'idle');
  console.log('PASS OpenCode native event observer (10 assertions; no agent/model calls)');
} finally {
  for (const name of await readdir(temp)) await unlink(join(temp,name));
  await rmdir(temp);
}
