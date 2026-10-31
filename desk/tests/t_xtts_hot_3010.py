"""Hot voice profiles and the real application worker loop; no model/network."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import speech_protocol as ipc, speech_worker, voice

spec = importlib.util.spec_from_file_location('hot_xtts_engine', DESK.parent/'helene/core/xtts_tts_worker.py')
engine_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine_module)
GEN = 'a'*32


def wav(path):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
        stream.writeframes(b'\0\0'*240)


class Queue(unittest.TestCase):
    def test_independent_clients_serialize_ticket_and_publication_with_a_frozen_clock(self):
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);incoming=home/'inbox'
            first=incoming/('e'*32+'.json');second=incoming/('b'*32+'.json')
            entered=home/'first-publishing';attempted=home/'second-attempted';release=home/'release-first'
            clock=1700000000000000000
            script="""
from pathlib import Path
import sys,time
if sys.argv[5]=='first':
    # Same bare sibling import as the embedded direct worker, with -I.
    sys.path.insert(0,str(Path(sys.argv[1])/'localharness'))
    import speech_protocol as ipc
else:
    sys.path.insert(0,sys.argv[1])
    from localharness import speech_protocol as ipc
path,entered,release=map(Path,sys.argv[2:5]);first=sys.argv[5]=='first'
ipc.time.time_ns=lambda:int(sys.argv[6])
write=ipc.write
def publish(target,value):
    if first and target==path:
        entered.touch();until=time.monotonic()+4
        while not release.exists():
            if time.monotonic()>until:raise TimeoutError('private publisher barrier')
            time.sleep(.01)
    return write(target,value)
