from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import operation_diagnostics as diagnostics

class Errors(unittest.TestCase):
 def test_failure_records_frames_and_redacts_config_and_request_credentials(self):
  with tempfile.TemporaryDirectory() as raw:
   root=Path(raw)/'Program';root.mkdir();data=Path(raw)/'Profile'
   (root/'helene.json').write_text(json.dumps({'tree':str(data),'model':{'key':'config-secret-fixture'}}))
   try:raise ValueError('config-secret-fixture and request-secret-fixture were rejected')
   except ValueError as error:path=diagnostics.record(root,'component_install',error,values={'password':'request-secret-fixture','arbitrary_text':'Do not persist user text'})
   text=path.read_text();self.assertNotIn('config-secret-fixture',text);self.assertNotIn('request-secret-fixture',text)
   self.assertNotIn('Do not persist user text',text);value=json.loads(text)['errors'][-1]
   self.assertEqual(value['operation'],'component_install');self.assertEqual(value['error_type'],'ValueError')
   self.assertTrue(value['frames']);self.assertNotIn('source',value['frames'][0])
 def test_error_history_is_bounded_and_lives_in_data(self):
  with tempfile.TemporaryDirectory() as raw:
   root=Path(raw)/'Program';root.mkdir();data=Path(raw)/'Profile'
   (root/'helene.json').write_text(json.dumps({'tree':str(data)}))
   for index in range(40):path=diagnostics.record(root,'update_start',RuntimeError(str(index)))
   value=json.loads(path.read_text());self.assertEqual(len(value['errors']),32)
   self.assertEqual(value['errors'][-1]['error'],'39');self.assertTrue(path.is_relative_to(data))

if __name__=='__main__':unittest.main()
