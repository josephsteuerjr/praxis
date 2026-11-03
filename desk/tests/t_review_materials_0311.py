"""Complete update review material, with ordinary privacy and size bounds intact."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'desk'), str(ROOT/'desk/tests'), str(ROOT/'helene/core')]
from localharness import self_review, source_merge
from localharness.self_review_worker import SnapshotReviewer
import t_self_review_192 as fixture


class ReviewMaterials(unittest.TestCase):
    setUp = fixture.ReviewTests.setUp

    def strict(self):
        return self_review.Service(self.root/'home', self.code, self.app, self.forge,
            coordinator={'kind':'incoming-update-runtime.v1', 'review_store':'isolated-v1'})

    def materials(self):
        harness=self.root/'composed';harness.mkdir()
        (harness/'main.py').write_bytes(b'VALUE = 1\n')
        assets={'font.woff2':b'\x89FONT\x00\xff','retained.woff2':b'\x89LOCAL\x00\xff',
                'changed.woff2':b'\x89CHANGED\x00\xff','unknown.png':b'\x89UNKNOWN\x00\xff',
                'bad.py':b'\x89CODE\x00\xff'}
        for name,raw in assets.items():(harness/name).write_bytes(raw)
        declared=source_merge.inventory(harness)
        distribution=self.root/'distribution';distribution.mkdir()
        proofs={}
        for name in ('python-package-files.json','source-inputs.json'):
            p=distribution/name;p.write_text(json.dumps({'files':declared}),encoding='utf8')
            proofs[name]=hashlib.sha256(p.read_bytes()).hexdigest()
        evidence={'format':'helene-distribution-evidence.v1','composition_sha256':'c'*64,
            'package_manifest_sha256':proofs['python-package-files.json'],
            'source_inputs_sha256':proofs['source-inputs.json'],
            'package_manifest_ref':'distribution/python-package-files.json',
            'source_inputs_ref':'distribution/source-inputs.json'}
        return {'harness':{'root':str(harness),'merge_files':declared,
            'inventory_scope':'full-composed-app-v1','release_evidence':evidence,
            'published_files':{'font.woff2':[declared['font.woff2']['sha256']]},
            'retained_files':{'retained.woff2':declared['retained.woff2'],
                'changed.woff2':{'sha256':'d'*64,'mode':'100644'},'bad.py':declared['bad.py']},
            'retained_input_sha256':'a'*64},
            'distribution':{'root':str(distribution),'files':{n:n for n in proofs},
                'published_files':{n:[d] for n,d in proofs.items()}}}

    def test_strict_core_and_retained_app_asset_identity_without_binary_waiver(self):
        sources=self.materials()
        for label in ('base','mine','release','resolved'):
            sources[label]={'root':str(self.code),'merge_files':source_merge.inventory(self.code)}
        (self.code/'icon.png').write_bytes(b'\x89PNG\x00\xff')
        for label in ('base','mine','release','resolved'):
            sources[label]['merge_files']=source_merge.inventory(self.code)
            sources[label]['published_files']={'icon.png':[sources[label]['merge_files']['icon.png']['sha256']]}
        service=self.strict();kwargs={'trigger':'contested_overlay','sources':sources,
            'overlay':{'merge_identity':{'composition_sha256':'c'*64}}}
        row=service.request(**kwargs);subject=self_review.validate_snapshot(row['snapshot'],row['subject_sha256'])
        rows={r['path']:r for r in subject['manifest']}
        for label in ('base','mine','release','resolved'):
            self.assertIn('opaque',rows[label+'/icon.png'])
            self.assertNotIn('withheld',rows[label+'/icon.png'])
        retained=rows['harness/retained.woff2']
        self.assertIn('retained local',retained['opaque'])
        self.assertNotIn('published_source',retained)
        self.assertEqual(retained['retained_input_sha256'],'a'*64)
        for name in ('changed.woff2','unknown.png','bad.py'):
            self.assertIn('withheld',rows['harness/'+name])
        reviewer=SnapshotReviewer({'root':row['snapshot'],'subject_sha256':row['subject_sha256']})
        result=json.loads(reviewer.dispatch({},'inspect',{'action':'read','path':'harness/retained.woff2'}))
        self.assertIn('opaque',result);self.assertNotIn('text',result)
        self.assertFalse(service.freshness(row['id'])['stale'])
        ordinary=self.service.request(**kwargs)
        self.assertFalse(any(r.get('opaque') for r in self_review.validate_snapshot(
            ordinary['snapshot'],ordinary['subject_sha256'])['manifest']))
        wrong=copy.deepcopy(kwargs);wrong['overlay']['merge_identity']['composition_sha256']='e'*64
        wrongrow=service.request(**wrong)
        self.assertFalse(any(r.get('opaque') for r in self_review.validate_snapshot(
            wrongrow['snapshot'],wrongrow['subject_sha256'])['manifest']))
        # An executable mode is a different retained asset, even at the same digest.
        changed=copy.deepcopy(kwargs)
        changed['sources']['harness']['retained_files']['retained.woff2']={
            **changed['sources']['harness']['retained_files']['retained.woff2'], 'mode':'100755'}
        other=service.request(**changed)
        item=next(r for r in self_review.validate_snapshot(other['snapshot'],other['subject_sha256'])['manifest']
                  if r['path']=='harness/retained.woff2')
        self.assertIn('withheld',item)

    def test_large_complete_update_keeps_report_and_final_source_readable_and_fresh(self):
        # Real sizes straddle both old limits: four 12MiB sides + a 17MiB report.
        (self.code/'large.py').write_bytes(b'#' + b'x'*(12*1024*1024-2) + b'\n')
        files=source_merge.inventory(self.code)
        sources={label:{'root':str(self.code),'merge_files':files} for label in ('base','mine','release','resolved')}
        battery=self.root/'battery';battery.mkdir()
        report=b'Complete diagnostics\n'+b'x'*(17*1024*1024)
        (battery/'report.md').write_bytes(report)
        sources['battery']={'root':str(battery),'files':{'report.md':'report.md'}}
        kwargs={'trigger':'contested_overlay','sources':sources,
                'overlay':{'merge_identity':{'composition_sha256':'c'*64}}}
        service=self.strict();row=service.request(**kwargs)
        subject=self_review.validate_snapshot(row['snapshot'],row['subject_sha256'])
        self.assertEqual(subject['gaps'],[])
        self.assertEqual((Path(row['snapshot'])/'battery/report.md').read_bytes(),report)
        self.assertEqual((Path(row['snapshot'])/'resolved/large.py').read_bytes(),(self.code/'large.py').read_bytes())
        self.assertFalse(service.freshness(row['id'])['stale'])
        reviewer=SnapshotReviewer({'root':row['snapshot'],'subject_sha256':row['subject_sha256']})
        result=json.loads(reviewer.dispatch({},'inspect',{'action':'read','path':'battery/report.md','end':1}))
        self.assertIn('Complete diagnostics',result['text'])
        ordinary=self.service.request(**kwargs)
        self.assertTrue(ordinary['gaps'])
        self.assertFalse((Path(ordinary['snapshot'])/'battery/report.md').exists())
        (battery/'report.md').write_bytes(report+b'changed\n')
        self.assertTrue(service.freshness(row['id'])['stale'])


if __name__=='__main__':unittest.main()
