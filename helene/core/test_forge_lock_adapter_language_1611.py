"""Language lifetime must not alter lock ownership or trusted adapter subjects."""
import copy
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

import forge
import frame_language as fl
import test_forge_coordination_2610 as fixture


class LockAdapterLanguage(unittest.TestCase):
    setUp = fixture.Coordination.setUp
    patch = fixture.Coordination.patch

    def test_generator_pins_enter_body_release_and_resets_after_exception16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                saved = {'lang':ui, 'agent':{'frame_language':language}}
                next_language = 'fr' if language != 'fr' else 'zh'
                lock = self.directory/'.mutation.lock'
                released = []
                original = Path.unlink
                def unlink(path, *args, **kwargs):
                    if path == lock:released.append(fl.language())
                    return original(path, *args, **kwargs)
                with self.subTest(ui=ui, frame=language), mock.patch.object(fl,'settings',side_effect=lambda:saved), mock.patch.object(Path,'unlink',unlink):
                    # Construction is lazy: language must be chosen at __enter__.
                    manager = forge._mutation_lock(self.tid)
                    with self.assertRaisesRegex(RuntimeError,'body exception'):
                        with manager as beat:
                            self.assertEqual(fl.language(), language)
                            token = lock.read_bytes()
                            saved['agent']['frame_language'] = next_language
                            beat(600)
                            self.assertEqual(lock.read_bytes(), token)
                            self.assertEqual(fl.language(), language)
                            self.assertGreater(lock.stat().st_mtime, forge.time.time()+500)
                            raise RuntimeError('body exception')
                    self.assertEqual(released, [language])
                    self.assertFalse(lock.exists())
                    self.assertEqual(fl.language(), next_language)
                    # Release must never delete a successor's token.
                    with forge._mutation_lock(self.tid):lock.write_text('successor:token',encoding='utf8')
                    self.assertEqual(lock.read_text(encoding='utf8'),'successor:token')
                    original(lock)
        self.spawn.assert_not_called()

    def test_timeout_respects_legacy_future_lease_and_disabled_idle_without_mutation(self):
        lock = self.directory/'.mutation.lock'
        for language in fl.LANGUAGES:
            for kind in ('legacy','current','future','disabled','unreadable'):
                raw = f'{os.getpid()}:legacy' if kind=='legacy' else f'{os.getpid()}:born:held'
                if kind=='unreadable':raw='unreadable token'
                lock.write_text(raw,encoding='utf8')
                idle = -600 if kind=='future' else 10
                os.utime(lock,(1000-idle,1000-idle))
                before = lock.read_bytes()
                saved = {'lang':'en','agent':{'frame_language':language}}
                def alive(*args):
                    saved['agent']['frame_language']='zh' if language!='zh' else 'fr'
                    return True
                with self.subTest(frame=language, kind=kind), mock.patch.object(fl,'settings',side_effect=lambda:saved), mock.patch.object(forge,'_owner_alive',side_effect=alive), mock.patch.object(forge.time,'time',return_value=1000), mock.patch.object(forge.time,'monotonic',side_effect=[0,1]), mock.patch.object(forge,'LOCK_IDLE_ABANDONED_SEC',0 if kind=='disabled' else 300):
                    with self.assertRaises(TimeoutError) as caught:
                        with forge._mutation_lock(self.tid,timeout=.1):self.fail('held lock was stolen')
                    with fl.use(language):
                        if kind in ('legacy','unreadable'):rule=fl.text('forge.lifecycle.56',v1=f'{forge.LOCK_LEGACY_IDLE_SEC:.0f}',v3=f'{forge.LOCK_IDLE_ABANDONED_SEC:.0f}')
                        elif kind=='disabled':rule=fl.text('forge.lifecycle.58')
                        else:rule=fl.text('forge.lifecycle.57',v1='300')
                        state=fl.text('forge.lifecycle.60' if idle<0 else 'forge.lifecycle.59',v1=str(0 if kind=='unreadable' else abs(idle)))
                        pid=0 if kind=='unreadable' else os.getpid()
                        self.assertEqual(str(caught.exception),fl.text('forge.lifecycle.61',v1=self.tid,v3=str(pid),v5=state,v7=rule))
                    self.assertEqual(lock.read_bytes(), before)
                lock.unlink()
        self.spawn.assert_not_called()

    def test_adapter_registration_preserves_subject_history_and_rejects_drift16axes(self):
        modules={}
        for name in ('source_update_worker','self_review_worker'):
            module=types.ModuleType(name);script=self.root/(name+'.py');script.write_text('# fixture',encoding='utf8')
            module.__file__=str(script);modules[name]=module
        with mock.patch.dict(sys.modules,{**modules,'localharness':None}):
            for index,(ui,language) in enumerate((ui,lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
                with self.subTest(ui=ui,frame=language), mock.patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}):
                    for offset,kind in enumerate(('source_update','self_review')):
                        tid='code-'+f'{index*2+offset+200:08x}'
                        script=modules[kind+'_worker'].__file__
                        args=[tid,str(self.root),'a'*64]
                        if kind=='source_update':args.append('b'*64)
                        args.append(script)
                        producer=getattr(forge,kind+'_task')
                        task=producer(*args)
                        self.assertEqual(forge._worker_script(task),Path(script).resolve())
                        self.assertEqual(task['goal'],fl.text('forge.lifecycle.156' if kind=='source_update' else 'forge.lifecycle.161'))
                        record=forge._task_dir(tid)/'task.json';before=record.read_bytes()
                        with fl.use('zh' if language!='zh' else 'fr'):self.assertEqual(producer(*args),task)
                        drift=list(args);drift[2]='c'*64
                        with self.assertRaises(ValueError):producer(*drift)
                        forged=copy.deepcopy(task);forged[kind]['runtime_code_dir']=str(self.root/'foreign')
                        with self.assertRaises(ValueError) as caught:forge._worker_script(forged)
                        self.assertEqual(str(caught.exception),fl.text('forge.lifecycle.162'))
                        self.assertEqual(record.read_bytes(),before)
        self.spawn.assert_not_called()
