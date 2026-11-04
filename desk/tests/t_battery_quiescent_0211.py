"""The serial phase must start from the same seed after parallel copies retire."""
from pathlib import Path
import os,shutil,sys,tempfile,unittest

sys.path[:0]=[os.environ.get('HELENE_GATE_REPAIR_SOURCE',str(Path(__file__).resolve().parents[1]))]
sys.path.insert(1,str(Path(sys.path[0]).parent/'helene/core'))
from localharness import source_update_battery as battery,source_update_parallel as parallel

class QuiescentCopy(unittest.TestCase):
    def test_parallel_then_quiescent_execute_without_deleting_the_source_seed(self):
        work=Path(tempfile.mkdtemp(prefix='helene-update-tests-')).resolve()
        self.addCleanup(shutil.rmtree,work,ignore_errors=True)
        cache=Path(tempfile.mkdtemp(prefix='helene-gate-evidence-')).resolve()
        self.addCleanup(shutil.rmtree,cache,ignore_errors=True)
        repository=work/'repository';(repository/'desk').mkdir(parents=True)
        code="from pathlib import Path; print('ran-private',Path(__file__).read_text().count('ran-private'))\n"
        (repository/'desk/probe.py').write_text(code)
        files=parallel.inventory(battery,repository,lambda:False)
        common={'kind':'script','source':'desk/probe.py','sources':['desk/probe.py'],'root':'desk','argv':[]}
        lanes=[dict(common,id='parallel-probe'),dict(common,id='quiescent-probe',isolation='quiescent')]
        rows,changes,_=parallel.run(battery,lanes,repository,files,work,None,
            {'python':sys.executable},{'verification_executor':{'parallel_lanes':2}},cache,
            lambda:False,{'executor':{}},{})
        self.assertEqual([(r['id'],r['status']) for r in rows],
                         [('parallel-probe','passed'),('quiescent-probe','passed')])
        self.assertEqual(changes,[])
        self.assertEqual((repository/'desk/probe.py').read_text(),code)
        self.assertEqual(len(list(cache.glob('*.lane.json'))),len(lanes))

if __name__=='__main__':unittest.main()
