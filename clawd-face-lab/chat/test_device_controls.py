import binascii
import struct
import unittest
from unittest.mock import patch
from bridge import Bridge
from device_controls import packet


class PacketTests(unittest.TestCase):
    def test_wire_crc_and_physical_bounds(self):
        data=packet('pose',(999,-100))
        self.assertEqual(data[:1],b'G');self.assertEqual(len(data),11)
        self.assertEqual(struct.unpack('<BhhB',data[1:7]),(1,300,30,0))
        self.assertEqual(struct.unpack('<I',data[7:])[0],binascii.crc32(data[1:7]))
        self.assertEqual(struct.unpack('<BhhB',packet('volume',(150,))[1:7]),(2,0,0,100))
        self.assertEqual(struct.unpack('<BhhB',packet('volume',(0,))[1:7])[-1],0)

    def test_bad_numbers_never_reach_wire(self):
        for value in (float('nan'),float('inf'),'30',True,None):
            with self.assertRaises(ValueError):packet('volume',(value,))
        with self.assertRaises(ValueError):packet('calibrate')


class BridgeControlTests(unittest.TestCase):
    def setUp(self):
        self.b=Bridge('unused',lambda *_:None);self.addCleanup(self.b.pool.shutdown,False)
        self.b.state='ready';self.b.device_controls=True;self.b.device_scenes=True
        self.b.connection_event=lambda *_a,**_k:None
        self.writes=[];self.b.send=self.writes.append

    def test_waits_for_audio_and_keeps_only_latest_pose(self):
        self.b.phase='speaking';self.b.request_control('pose',10,150);self.b.request_control('pose',20,180)
        self.b.service_controls();self.assertEqual(self.writes,[])
        self.b.phase='idle';self.b.service_controls()
        self.assertEqual(self.writes,[packet('pose',(20,180))]);self.assertTrue(self.b.control_active)
        self.assertFalse(self.b.wake_eligible());self.assertFalse(self.b.scene_idle())
        self.assertEqual(self.b.turns,0);self.assertEqual(self.b.cloud.calls,0)

    def test_no_packet_may_enter_pcm_upload(self):
        self.b.request_control('volume',50);self.b.uploading=True
        self.b.service_controls();self.assertEqual(self.writes,[])
        self.b.uploading=False;self.b.service_controls();self.assertEqual(self.writes,[packet('volume',(50,))])

    def test_waits_for_native_wake_worker_release_without_restarting_mic(self):
        self.b.wake_enabled=True;self.b.device_wake=True
        self.b.wake_requested=True;self.b.wake_device_active=True
        self.b.request_control('pose',0,150)
        self.assertFalse(self.b.wake_eligible())
        self.b.service_controls();self.assertEqual(self.writes,[b'U'])
        self.b.service_controls();self.assertEqual(self.writes,[b'U'])
        self.b.wake_device_active=False;self.b.service_controls()
        self.assertEqual(self.writes[-1],packet('pose',(0,150)))

    def test_feedback_uses_device_values_and_completion_releases_gate(self):
        self.b.request_control('pose',40,200);self.b.service_controls()
        self.b.control_event(b'CLAWD CONTROL_STATUS active=0 yaw=37 pitch=194 volume=50 yaw_torque=0 pitch_torque=0')
        self.assertEqual(self.b.control_status['yaw'],37)
        self.assertTrue(self.b.control_active)
        self.b.control_event(b'CLAWD CONTROL_DONE reason=complete')
        self.assertFalse(self.b.control_active);self.assertEqual(self.b.phase,'idle')

    def test_cancel_invalidates_pending_movement(self):
        self.b.request_control('pose',200,300);self.b.cancel();self.b.service_controls()
        self.assertEqual(self.writes,[b'x']);self.assertEqual(self.b.control_requests,{})

    def test_bad_frame_is_a_reconnect_condition(self):
        with self.assertRaises(RuntimeError):self.b.control_event(b'CLAWD CONTROL_ERROR reason=frame')

    def test_timeout_stops_without_cloud_and_unsupported_firmware_sends_nothing(self):
        self.b.device_controls=False;self.assertFalse(self.b.request_control('volume',50));self.b.service_controls()
        self.assertEqual(self.writes,[])
        self.b.device_controls=True;self.b.request_control('pose',0,150)
        with patch('bridge.time.monotonic',return_value=0):self.b.service_controls()
        with patch('bridge.time.monotonic',return_value=7):self.b.service_controls()
        self.assertFalse(self.b.control_active);self.assertEqual(self.writes[-1],b'x')


if __name__=='__main__':unittest.main()