ipc.write=publish
if not first:entered.touch()
ipc.enqueue(path,{'generation':'a'*32,'text':path.stem})
"""
            children=[]
            def launch(path,marker,kind):
                child=subprocess.Popen([sys.executable,'-I','-B','-c',script,str(DESK),str(path),str(marker),str(release),kind,str(clock)],
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                children.append(child);return child
            def wait(marker):
                until=time.monotonic()+4
                while not marker.exists():
                    if time.monotonic()>until:self.fail('Private queue publisher did not reach barrier')
                    if any(child.poll() is not None for child in children):self.fail('Private publisher exited before barrier')
                    time.sleep(.01)
            try:
                launch(first,entered,'first');wait(entered)
                launch(second,attempted,'second');wait(attempted)
                time.sleep(.05)
                self.assertFalse(first.exists(),'First writer is held before atomic request publication')
                self.assertFalse(second.exists(),'Second process cannot publish around the first ticket owner')
                release.touch()
                for child in children:
                    out,error=child.communicate(timeout=4)
                    self.assertEqual(child.returncode,0,error.decode(errors='replace'))
                self.assertEqual(ipc.read(first)['enqueued_ns'],clock)
                self.assertEqual(ipc.read(second)['enqueued_ns'],clock+1)
                for path in (first,second):os.utime(path,ns=(10**18,10**18))
                self.assertEqual(ipc.queued(incoming),[first,second])
                # Legacy timestamps share the same nanosecond domain. Their
                # unequal times remain comparable; equal-time legacy FIFO is
                # deliberately not reconstructed from a random UUID.
                before=incoming/('a'*32+'.json');after=incoming/('f'*32+'.json')
                for path,stamp in ((before,clock-1000),(after,clock+1000)):
                    ipc.write(path,{'generation':'a'*32});os.utime(path,ns=(stamp,stamp))
                self.assertEqual(ipc.queued(incoming),[before,first,second,after])
            finally:
                release.touch()
                for child in children:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=4)


class Profiles(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.calls = []
        def condition(audio_path, gpt_cond_len=6, sound_norm_refs=False):
            self.calls.append((Path(audio_path[0]).read_bytes(), gpt_cond_len, sound_norm_refs))
            return ('conditioning', len(self.calls))
        self.engine = SimpleNamespace(get_conditioning_latents=condition,
            speaker_manager=SimpleNamespace(speakers={'A': {'speaker_embedding':'speaker', 'gpt_cond_latent':'latent'}}))
        self.cache = engine_module.VoiceProfiles(self.engine, 'model-sha', limit=2)

    def ref(self, name='sample.wav', content=b'original'):
        path=self.root/name; path.write_bytes(content); return str(path)

    def test_content_key_reuses_profile_across_paths_and_invalidates_same_path(self):
        path=self.ref(); first=self.cache.get({'reference':path})
        self.assertEqual(first,self.cache.get({'reference':self.ref('copy.wav')}))
        Path(path).write_bytes(b'new sample')
        self.assertNotEqual(first[0],self.cache.get({'reference':path})[0])
        self.assertEqual(len(self.calls),2)

    def test_effective_parameters_and_model_are_part_of_key(self):
        path=self.ref(); a=self.cache.get({'reference':path})
        self.assertEqual(a,self.cache.get({'reference':path,'conditioning':{'gpt_cond_len':6}}))
        self.assertNotEqual(a[0],self.cache.get({'reference':path,'conditioning':{'gpt_cond_len':3}})[0])
        other=engine_module.VoiceProfiles(self.engine,'another-model')
        self.assertNotEqual(a[0],other.get({'reference':path})[0])
        with self.assertRaises(ValueError):self.cache.get({'reference':path,'conditioning':{'audio_path':'injection'}})

    def test_builtin_fields_are_named_not_insertion_order(self):
        self.assertEqual(self.cache.get({'speaker':'A'})[1],('latent','speaker'))
        self.assertEqual(self.calls,[])

    def test_lru_is_bounded_and_evicted_profile_is_recomputed(self):
        paths=[self.ref(str(n)+'.wav',str(n).encode()) for n in range(3)]
        for path in paths:self.cache.get({'reference':path})
        self.assertEqual(len(self.cache.cache),2)
        self.cache.get({'reference':paths[0]})
        self.assertEqual(len(self.calls),4)

    def test_snapshot_matches_hashed_bytes_when_reference_replaced(self):
        path=self.ref(); original=self.engine.get_conditioning_latents
        def condition(audio_path,gpt_cond_len=6,sound_norm_refs=False):
            Path(path).write_bytes(b'replaced while conditioning')
            return original(audio_path,gpt_cond_len,sound_norm_refs)
        self.engine.get_conditioning_latents=condition
        self.cache.get({'reference':path})
        self.assertEqual(self.calls[0][0],b'original')
        self.cache.get({'reference':path})
        self.assertEqual(len(self.calls),2)

    def test_cancelled_conditioning_never_enters_cache(self):
        path=self.ref(); cancelled=threading.Event(); original=self.engine.get_conditioning_latents
        def condition(audio_path,gpt_cond_len=6,sound_norm_refs=False):
            result=original(audio_path,gpt_cond_len,sound_norm_refs); cancelled.set(); return result
        self.engine.get_conditioning_latents=condition
        with self.assertRaises(InterruptedError):self.cache.get({'reference':path},lambda:True)
        self.assertEqual(self.calls,[])
        with self.assertRaises(InterruptedError):self.cache.get({'reference':path},cancelled.is_set)
        self.assertEqual(len(self.cache.cache),0)

    def test_splitter_preserves_words_and_obeys_token_budget(self):
        self.engine.tokenizer=SimpleNamespace(char_limits={'ru':150},encode=lambda text,lang:list(text)*2)
        self.engine.args=SimpleNamespace(gpt_max_text_tokens=23)
        text='Первое. Второе предложение! '+ 'длинноесловобезпробелов'*7
        chunks=list(engine_module.sentences(self.engine,text,'ru'))
        self.assertEqual(''.join(chunks).replace(' ',''),text.replace(' ',''))
        self.assertTrue(all(len(chunk)*2<23 for chunk in chunks))
        self.assertEqual(chunks[0],'Первое.')

    def test_warm_once_per_voice_and_language_no_audio_left(self):
        self.engine._helene_voice_profiles=self.cache
        with patch.object(engine_module,'synthesize_wav',side_effect=lambda engine,request,path,stop:wav(path)) as synth:
            first=engine_module.warm_voice(self.engine,{'speaker':'A','language':'ru'})
            engine_module.warm_voice(self.engine,{'speaker':'A','language':'ru'})
            engine_module.warm_voice(self.engine,{'speaker':'A','language':'en'})
        self.assertEqual(synth.call_count,2)
        self.assertFalse(synth.call_args.args[2].exists())
        self.assertEqual(first['voice_identity'],self.cache.get({'speaker':'A'})[0])

    def test_budget_uses_same_case_and_language_normalization_as_inference(self):
        def encode(text,lang):
            self.assertEqual(text,text.strip().lower());self.assertEqual(lang,'en')
            return list(text)*2
        self.engine.tokenizer=SimpleNamespace(char_limits={'en':180},encode=encode)
        self.engine.args=SimpleNamespace(gpt_max_text_tokens=15)
        chunks=list(engine_module.sentences(self.engine,'ABCD EFGH IJKL','en-US'))
        self.assertEqual(''.join(chunks).replace(' ',''),'ABCDEFGHIJKL')


class Worker(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.home=self.root/'memory/.state/xtts'; self.home.mkdir(parents=True)
        self.stop=threading.Event(); self.errors=[]; self.paused=threading.Event()
        p=patch.object(ipc,'blocked',side_effect=lambda tree:self.paused.is_set()); p.start(); self.addCleanup(p.stop)

    def wait(self, predicate, timeout=4):
        until=time.monotonic()+timeout
        while not predicate():
            if self.errors:raise self.errors[0]
            if time.monotonic()>until:self.fail('worker condition timeout: '+str(ipc.read(self.home/'ready.json')))
            time.sleep(.01)

    def launch(self, warm=lambda choice,stop:{}, synth=lambda engine,req,path,stop:wav(path), **options):
        engine=SimpleNamespace(speaker_manager=SimpleNamespace(speakers={'A':{}}),config=SimpleNamespace(languages=['ru']))
        load=Mock(return_value=engine)
        canonical=SimpleNamespace(load_engine=load,warm_voice=lambda eng,choice,stop:warm(choice,stop),synthesize_wav=synth)
        p=patch.object(speech_worker,'engine_module',return_value=canonical); p.start(); self.addCleanup(p.stop)
        def run():
            try:speech_worker.serve(self.home,self.root/'models',self.root,generation=GEN,stopped=self.stop.is_set,codec=False,**options)
            except BaseException as exc:self.errors.append(exc)
        thread=threading.Thread(target=run); thread.start()
        def finish():
            self.stop.set(); thread.join(4)
            self.assertFalse(thread.is_alive()); self.assertEqual(self.errors,[])
        self.addCleanup(finish)
        return load

    def request(self, identity, **values):
        path=self.home/'inbox'/(identity+'.json'); path.with_suffix('.lease').touch()
        ipc.enqueue(path,dict(generation=GEN,deadline=time.time()+20,text=identity,speaker='A',language='ru',format='wav',**values))
        return path

    def test_ready_waits_for_warmup_and_old_stop_does_not_poison_restart(self):
        (self.home/'stop').touch(); entered=threading.Event(); release=threading.Event()
        def warm(choice,cancel):entered.set(); release.wait(2); return {'model_identity':'model','voice_identity':'voice','device':'cpu'}
        load=self.launch(warm); self.wait(entered.is_set)
        self.assertFalse(ipc.read(self.home/'ready.json')['ready'])
        self.assertEqual(ipc.read(self.home/'ready.json')['phase'],'warming_voice')
        release.set(); self.wait(lambda:ipc.read(self.home/'ready.json').get('ready'))
        ready=ipc.read(self.home/'ready.json')
        self.assertGreater(ready['process_identity']['birth'],0); self.assertEqual(ready['voice_identity'],'voice')
        load.assert_called_once()

    def test_bad_voice_retains_loaded_model_then_recovers_on_selection_change(self):
        calls=[]
        def warm(choice,cancel):
            calls.append(choice['speaker'])
            if choice['speaker']!='A':raise ValueError('unknown voice')
            return {'voice_identity':'A'}
        load=self.launch(warm)
        self.wait(lambda:ipc.read(self.home/'ready.json').get('phase')=='error')
        time.sleep(1.15); self.assertEqual(len(calls),1)
        ipc.write(self.home/'selection.json',{'speaker':'A','language':'ru'})
        self.wait(lambda:ipc.read(self.home/'ready.json').get('ready'))
        load.assert_called_once(); self.assertEqual(calls,['Ana Florence','A'])

    def test_reference_replacement_and_pause_rewarm_without_model_reload(self):
        path=self.root/'reference.wav'; wav(path); calls=[]
        ipc.write(self.home/'selection.json',{'reference':str(path)})
        load=self.launch(lambda choice,cancel:(calls.append(choice) or {}))
        self.wait(lambda:ipc.read(self.home/'ready.json').get('ready')); path.write_bytes(b'new reference')
        self.wait(lambda:len(calls)==2)
        self.paused.set(); self.wait(lambda:ipc.read(self.home/'ready.json').get('phase')=='paused')
        self.paused.clear(); self.wait(lambda:ipc.read(self.home/'ready.json').get('ready'))
        self.assertEqual(len(calls),3); load.assert_called_once()

    def test_nan_and_malformed_requests_do_not_crash_or_synthesize(self):
        synth=Mock(side_effect=lambda eng,req,path,stop:wav(path)); self.launch(synth=synth)
        self.wait(lambda:ipc.read(self.home/'ready.json').get('ready'))
        for n,deadline in enumerate(('bad',float('nan'),float('inf'))):
            identity=str(n)*32; path=self.home/'inbox'/(identity+'.json')
            path.with_suffix('.lease').touch(); ipc.write(path,{'generation':GEN,'deadline':deadline,'format':'wav'})
            self.wait(lambda:not path.exists())
        synth.assert_not_called(); self.assertTrue(ipc.read(self.home/'ready.json')['ready'])

    def test_fifo_and_queued_cancel_preserve_other_requests(self):
        calls=[]; entered=threading.Event(); release=threading.Event()
        def synth(engine,req,path,cancel):
            calls.append(req['text'])
            if len(calls)==1:entered.set(); release.wait(2)
            wav(path)
        self.launch(synth=synth); self.wait(lambda:ipc.read(self.home/'ready.json').get('ready'))
        self.request('f'*32); self.wait(entered.is_set)
        cancelled=self.request('c'*32); cancelled.with_suffix('.cancel').touch()
        earlier=self.request('e'*32);later=self.request('b'*32)
        # Coarse/equal filesystem timestamps must not turn UUID lexicographic
        # order into FIFO. Cancellation still excludes only its own request.
        for path in (cancelled,earlier,later):os.utime(path,ns=(10**18,10**18))
        release.set(); self.wait(lambda:len(list((self.home/'output').glob('*.json')))==3)
        self.assertEqual(calls,['f'*32,'e'*32,'b'*32])
        self.assertFalse(list((self.home/'output').glob('c'*32+'*')))

    def test_watchdog_retires_only_owned_worker_and_fences_late_audio(self):
        entered=threading.Event(); release=threading.Event(); retired=[]
        def synth(engine,req,path,cancel):entered.set(); release.wait(6); wav(path)
        def terminate(code):retired.append(code); self.stop.set(); release.set()
        self.launch(synth=synth,terminate=terminate,cancellation_grace=.15)
        self.wait(lambda:ipc.read(self.home/'ready.json').get('ready'))
        path=self.request('b'*32); self.wait(entered.is_set); path.with_suffix('.cancel').touch()
        self.wait(lambda:bool(retired),timeout=5)
        self.wait(lambda:not (self.home/'ready.json').exists())
        self.assertEqual(retired,[75]); self.assertFalse(list((self.home/'output').iterdir()))
        self.assertEqual(ipc.read(self.home/'state.json')['phase'],'error')

    def test_replacement_generation_artifacts_survive_old_teardown(self):
        foreign=self.home/'output'/('d'*32+'.wav')
        def synth(engine,req,path,cancel):
            wav(path); foreign.write_bytes(b'new generation')
            ipc.write(self.home/'ready.json',{'generation':'d'*32,'ready':True})
        self.launch(synth=synth); self.wait(lambda:ipc.read(self.home/'ready.json').get('ready'))
        self.request('b'*32); self.wait(foreign.exists); self.stop.set()
        self.wait(lambda:not (self.home/'inbox'/('b'*32+'.json')).exists())
        self.assertEqual(foreign.read_bytes(),b'new generation')

    def test_pid_birth_and_generation_must_match_actual_command(self):
        import psutil
        record={'pid':123,'schema':2,'ready':True,'generation':GEN,
            'process_identity':{'adapter':'psutil-v1','pid':123,'birth':10.5}}
        process=SimpleNamespace(cmdline=lambda:['python','speech_worker.py','--home',str(self.home),'--generation',GEN],create_time=lambda:11.5,
            wait=Mock(side_effect=psutil.TimeoutExpired(0)))
        with patch('psutil.Process',return_value=process):
            self.assertIs(voice.worker_live(self.home,record),False)
            record['process_identity']['birth']=11.5
            self.assertIs(voice.worker_live(self.home,record),True)
            record['generation']='b'*32
            self.assertIs(voice.worker_live(self.home,record),False)

    @unittest.skipUnless(os.name=='nt','Windows retained process metadata')
    def test_real_job_stop_waits_for_leader_and_retained_ready_is_not_live(self):
        import psutil
        from localharness import desktop_process as custody
        script=self.root/'speech_worker.py';script.write_text('import time;time.sleep(30)')
        for attempt in range(3):
            child=custody.JobChild.spawn([sys.executable,'-B',str(script),'--home',str(self.home),'--generation',GEN],
                cwd=self.root,env=dict(os.environ),log=self.root/'fixture.log',stopped=lambda:False)
            try:
                record={'schema':2,'pid':child.pid,'generation':GEN,'ready':True,
                    'process_identity':{'adapter':'psutil-v1','pid':child.pid,'birth':psutil.Process(child.pid).create_time()}}
                self.wait(lambda:voice.worker_live(self.home,record) is True)
                child.stop()
                self.assertIs(voice.worker_live(self.home,record),False)
                self.assertIsNone(child.job.handle)
            finally:child.stop()

    def test_installed_availability_is_separate_from_selected_voice_readiness(self):
        model=self.root/'models/xtts';model.mkdir(parents=True)
        for name in ('config.json','model.pth','vocab.json','speakers_xtts.pth'):(model/name).touch()
        cfg={'voice':{'speak':True,'tts_speaker':'A','tts_language':'ru'}}
        choice=ipc.selection({'speaker':'A','language':'ru'},self.root)
        record={'ready':True,'phase':'ready','selection_key':'old voice'}
        with patch.object(voice,'worker_live',return_value=True),patch.object(voice,'speech_library',return_value={'present':True,'why':''}):
            ipc.write(self.home/'ready.json',record);state=voice.speech_state(self.root,cfg)
            self.assertTrue(state['ready']);self.assertFalse(state['worker_ready'])
            self.assertEqual(state['worker_phase'],'warming_voice')
            record.update(phase='busy',selection_key=ipc.selection_key(choice),reference_stamp=None)
            ipc.write(self.home/'ready.json',record);state=voice.speech_state(self.root,cfg)
            self.assertTrue(state['worker_ready']);self.assertEqual(state['worker_phase'],'busy')
            record.update(ready=False,phase='error',error='bad sample')
            ipc.write(self.home/'ready.json',record);state=voice.speech_state(self.root,cfg)
            self.assertTrue(state['ready']);self.assertFalse(state['worker_ready'])
            self.assertEqual(state['worker_error'],'bad sample');self.assertEqual(state['worker_phase'],'error')


if __name__=='__main__':unittest.main()
