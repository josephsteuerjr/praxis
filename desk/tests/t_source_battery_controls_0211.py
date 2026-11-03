"""Bounded owner-control observation without per-file Known Folder/JSON storms."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys,tempfile,threading,unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_update_parallel as parallel

class BatteryControls(unittest.TestCase):
    def test_pause_is_seen_at_the_next_bounded_poll_and_cannot_revive_this_attempt(self):
        now=[100.0];pause=[False];abort=threading.Event()
        with mock.patch.object(parallel.time,'monotonic',side_effect=lambda:now[0]):
            observer=parallel._cancellation(lambda:pause[0],abort)
            self.assertFalse(observer());pause[0]=True
            now[0]+=.021
            self.assertTrue(observer());pause[0]=False
            now[0]+=1
            self.assertTrue(observer());self.assertTrue(abort.is_set())
    def test_multiple_slots_share_one_durable_control_read_within_the_poll_interval(self):
        read=mock.Mock(return_value=False)
        with mock.patch.object(parallel.time,'monotonic',return_value=100.0):
            observer=parallel._cancellation(read,threading.Event())
            with ThreadPoolExecutor(max_workers=12) as pool:
                self.assertFalse(any(pool.map(lambda _:observer(),range(12))))
            read.assert_called_once_with()
    def test_an_internal_abort_is_immediate_even_between_owner_polls(self):
        abort=threading.Event()
        with mock.patch.object(parallel.time,'monotonic',return_value=100.0):
            observer=parallel._cancellation(lambda:False,abort)
            self.assertFalse(observer());abort.set();self.assertTrue(observer())
    def test_unreadable_owner_controls_propagate_instead_of_becoming_a_clear_poll(self):
        abort=threading.Event()
        with mock.patch.object(parallel.time,'monotonic',return_value=100.0):
            observer=parallel._cancellation(mock.Mock(side_effect=OSError('unreadable')),abort)
            with self.assertRaisesRegex(OSError,'unreadable'):observer()
            self.assertTrue(abort.is_set())
    def test_transient_fresh_stop_is_latched_before_the_next_slot_can_admit(self):
        flag=[False];abort=threading.Event()
        with mock.patch.object(parallel.time,'monotonic',return_value=100.0):
            observer=parallel._cancellation(lambda:flag[0],abort)
            self.assertFalse(observer());flag[0]=True
            with self.assertRaises(InterruptedError):parallel._fresh_check(lambda:flag[0],abort)
            self.assertTrue(abort.is_set());flag[0]=False
            with self.assertRaises(InterruptedError):parallel._fresh_check(lambda:flag[0],abort)
            self.assertTrue(observer())
    def test_fresh_unreadable_control_aborts_other_slots_before_propagation(self):
        abort=threading.Event()
        with self.assertRaisesRegex(OSError,'unreadable'):
            parallel._fresh_check(mock.Mock(side_effect=OSError('unreadable')),abort)
        self.assertTrue(abort.is_set())
        with self.assertRaises(InterruptedError):parallel._fresh_check(lambda:False,abort)
    def test_new_command_rechecks_stop_even_before_the_copy_poll_interval_expires(self):
        from localharness import source_update_battery as battery
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);repo=root/'repository';repo.mkdir();(repo/'logic.py').write_text('VALUE=1\n')
            files=battery.bundle.inventory(repo);cache=root/'cache';cache.mkdir();work=root/'work';work.mkdir()
            stopped=[False];original=parallel._copy
            def copy_then_stop(*args,**kwargs):
                original(*args,**kwargs);stopped[0]=True
            lane={'id':'must-not-launch','kind':'script','sources':['logic.py']}
            with mock.patch.object(parallel.time,'monotonic',return_value=100.0), \
                    mock.patch.object(parallel,'_copy',side_effect=copy_then_stop), \
                    mock.patch.object(battery,'_run_lane') as execute:
                with self.assertRaises(InterruptedError):
                    parallel.run(battery,[lane],repo,files,work,None,{}, {},cache,lambda:stopped[0],{'executor':{}},{})
                execute.assert_not_called()
    def test_private_copy_reuses_annotations_only_for_identical_freshly_hashed_bytes(self):
        from localharness import source_update_battery as battery
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'test_native.py'
            path.write_text("HELENE_TEST_NATIVE_INPUTS=('DISPLAY',)\ndef test_actual(): pass\n")
            expected=battery.bundle.inventory(root)
            with mock.patch.object(battery.bundle,'python_framework',side_effect=AssertionError('already classified')), \
                    mock.patch.object(battery.bundle,'python_native_inputs',side_effect=AssertionError('already classified')):
                self.assertEqual(parallel.inventory(battery,root,lambda:False,expected=expected),expected)
                path.write_text("HELENE_TEST_NATIVE_INPUTS=()\ndef test_actual(): pass\n")
                self.assertNotEqual(parallel.inventory(battery,root,lambda:False,expected=expected),expected)

if __name__=='__main__':unittest.main()
