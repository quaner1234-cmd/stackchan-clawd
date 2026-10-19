"""Exercise the actual GUI update callback without native windows or USB readers."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import MagicMock, patch
import bridge as app
import panel_design as design
from workday_alarm import WorkdayAlarm


class Value:
    def __init__(self,value=None):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value


class FakeWorker:
    def __init__(self,*_a,**_kw):self.alive=False
    def start(self):self.alive=True
    def is_alive(self):return self.alive


class ReconnectTests(unittest.TestCase):
    def test_alarm_mode_recovers_one_connection_without_overlapping_readers(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=MagicMock();callbacks=[];workers=[];instances=[];variables=[]
            root.after.side_effect=lambda _,callback:callbacks.append(callback)
            def worker(*args,**kwargs):
                item=FakeWorker();workers.append(item);return item
            def variable(value=None):
                item=Value(value);variables.append(item);return item
            def make_bridge(*_a,**_kw):
                item=MagicMock();item.state='ready';item.future=None
                item.closed=threading.Event();item.wake_enabled=False;item.chat_expired=False
                item.workday_alarm=WorkdayAlarm(Path(tmp)/'alarm.json')
                item.device_controls=False;item.control_status={};item.control_active=False
                instances.append(item);return item
            with patch.object(app.tk,'Tk',return_value=root),patch.object(app.tk,'StringVar',side_effect=Value),\
                 patch.object(app.tk,'BooleanVar',side_effect=variable),patch.object(app.tk,'Text'),\
                 patch.object(app.tk,'DoubleVar',side_effect=Value),patch.object(app.tk,'Canvas'),\
                 patch.object(design,'ttk'),\
                 patch.object(app,'ttk'),patch.object(app,'Bridge',side_effect=make_bridge),\
                 patch.object(app,'ParentTasks'),patch.object(app.threading,'Thread',side_effect=worker),\
                 patch('sys.argv',['bridge.py','--port','COM5']),patch.object(app.time,'monotonic',return_value=0) as clock:
                app.main()
                self.assertEqual(len(instances),1)
                update=callbacks[-1]
                instances[0].state='error';instances[0].closed.set()
                clock.return_value=6;update()
                self.assertEqual(len(instances),1,'Running old reader must not overlap')
                workers[0].alive=False;update()
                self.assertEqual(len(instances),2)
                update();self.assertEqual(len(instances),2,'Only one new reader may start')
                instances[1].state='error';instances[1].closed.set();workers[1].alive=False
                for variable in variables[-3:]:variable.set(False)  # alarm, hourly and bedtime local services
                clock.return_value=99;update()
                self.assertEqual(len(instances),2,'Disabling all local services restores manual reconnect behavior')


    def test_preview_never_opens_usb_even_after_autoreconnect_timer(self):
        root=MagicMock();callbacks=[]
        root.after.side_effect=lambda _,callback:callbacks.append(callback)
        with patch.object(app.tk,'Tk',return_value=root),patch.object(app.tk,'StringVar',side_effect=Value),\
             patch.object(app.tk,'BooleanVar',side_effect=Value),patch.object(app.tk,'DoubleVar',side_effect=Value),\
             patch.object(app.tk,'Text'),patch.object(app.tk,'Canvas'),patch.object(design,'ttk'),\
             patch.object(app,'ttk'),patch.object(app,'Bridge') as factory,patch.object(app,'ParentTasks'),\
             patch('sys.argv',['bridge.py','--preview-ui']),patch.object(app.time,'monotonic',return_value=99999):
            app.main()
            for _ in range(3):callbacks[-1]()
            factory.assert_not_called()


if __name__=='__main__':unittest.main()
