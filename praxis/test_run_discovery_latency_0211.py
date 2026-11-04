"""Discovery must stay fresh without re-enumerating the historical tree per tick."""
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

import run_manager
from test_run_manager import RunManagerBase


class DiscoveryLatency(RunManagerBase):
    def test_broken_and_loop_manifest_links_remain_unproven(self):
        self.create('seed')
        month=next(self.manager.root.iterdir())
        for label,target in [('broken','missing.json'),('loop','manifest.json')]:
            directory=month/('run-'+label);directory.mkdir()
            (directory/'manifest.json').symlink_to(target)
            self.assertIn(directory.name,self.manager.run_ids())
            self.assertIn(directory.name,self.manager.live_run_ids())

    @unittest.skipUnless(sys.platform=='linux' and os.geteuid()==0,
                         'requires Linux privilege drop to test real directory access')
    def test_readable_unsearchable_run_stays_visible_without_privilege(self):
        created=self.create('unsearchable')
        directory=self.manager._find(created.run_id)
        self.manager.transition(created.run_id,'running')
        self.manager.transition(created.run_id,'done')
        self.assertEqual(self.manager.live_run_ids(),[])
        # RunManager intentionally makes each ancestor private. Open only this
        # synthetic fixture's ancestors before denying passage to the run itself.
        visible=directory.parent
        while visible.is_relative_to(self.base):
            visible.chmod(0o755);visible=visible.parent
        directory.chmod(0o444)
        read_fd,write_fd=os.pipe();pid=os.fork()
        if pid==0:
            try:
                os.close(read_fd);os.setgroups([]);os.setgid(65534);os.setuid(65534)
                found={'ids':self.manager.run_ids(),'live':self.manager.live_run_ids()}
                os.write(write_fd,json.dumps(found).encode());os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(write_fd)
        try:
            import select
            ready,_,_=select.select([read_fd],[],[],10)
            if not ready:
                os.kill(pid,9)
                self.fail('unprivileged discovery did not return')
            raw=os.read(read_fd,65536)
            _,status=os.waitpid(pid,0)
            self.assertEqual(status,0)
            found=json.loads(raw)
            self.assertIn(created.run_id,found['ids'])
            self.assertIn(created.run_id,found['live'])
        finally:
            os.close(read_fd);directory.chmod(0o755)
            try:os.waitpid(pid,0)
            except ChildProcessError:pass

    def test_warm_discovery_does_not_walk_run_directories(self):
        created=[self.create(str(i)).run_id for i in range(12)]
        self.assertEqual(self.manager.run_ids(),sorted(created))
        allowed={str(self.manager.root),*(str(p) for p in self.manager.root.iterdir())}
        scanned=[];original=os.scandir
        def observe(path):
            scanned.append(os.fspath(path));return original(path)
        with mock.patch.object(run_manager.os,'scandir',side_effect=observe):
            self.assertEqual(self.manager.run_ids(),sorted(created))
        self.assertTrue(scanned,'root membership must still be checked')
        self.assertTrue(set(scanned)<=allowed,scanned)

    def test_late_manifest_in_reserved_directory_is_not_hidden(self):
        self.create('seed')
        month=next(self.manager.root.iterdir());reserved=month/'run-late'
        reserved.mkdir();before=self.manager.run_ids()
        self.assertNotIn(reserved.name,before)
        (reserved/'manifest.json').write_text('{}')
        self.assertIn(reserved.name,self.manager.run_ids())
        self.assertIn(reserved.name,self.manager.live_run_ids(),'unproven data must stay visible')

    def test_external_create_delete_and_rename_are_seen(self):
        first=self.create('first');self.manager.run_ids()
        other=run_manager.RunManager(self.base)
        second=self.create('second',manager=other)
        self.assertEqual(set(self.manager.run_ids()),{first.run_id,second.run_id})
        old=other._find(second.run_id);renamed=old.with_name('run-renamed');old.rename(renamed)
        self.assertEqual(set(self.manager.run_ids()),{first.run_id,renamed.name})
        shutil.rmtree(renamed)
        self.assertEqual(self.manager.run_ids(),[first.run_id])

    def test_deleted_and_replaced_month_cannot_return_old_rows(self):
        old=self.create('old');self.manager.run_ids()
        month=self.manager._find(old.run_id).parent
        held=month.with_name(month.name+'-held');month.rename(held)
        # Restore the original month name with a new inode and different contents.
        month.mkdir();new=month/'run-new';new.mkdir();(new/'manifest.json').write_text('{}')
        shutil.rmtree(held)
        self.assertEqual(self.manager.run_ids(),[new.name])
        shutil.rmtree(month)
        self.assertEqual(self.manager.run_ids(),[])

    def test_new_month_and_manifest_atomic_replacement_stay_visible(self):
        original=self.create('original');self.manager.run_ids()
        month=self.manager.root/'9999-01';directory=month/'run-future'
        directory.mkdir(parents=True);(directory/'manifest.json').write_text('{}')
        self.assertEqual(set(self.manager.run_ids()),{original.run_id,directory.name})
        (directory/'manifest.json').unlink()
        self.assertEqual(self.manager.run_ids(),[original.run_id])
        tmp=directory/'replacement.tmp';tmp.write_text('{}');tmp.replace(directory/'manifest.json')
        self.assertIn(directory.name,self.manager.run_ids())

    def test_month_change_during_listing_is_not_cached_as_complete(self):
        self.create('first');month=next(self.manager.root.iterdir())
        original=os.scandir;fired=[]
        def concurrent_publish(path):
            iterator=original(path)
            same=(os.fstat(path).st_ino==month.stat().st_ino if isinstance(path,int)
                  else Path(path)==month)
            if same and not fired:
                rows=list(iterator);iterator.close();fired.append(True)
                self.create('published')
                class Snapshot:
                    def __enter__(self):return iter(rows)
                    def __exit__(self,*args):return False
                    def __iter__(self):return iter(rows)
                return Snapshot()
            return iterator
        with mock.patch.object(run_manager.os,'scandir',side_effect=concurrent_publish):
            self.manager.run_ids()
        self.assertTrue(fired)
        self.assertIn('run-published',self.manager.run_ids())

    def test_directory_symlink_membership_matches_existing_discovery(self):
        created=self.create('linked');self.manager.run_ids()
        month=self.manager._find(created.run_id).parent
        alias=month.with_name('9999-alias');alias.symlink_to(month,target_is_directory=True)
        self.assertEqual(self.manager.run_ids(),[created.run_id,created.run_id])
        alias.unlink()
        self.assertEqual(self.manager.run_ids(),[created.run_id])

    def test_dangling_child_link_target_can_publish_without_month_change(self):
        original=self.create('seed');month=self.manager._find(original.run_id).parent
        target=self.base/'external-reservation'
        link=month/'run-late-link';link.symlink_to(target,target_is_directory=True)
        self.assertEqual(self.manager.run_ids(),[original.run_id])
        before=month.stat().st_mtime_ns
        target.mkdir();(target/'manifest.json').write_text('{}')
        self.assertEqual(month.stat().st_mtime_ns,before)
        self.assertEqual(set(self.manager.run_ids()),{original.run_id,link.name})

    def test_child_link_file_target_can_become_directory_without_month_change(self):
        original=self.create('seed');month=self.manager._find(original.run_id).parent
        target=self.base/'external-file';target.write_text('not a directory')
        link=month/'run-late-link';link.symlink_to(target)
        self.assertEqual(self.manager.run_ids(),[original.run_id])
        before=month.stat().st_mtime_ns
        target.unlink();target.mkdir();(target/'manifest.json').write_text('{}')
        self.assertEqual(month.stat().st_mtime_ns,before)
        self.assertEqual(set(self.manager.run_ids()),{original.run_id,link.name})

    def test_month_alias_ABA_cannot_publish_foreign_listing_as_current(self):
        self.manager.root.mkdir(parents=True)
        targets=[]
        for label in ('A','B'):
            target=self.base/label;directory=target/('run-'+label)
            directory.mkdir(parents=True);(directory/'manifest.json').write_text('{}')
            targets.append(target)
        alias=self.manager.root/'9999-alias';alias.symlink_to(targets[0],target_is_directory=True)
        real_scan=os.scandir;real_open=os.open;fired=[]
        def redirect(call,path,*args,**kwargs):
            if not isinstance(path,int) and Path(path)==alias and not fired:
                fired.append(True);alias.unlink();alias.symlink_to(targets[1],target_is_directory=True)
                try:return call(path,*args,**kwargs)
                finally:alias.unlink();alias.symlink_to(targets[0],target_is_directory=True)
            return call(path,*args,**kwargs)
        with mock.patch.object(run_manager.os,'scandir',side_effect=lambda p:redirect(real_scan,p)), \
             mock.patch.object(run_manager.os,'open',side_effect=lambda p,*a,**k:redirect(real_open,p,*a,**k)):
            self.manager.run_ids()  # listing can race, but it must not poison later calls
        self.assertTrue(fired)
        self.assertEqual(self.manager.run_ids(),['run-A'])
        self.assertEqual(self.manager.run_ids(),['run-A'])


if __name__=='__main__':unittest.main()
