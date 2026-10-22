from pathlib import Path
from tempfile import TemporaryDirectory
import json
import io
import runpy
import time
import unittest
from unittest.mock import MagicMock, patch
from agent_status import AgentMonitor, LineTail, Snapshot, StatusBook, attention_pcm
from agent_display import AgentDisplay


class BookTests(unittest.TestCase):
    def test_priority_and_running_prevents_completion_dance(self):
        b=StatusBook();b.put('codex','a','running',100);b.put('hermes','b','completed',101)
        self.assertEqual(b.snapshot(102).status,'running')
        b.put('opencode','c','failed',102);self.assertEqual(b.snapshot(103).status,'failed')
        b.put('hermes','d','waiting',103);self.assertEqual(b.snapshot(104).status,'waiting')
        b.put('hermes','d','running',104);self.assertEqual(b.snapshot(105).status,'failed')
    def test_stale_dedup_expiry_and_terminal(self):
        b=StatusBook();b.put('codex','a','waiting',100);key=b.snapshot(101).key
        b.put('codex','a','waiting',102);self.assertEqual(key,b.snapshot(103).key)
        b.put('codex','a','completed',99);self.assertEqual(b.snapshot(103).status,'waiting')
        b.put('codex','a','cancelled',104);self.assertEqual(b.snapshot(105).status,'idle')
        b.put('codex','b','completed',110);self.assertEqual(b.snapshot(231).status,'idle')
        b.put('codex','c','failed',120);self.assertEqual(b.snapshot(241).status,'idle')
        b.put('codex','d','running',300);self.assertEqual(b.snapshot(22000).status,'idle')
    def test_bootstrap_never_replays_old_completion(self):
        m=AgentMonitor();m._put('codex','a','completed',bootstrap=True)
        self.assertEqual(m.snapshot().status,'idle')
        m._put('codex','a','running');self.assertEqual(m.snapshot().status,'running')
    def test_mailbox_rejects_invalid_future_and_oversize(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as temp:
            m=AgentMonitor(events=temp);m.booted=True
            for i,(status,at) in enumerate([('waiting',time.time()),('failed',time.time()+20)]):
                Path(temp,f'{i}.json').write_text(json.dumps(dict(agent='codex',job=str(i),status=status,at=at)))
            Path(temp,'bad.json').write_text('x'*3000)
            m.mailboxes();self.assertEqual(m.snapshot().status,'waiting');self.assertEqual(len(m.book.jobs),1)
    def test_log_tail_partial_and_rotation(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as temp:
            p=Path(temp,'x');p.write_bytes(b'old\n');t=LineTail(p)
            self.assertEqual(t.read(),[])
            with p.open('ab') as f:f.write(b'new')
            self.assertEqual(t.read(),[])
            with p.open('ab') as f:f.write(b'\n')
            self.assertEqual(t.read(),[b'new'])
            p.write_bytes(b'z\n');self.assertEqual(t.read(),[b'z'])
    def test_huge_nonmetadata_line_cannot_grow_tail_forever(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as temp:
            p=Path(temp,'x');p.write_bytes(b'');t=LineTail(p);t.read()
            with p.open('ab') as f:f.write(b'x'*150000)
            t.read();self.assertLessEqual(len(t.partial),128*1024)
            with p.open('ab') as f:f.write(b'xxx\nnext\n')
            self.assertEqual(t.read(),[b'next'])
    def test_codex_hook_reports_metadata_and_never_approves(self):
        with patch('agent_status.emit') as emit:
            main=runpy.run_path(str(Path(__file__).parent/'agent-hook.py'))['main']
            for kind,state in [('UserPromptSubmit','running'),('PermissionRequest','waiting'),
                               ('PostToolUse','running'),('Stop','completed'),('Interrupt','cancelled')]:
                output=io.StringIO()
                with patch('sys.stdin',io.StringIO(json.dumps({'hook_event_name':kind,'session_id':'fixture','tool_input':'SECRET_NOT_STORED'}))),patch('sys.stdout',output):main()
                self.assertEqual(json.loads(output.getvalue()),{})
                emit.assert_called_with('codex','fixture',state)
    def test_hermes_observer_returns_no_approval_decision(self):
        hooks={};context=MagicMock();context.register_hook.side_effect=lambda name,fn:hooks.update({name:fn})
        with patch('agent_status.emit') as emit:
            register=runpy.run_path(str(Path(__file__).parent/'adapters/hermes-status/__init__.py'))['register']
            register(context)
            self.assertEqual(len(hooks),4)
            self.assertIsNone(hooks['pre_approval_request'](session_key='fixture',command='DO_NOT_COPY'))
            emit.assert_called_with('hermes','fixture','waiting')
            self.assertIsNone(hooks['post_approval_response'](session_key='fixture',choice='deny'))
            emit.assert_called_with('hermes','fixture','running')
    def test_hermes_uses_whole_turn_not_tool_error(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root=Path(temp);(root/'logs').mkdir();p=root/'logs'/'agent.log';p.write_text('')
            m=AgentMonitor(hermes=root);m.hermes_logs()
            p.write_text('tui prompt accepted: ui_session=ui1 session_key=masked agent_session_id=sid kind=user\n')
            m.hermes_logs();self.assertEqual(m.snapshot().status,'running')
            with p.open('a') as f:f.write('tool failed\ntui turn finished: ui_session=ui1 session_key=masked agent_session_id=sid status=error duration=3\n')
            m.hermes_logs();self.assertEqual(m.snapshot().status,'failed')
    def test_cue_is_bounded_local_pcm(self):
        self.assertLess(len(attention_pcm()),24000);self.assertEqual(len(attention_pcm())%2,0)


class DisplayTests(unittest.TestCase):
    def test_live_ephemeral_parent_completes_but_restored_result_does_not_replay(self):
        b=MagicMock();d=AgentDisplay(b)
        d.parent_status('completed');self.assertEqual(d.snapshot().status,'idle')
        d.parent_status('running');self.assertEqual(d.snapshot().status,'running')
        d.parent_status('completed');self.assertEqual(d.snapshot().status,'completed')
    def test_task_visual_transition_drains_microphone_before_command(self):
        from bridge import Bridge
        b=Bridge('unused',lambda *_:None);b.state='ready';b.device_agent_status=True
        b.agent_display.status=Snapshot('running','event',1);b.wake_device_active=True
        with patch.object(b,'send') as send,patch.object(b,'stop_wake') as stop:
            self.assertFalse(b.service_task_status());stop.assert_called_once();send.assert_not_called()
            b.wake_device_active=False;b.control_wake_ready_at=0
            self.assertTrue(b.service_task_status());send.assert_called_once_with(b'W')
            send.reset_mock();b.sent_task_status=None;b.dance_token=b.epoch
            self.assertTrue(b.service_task_status());send.assert_not_called()
        b.pool.shutdown()
    def setup_display(self,state='waiting'):
        b=MagicMock();b.task_status='idle';b.scene_idle.return_value=True;b.control_requests={}
        b.scene_request=None;b.hourly_requested=False;b.alarm_requested=False;b.wake_retry_at=0
        b.wake_requested=False;b.wake_device_active=False;b.control_wake_ready_at=0
        b.control_status={'yaw':20,'pitch':150};b.device_dance=True;b.epoch=1
        d=AgentDisplay(b);d.monitor=MagicMock();d.monitor.snapshot.return_value=Snapshot(state,'event1',1)
        return b,d
    def test_child_and_speaker_are_not_interrupted(self):
        b,d=self.setup_display();b.scene_idle.return_value=False;d.service()
        b.request_control.assert_not_called();b.send.assert_not_called()
    def test_stop_mic_before_pose_and_notice_once(self):
        b,d=self.setup_display();b.wake_device_active=True;d.service()
        b.stop_wake.assert_called_once();b.request_control.assert_not_called()
        b.wake_device_active=False;d.service();b.request_control.assert_called_once_with('pose',20,210)
        d.cancel();d.service();self.assertEqual(b.request_control.call_count,1)
    def test_completion_dance_throttled_and_no_child_recording(self):
        b,d=self.setup_display('completed');d.service();b.send.assert_called_once_with(b'B')
        self.assertTrue(d.dance_done());b.schedule_listen.assert_not_called()
        d.service();self.assertEqual(b.send.call_count,1)
        d.monitor.snapshot.return_value=Snapshot('completed','event2',1);d.service()
        self.assertEqual(b.send.call_count,1)
    def test_disable_stops_only_own_action(self):
        b,d=self.setup_display();d.enabled=False;d.service();b.cancel.assert_not_called()
        b,d=self.setup_display();d.dancing=True;d.enabled=False;d.service();b.cancel.assert_called_once()
    def test_waiting_cannot_be_hidden_by_parent_task(self):
        b,d=self.setup_display();b.task_status='running';self.assertEqual(d.snapshot().status,'waiting')
    def test_waiting_resolved_before_idle_has_no_chime(self):
        b,d=self.setup_display();b.scene_idle.return_value=False;d.service()
        d.monitor.snapshot.return_value=Snapshot('running','next',1);b.scene_idle.return_value=True;d.service()
        b.request_control.assert_not_called()

if __name__=='__main__':unittest.main()
