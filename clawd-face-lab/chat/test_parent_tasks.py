import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from parent_tasks import ParentTasks,ROOT


class ParentTaskTests(unittest.TestCase):
    def check_result(self,events,expected):
        class Process:
            def __init__(self,*args,**kwargs):
                self.stdin=io.StringIO()
                self.stdout=io.StringIO('\n'.join(json.dumps(e) for e in events))
            def wait(self,timeout=None):return 0
            def poll(self):return 0
            def terminate(self):pass
        notices=[]
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            tasks=ParentTasks(lambda state,data:notices.append((state,data)),folder=temp)
            with patch('parent_tasks.codex_path',return_value='existing-codex.exe'), \
                 patch('parent_tasks.subprocess.Popen',side_effect=Process) as spawn:
                tasks.submit('Synthetic adult planning task')
                tasks.thread.join(5)
            self.assertFalse(tasks.busy)
            self.assertEqual(tasks.state,expected)
            args=spawn.call_args.args[0]
            self.assertIn('read-only',args)
            self.assertNotIn('DASHSCOPE_API_KEY',spawn.call_args.kwargs['env'])
            record=json.loads((Path(temp)/tasks.task_id/'task.json').read_text(encoding='utf-8'))
            self.assertEqual(record['status'],expected)
            if expected=='completed':
                restored=[]
                loaded=ParentTasks(lambda state,data:restored.append((state,data)),folder=temp)
                self.assertEqual(loaded.state,'completed')
                self.assertEqual(restored[-1][1]['text'],'Synthetic plan')
            return notices

    def test_complete_requires_explicit_success_and_final_message(self):
        notices=self.check_result([
            {'type':'item.completed','item':{'type':'agent_message','text':'Synthetic plan'}},
            {'type':'turn.completed','usage':{'output_tokens':3}}],'completed')
        self.assertEqual(notices[-1][1]['text'],'Synthetic plan')

    def test_exit_zero_without_turn_completed_is_failure(self):
        notices=self.check_result([
            {'type':'item.completed','item':{'type':'agent_message','text':'Partial draft'}}],'failed')
        self.assertNotIn('text',notices[-1][1])


if __name__=='__main__':unittest.main()
