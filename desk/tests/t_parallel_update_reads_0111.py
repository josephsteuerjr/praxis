"""Cancellation and scope custody for the real update read pool."""
import threading
import unittest
from localharness import validation_read


class ParallelUpdateReads(unittest.TestCase):
    def test_worker_inherits_the_exact_validation_callback(self):
        calls=[];lock=threading.Lock();main=threading.get_ident()
        def check():
            with lock:calls.append(threading.get_ident())
        def work(value):
            self.assertIs(validation_read.current(),check)
            validation_read.checkpoint()
            return value*value
        with validation_read.scope(check):
            result=validation_read.parallel(work,range(9),workers=3)
            self.assertIs(validation_read.current(),check)
        self.assertEqual(result,[value*value for value in range(9)])
        self.assertTrue(any(identity!=main for identity in calls))
        self.assertIsNone(validation_read.current())

    def test_revoked_scope_never_enters_the_file_function(self):
        entered=[]
        def cancelled():raise validation_read.ReadCancelled()
        with validation_read.scope(cancelled):
            with self.assertRaises(validation_read.ReadCancelled):
                validation_read.parallel(lambda item:entered.append(item),range(8),workers=3)
        self.assertEqual(entered,[])
        self.assertIsNone(validation_read.current())

    def test_failure_joins_running_readers_before_returning(self):
        started=threading.Event();settled=threading.Event()
        def read(item):
            if item==0:
                if not started.wait(3):raise AssertionError('Other reader did not start')
                raise ValueError('bad source bytes')
            started.set()
            settled.set()
            return item
        with self.assertRaisesRegex(ValueError,'bad source bytes'):
            validation_read.parallel(read,[0,1],workers=2)
        self.assertTrue(settled.is_set())


if __name__=='__main__':unittest.main()
