"""A tree lock torn by a hard crash must not wedge the engine forever."""
from pathlib import Path
import json
import os
import tempfile
import time
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import boot


class TornTreeLock(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tree = Path(temp.name)
        self.path = boot.tree_lock_path(self.tree)
        self.path.parent.mkdir(parents=True)
        self.addCleanup(boot.release_tree)

    def tear(self, age):
        # What NTFS left on 08.11.2026: the size survived, the bytes did not.
        self.path.write_bytes(b"\0" * 130)
        stamp = time.time() - age
        os.utime(self.path, (stamp, stamp))

    def owner(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def leftovers(self):
        return [p.name for p in self.path.parent.iterdir() if p != self.path]

    def test_an_old_torn_lock_is_reclaimed_by_a_normal_runner(self):
        self.tear(boot._LOCK_FRESH_SEC + 60)
        self.assertEqual(boot.claim_tree(self.tree, role="test"), {})
        self.assertEqual(self.owner()["pid"], os.getpid())

    def test_a_fresh_unreadable_lock_still_counts_as_another_starter(self):
        self.tear(0)
        self.assertEqual(boot.claim_tree(self.tree, role="test")["pid"], 0)
        self.assertEqual(self.path.read_bytes(), b"\0" * 130)

    def test_strict_first_birth_never_reclaims_even_an_old_torn_lock(self):
        self.tear(boot._LOCK_FRESH_SEC + 60)
        busy = boot.claim_tree(self.tree, role="explicit first-create", reclaim_stale=False)
        self.assertEqual(busy["pid"], 0)
        self.assertEqual(self.path.read_bytes(), b"\0" * 130)

    def test_a_crash_inside_the_heartbeat_leaves_the_previous_record_readable(self):
        self.assertEqual(boot.claim_tree(self.tree, role="test"), {})
        before = self.owner()
        with patch.object(boot.os, "fsync", side_effect=OSError("crash fixture")):
            boot.refresh_tree_lock()
        self.assertEqual(self.owner(), before)
        self.assertEqual(self.leftovers(), [])

    def test_the_heartbeat_still_refreshes_the_record(self):
        self.assertEqual(boot.claim_tree(self.tree, role="test"), {})
        before = self.owner()
        later = before["at"] + 5
        with patch.object(boot.time, "time", return_value=later):
            boot.refresh_tree_lock()
        self.assertEqual(self.owner(), dict(before, at=later))
        self.assertEqual(self.leftovers(), [])

    def test_unknown_nonzero_record_is_not_reclaimed_even_when_old(self):
        self.tear(boot._LOCK_FRESH_SEC+60)
        self.path.write_bytes(b'{"pid": "unfinished owner')
        old=time.time()-boot._LOCK_FRESH_SEC-60
        os.utime(self.path,(old,old));before=self.path.read_bytes()
        self.assertTrue(boot.claim_tree(self.tree))
        self.assertEqual(self.path.read_bytes(),before)

    def test_unreadable_record_does_not_grant_reclaim(self):
        self.tear(boot._LOCK_FRESH_SEC+60)
        with patch.object(Path,'open',side_effect=PermissionError('read denied')):
            self.assertFalse(boot._torn_lock_is_abandoned(self.path))
        self.assertEqual(self.path.read_bytes(),b'\0'*130)

    def test_existing_live_owner_is_never_overwritten(self):
        holder={'pid':os.getpid()+1,'at':time.time(),'token':'other-owner'}
        self.path.write_text(json.dumps(holder),encoding='utf-8')
        with patch.object(boot,'_pid_alive',return_value=True):
            self.assertEqual(boot.claim_tree(self.tree),holder)
        self.assertEqual(self.owner(),holder)

    def test_failed_atomic_replace_keeps_previous_readable_record(self):
        self.assertEqual(boot.claim_tree(self.tree),{})
        before=self.owner()
        with patch.object(boot.os,'replace',side_effect=PermissionError('replace denied')):
            boot.refresh_tree_lock()
        self.assertEqual(self.owner(),before)
        self.assertEqual(self.leftovers(),[])

    def test_heartbeat_never_overwrites_owner_changed_while_flushing(self):
        self.assertEqual(boot.claim_tree(self.tree),{})
        other={'pid':os.getpid()+1,'at':time.time(),'token':'new-owner'}
        def change_owner(fd):self.path.write_text(json.dumps(other),encoding='utf-8')
        with patch.object(boot.os,'fsync',side_effect=change_owner):boot.refresh_tree_lock()
        self.assertEqual(self.owner(),other)
        self.assertIsNone(boot._LOCK['path'])
        self.assertEqual(self.leftovers(),[])


if __name__ == "__main__":
    unittest.main()
