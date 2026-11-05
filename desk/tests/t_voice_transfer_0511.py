"""Causal integration: cold carry, persistent pins and actual server role access."""
from pathlib import Path
import io, json, os, subprocess, sys, tempfile, unittest, zipfile
from unittest.mock import patch

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK/'localharness')]
from localharness import component_store as store, carry, fish_speech


class CarryVoice(unittest.TestCase):
    def test_legacy_programme_relative_sample_moves_with_the_agent(self):
        from localharness import app_migration
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';data=old/'data';new.mkdir()
            data.mkdir(parents=True);(old/'assets').mkdir();sample=old/'assets/reference.wav';sample.write_bytes(b'legacy personal voice')
            config={'tree':str(data),'code':str(data),'runner':'','voice':{'tts_reference':'assets/reference.wav'}}
            (old/'helene.json').write_text(json.dumps(config))
            with patch.object(store,'root',return_value=root/'store'):
                app_migration.prepare(old,new,root/'profile')
                fresh=json.loads((new/'helene.json').read_text());selected=Path(fresh['voice']['tts_reference'])
                self.assertTrue(selected.is_relative_to(Path(fresh['tree'])))
                sample.unlink();self.assertEqual(selected.read_bytes(),b'legacy personal voice')

    def test_data_migration_detaches_absolute_personal_model_reference_from_old_folder(self):
        from localharness import app_migration
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';data=old/'data';new.mkdir()
            sample=data/'models/xtts/my-voice.wav';sample.parent.mkdir(parents=True);sample.write_bytes(b'personal synthetic sample')
            (data/'memory').mkdir();(data/'memory/life.md').write_text('personal life')
            cfg={'tree':str(data),'code':str(data),'runner':'','voice':{'voice':'xtts','tts_reference':str(sample)}}
            (old/'helene.json').write_text(json.dumps(cfg))
            with patch.object(store,'root',return_value=root/'store'):
                app_migration.prepare(old,new,root/'profile')
                fresh=json.loads((new/'helene.json').read_text())
                selected=store.reference_path(Path(fresh['tree']),fresh['voice']['tts_reference'])
                self.assertTrue(selected.is_relative_to(root/'store'),'new profile must not depend on a retired model folder')
                sample.unlink()
                self.assertEqual(selected.read_bytes(),b'personal synthetic sample')
                self.assertEqual(json.loads((old/'helene.json').read_text()),cfg)

    def test_ordinary_update_resolves_the_personal_sample_against_agent_pins(self):
        from localharness import update_profile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';data=root/'agent';old.mkdir();new.mkdir()
            sample=data/'models/xtts/my-voice.wav';sample.parent.mkdir(parents=True);sample.write_bytes(b'personal synthetic sample')
            cfg={'tree':str(data),'code':str(data),'runner':'','voice':{'voice':'xtts','tts_reference':'models/xtts/my-voice.wav'}}
            (old/'helene.json').write_text(json.dumps(cfg))
            with patch.object(store,'root',return_value=root/'store'):
                store.preserve_models(data,data)
                update_profile.prepare(old,new,snapshot=False)
                fresh=json.loads((new/'helene.json').read_text())
                self.assertTrue(Path(fresh['voice']['tts_reference']).samefile(store.model_path(data,'xtts')/'my-voice.wav'))
                self.assertEqual(json.loads((old/'helene.json').read_text()),cfg)

    def test_server_voice_status_does_not_load_a_foreign_desktop_component_map(self):
        from localharness import voice, component_downloads
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'app').mkdir()
            (root/'app/component-downloads.json').write_text('{"target":"windows-x86_64"}')
            with patch.object(voice,'install_root',return_value=root),patch.dict(os.environ,{'HELENE_SUPERVISOR':'serverboot','HELENE_XTTS_PYTHON':str(root/'absent/python')}),patch.object(component_downloads,'load',side_effect=AssertionError('foreign platform map must not be loaded')):
                status=voice.speech_state(root/'data',{'voice':{'speak':False,'voice':'xtts'}})
                self.assertFalse(status['ready'])
                self.assertFalse(status['library']['downloadable'])

    def test_pinned_weights_stay_home_and_personal_samples_rebind_without_host_pins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);old=root/'old';data=old/'data';data.mkdir(parents=True)
            models=data/'models/xtts';models.mkdir(parents=True)
            (models/'model.pth').write_bytes(b'synthetic model weight'*10000)
            (models/'my-voice.wav').write_bytes(b'personal synthetic WAV')
            with patch.object(store,'root',return_value=root/'store'):
                store.preserve_models(data,data)
                original=(data/store.PINS).read_bytes()
                config=old/'helene.json'
                config.write_text(json.dumps({'tree':'data','voice':{'voice':'xtts','tts_reference':'models/xtts/my-voice.wav'}}))
                archive=root/'carry.zip';carry.export(config,archive)
                with zipfile.ZipFile(archive) as z:
                    names=z.namelist();self.assertEqual(len(names),len(set(names)))
                    self.assertNotIn('data/'+store.PINS,names)
                    self.assertFalse(any('/models/' in name for name in names))
                    moved=json.loads(z.read('helene.json'))['voice']['tts_reference']
                    self.assertTrue(moved.startswith('voice-references/'))
                    self.assertEqual(z.read('data/'+moved),b'personal synthetic WAV')
                new=root/'new';new.mkdir();cfg=new/'helene.json';cfg.write_text('{"tree":"data"}')
                carry.import_(cfg,archive)
                reference=json.loads(cfg.read_text())['voice']['tts_reference']
                self.assertEqual((new/'data'/reference).read_bytes(),b'personal synthetic WAV')
                self.assertFalse((new/'data'/store.PINS).exists())
                # A second move keeps one selected sample and no duplicate ZIP entries.
                again=root/'again.zip';carry.export(cfg,again)
                with zipfile.ZipFile(again) as z:
                    self.assertEqual(len(z.namelist()),len(set(z.namelist())))
                    passport=json.loads(z.read(carry.PASSPORT))
                    payload=[row for row in z.infolist() if row.filename.startswith('data/') and not row.is_dir()]
                    self.assertEqual(passport['files'],len(payload))
                    self.assertEqual(passport['bytes'],sum(row.file_size for row in payload))
                self.assertEqual((data/store.PINS).read_bytes(),original)

    def test_old_archive_host_pins_are_not_installed_on_the_new_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cfg=root/'helene.json';cfg.write_text('{"tree":"data"}')
            archive=root/'legacy.zip'
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('helene-carry.json',json.dumps({'schema':carry.SCHEMA}))
                z.writestr('helene.json','{}')
                z.writestr('data/'+store.PINS,'{"pins":{"models/xtts":{"store":"C:/foreign"}}}')
                z.writestr('data/memory/kept.md','personal life')
            result=carry.import_(cfg,archive)
            self.assertTrue(result['ignored_host_component_pins'])
            self.assertEqual((root/'data/memory/kept.md').read_text(),'personal life')
            self.assertFalse((root/'data'/store.PINS).exists())

    def test_server_programme_pins_use_atomic_directory_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);program=root/'program';payload=root/'payload';payload.mkdir();(payload/'sample').write_bytes(b'not executable')
            with patch.object(store,'root',return_value=root/'store'),patch.dict(os.environ,{'HELENE_COMPONENT_PROGRAM':str(program)}):
                ref,_=store.import_directory(payload)
                store.pin(program,'voice',ref)
                self.assertTrue((program/'component-state'/store.PINS).is_file())
                self.assertFalse((program/store.PINS).exists())
                self.assertEqual(store.path(program,'voice').joinpath('sample').read_bytes(),b'not executable')


