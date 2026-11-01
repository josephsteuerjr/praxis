"""Execute the real source-event adapter before a model-input snapshot is built."""
from pathlib import Path
import ast,datetime as dt,unittest
task=Path(__file__).resolve().parents[1]/"localharness"

class WorkEvent(unittest.TestCase):
 def test_real_current_event_precedes_turn_and_keeps_system_speaker(self):
  source=(task/'runner.py').read_text(encoding='utf8');module=ast.parse(source)
  node=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='_source_update_turn')
  archive={};life={};order=[];received=[]
  class Desk:
   def archive(self,text,**args):
    self.assert_system(args);archive[args['source_id']]=(text,args);order.append('archive')
   def assert_system(self,args):
    if args['outgoing'] or args['sender']!='Hélène' or args.get('system'):raise AssertionError('Do not forge an owner message or hide work from the model')
   def life(self,text,**args):life[args['source_id']]=(text,args);order.append('life')
  desk=Desk();moment=dt.datetime.now(dt.timezone.utc)
  def turn(source_id,**args):
   order.append('turn');received.append(life[source_id][0]);return 'silent'
  env={'_now':lambda:moment,'_room':lambda stream:desk,'STREAM':'window','_turn_in_window':turn}
  exec(compile(ast.Module(body=[node],type_ignores=[]),str(task/'runner.py'),'exec'),env)
  note='Current authorized update: resolve this exact candidate, then finish.'
  for _ in range(2):self.assertEqual(env['_source_update_turn']('exact-work-event',speaker='Hélène',origin_text=note),'silent')
  self.assertEqual(received,[note,note]);self.assertEqual(len(archive),1);self.assertEqual(len(life),1)
  self.assertEqual(order,['archive','life','turn']*2)
 def test_idle_update_path_uses_the_event_adapter(self):
  module=ast.parse((task/'runner.py').read_text(encoding='utf8'))
  node=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='_update_report_due')
  calls=[n for n in ast.walk(node) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='notify_due']
  self.assertEqual(len(calls),1);self.assertEqual(calls[0].args[1].id,'_source_update_turn')

if __name__=='__main__':unittest.main()
