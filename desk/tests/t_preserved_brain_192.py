"""Upgrade projection -> the actual retained pre-alias normalizer, no code replacement."""
import os,sys,json,tempfile,importlib.util,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];os.environ['PRAXIS_TEST']='1'
sys.path[:0]=[str(ROOT/'desk'),str(ROOT/'praxis')]
import _sandbox
_sandbox.activate_if_testing()
from localharness import boot
spec=importlib.util.spec_from_file_location('legacy_brain_consumer',ROOT/'praxis/llm.py')
legacy=importlib.util.module_from_spec(spec);sys.modules[spec.name]=legacy;spec.loader.exec_module(legacy)
class Preserved(unittest.TestCase):
    def test_old_openai_only_normalizer_receives_legacy_role_and_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);code=root/'code';code.mkdir();(code/'llm.py').write_bytes((ROOT/'praxis/llm.py').read_bytes())
            cfg={'model':{'framework':'openai','base_url':'http://127.0.0.1:59999/v1','key':'TEST-ONLY','model':'primary'}}
            before=(code/'llm.py').read_bytes()
            boot.project_brain(root/'data',cfg,code_dir=code)
            projected=json.loads((root/'data/memory/llm.json').read_text(encoding='utf-8'))
            actual=legacy._normalize(projected)
            self.assertEqual(actual['roles']['voice']['framework'],'openai');self.assertEqual(actual['roles']['evaluator']['framework'],'openai')
            self.assertEqual(actual['frameworks']['openai']['api_key'],'TEST-ONLY')
            self.assertEqual((code/'llm.py').read_bytes(),before)
    def test_declared_updated_consumer_keeps_independent_aliases(self):
        cfg={'model':{'framework':'openai','key':'first','model':'one','fallback_model':'two','fallback_base_url':'https://other.invalid/v1','fallback_key':'second'}}
        built=boot._brain_config(cfg)
        self.assertIs(boot._consumer_brain_config(built,ROOT/'helene/core'),built)
        with tempfile.TemporaryDirectory() as tmp:
            code=Path(tmp);(code/'llm.py').write_text('# old consumer\n')
            with self.assertRaisesRegex(ValueError,'independent same-protocol'):boot._consumer_brain_config(built,code)
    def test_explicit_responses_is_not_silently_downgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);code=root/'code';code.mkdir();(code/'llm.py').write_text('# legacy consumer\n')
            with self.assertRaisesRegex(ValueError,'Responses'):
                boot.project_brain(root/'data',{'model':{'framework':'openai','key':'fixture','api_mode':'responses'}},code_dir=code)
            self.assertFalse((root/'data/memory/llm.json').exists())
    def test_hot_watch_projects_for_actually_loaded_old_source(self):
        import ast,logging,types
        from unittest.mock import patch
        from localharness import brain_trail
        syntax=ast.parse((ROOT/'desk/localharness/runner.py').read_text(encoding='utf-8'))
        function=next(n for n in syntax.body if isinstance(n,ast.FunctionDef) and n.name=='_config_watch_forever')
        class End(BaseException):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);code=root/'loaded-old-code';code.mkdir();(code/'llm.py').write_text('# old consumer\n')
            config=root/'helene.json';cfg={'model':{'framework':'openai','key':'fixture','model':'before'},'code':str(root/'NEW-BUT-NOT-LOADED')};config.write_text(json.dumps(cfg))
            ticks=[]
            def sleep(_):
                if ticks:raise End()
                ticks.append(True);cfg['model']['model']='after';config.write_text(json.dumps(cfg));os.utime(config,ns=(config.stat().st_atime_ns,config.stat().st_mtime_ns+1000000))
            namespace={'Path':Path,'time':types.SimpleNamespace(sleep=sleep),'boot':boot,'json':json,'log':logging.getLogger(),'_CONFIG_WATCH_SEC':0,'_bot':None,'_agent':None}
            exec(compile(ast.Module(body=[function],type_ignores=[]),'runner.py','exec'),namespace)
            with patch.dict(sys.modules,{'brain_trail':brain_trail}):
                with self.assertRaises(End):namespace['_config_watch_forever'](config,root/'data',code)
            actual=legacy._normalize(json.loads((root/'data/memory/llm.json').read_text(encoding='utf-8')))
            self.assertEqual(actual['roles']['voice']['framework'],'openai');self.assertEqual(actual['roles']['voice']['model'],'after')
    def test_unreadable_consumer_does_not_write_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(RuntimeError,'unreadable'):boot.project_brain(root,{'model':{'key':'fixture'}},code_dir=root/'absent')
            self.assertFalse((root/'memory/llm.json').exists())
if __name__=='__main__':unittest.main()