@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:-1)()==0,'native container root/roles required')
class ServerRoles(unittest.TestCase):
    def test_private_key_and_durable_receipt_survive_real_role_boundary(self):
        import pwd
        try:agent,desk=(pwd.getpwnam(name) for name in ('helene','desk'))
        except KeyError:self.skipTest('product accounts not installed in this stand')
        from localharness import server_components
        with tempfile.TemporaryDirectory() as temporary,patch.dict(os.environ,{'HELENE_SUPERVISOR':'serverboot'}):
            root=Path(temporary);root.chmod(0o755);program=root/'program';program.mkdir();tree=root/'data';tree.mkdir();tree.chmod(0o2770);os.chown(tree,agent.pw_uid,agent.pw_gid)
            memory=tree/'memory';memory.mkdir();memory.chmod(0o2770);os.chown(memory,agent.pw_uid,agent.pw_gid)
            env={**os.environ,'PYTHONPATH':str(DESK),'XDG_DATA_HOME':str(root/'shared'),'HELENE_COMPONENT_PROGRAM':str(program),'TREE':str(tree)}
            with patch.dict(os.environ,env):server_components.prepare(program,tree)
            script="""import os,json
from pathlib import Path
from localharness import fish_speech as f
t=Path(os.environ['TREE'])
"""
            def run(user,code):
                done=subprocess.run([sys.executable,'-c',script+code],env=env,user=user.pw_uid,group=user.pw_gid,extra_groups=[],capture_output=True,text=True)
                self.assertEqual(done.returncode,0,done.stderr)
            run(desk,"f.configure_key(t,'synthetic-private-fish-key');assert f._key(t)=='synthetic-private-fish-key'")
            run(agent,"assert f._key(t)=='synthetic-private-fish-key';f.cancel(t,'a'*32)")
            run(desk,"assert f.status(t,'a'*32)['cancelled'] is True")
            with patch.dict(os.environ,env):server_components.prepare(program,tree)
            run(agent,"assert f._key(t)=='synthetic-private-fish-key';assert f.status(t,'a'*32)['cancelled'] is True")
            nobody=pwd.getpwnam('nobody')
            run(nobody,"\ntry:f._key(t)\nexcept RuntimeError:pass\nelse:raise AssertionError('foreign role read private key')")


if __name__=='__main__':unittest.main()
