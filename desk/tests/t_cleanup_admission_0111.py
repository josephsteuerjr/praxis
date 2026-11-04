"""Cleanup keeps an earned generation; it never invents five-minute credit."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from localharness import portable_switch as switch

class CleanupAdmission(unittest.TestCase):
    def earned(self):
        value=switch.HealthWindow()
        for now in range(0,switch.TRIAL_SECONDS+1,5):value.observe('working',float(now))
        return value

    def test_short_or_stale_observations_cannot_admit_cleanup(self):
        short=switch.HealthWindow();short.observe('working',0)
        for now in range(5,switch.TRIAL_SECONDS,5):short.observe('working',float(now))
        with self.assertRaises(ValueError):short.seal_retirement(float(switch.TRIAL_SECONDS-5))
        with self.assertRaises(ValueError):self.earned().seal_retirement(float(switch.TRIAL_SECONDS+16))

    def test_uncommitted_gap_still_resets_the_working_trial(self):
        value=self.earned()
        self.assertFalse(value.observe('working',float(switch.TRIAL_SECONDS+16)))
        self.assertIsNone(value.retirement_identity)

    def test_earned_cleanup_still_reads_real_bytes_after_its_own_slow_effect(self):
        value=self.earned();value.seal_retirement(float(switch.TRIAL_SECONDS))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'input';path.write_bytes(b'actual private source')
            with patch.object(switch,'health',return_value='working'), \
                 patch.object(switch.time,'monotonic',return_value=float(switch.TRIAL_SECONDS+30)):
                result=switch.monitored_validation(root,value,path.read_bytes)
        self.assertEqual(result,b'actual private source');self.assertEqual(value.since,0.)

    def test_new_generation_revokes_earned_cleanup(self):
        value=self.earned();value.seal_retirement(float(switch.TRIAL_SECONDS))
        with patch.object(switch,'health',return_value='replacement'), \
             patch.object(switch.time,'monotonic',return_value=float(switch.TRIAL_SECONDS+30)):
            with self.assertRaises(switch.TrialRestart):switch.monitored_validation(Path('.'),value,lambda:True)
        self.assertIsNone(value.retirement_identity)

    def test_restart_never_restores_admission_from_an_old_receipt(self):
        value=switch.HealthWindow();value.observe('working',float(switch.TRIAL_SECONDS+30))
        with self.assertRaises(ValueError):value.seal_retirement(float(switch.TRIAL_SECONDS+30))

if __name__=='__main__':unittest.main()
