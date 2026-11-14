"""Semantic evidence, live revision pages, cache privacy and owned execution."""
import contextvars
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import code_navigation as navigation
import code_query
import code_query_cache
import code_query_facts
import forge_intelligence as intelligence


class Evidence(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        code_query._SNAPSHOTS.clear();code_query_cache._MEMORY.clear()
    def put(self,name,text):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(text.encode());return p
    def query(self,op='callers',name='transform',module='engine',**kw):
        request={'op':op,'root':{'path':str(self.root),'kind':'external'},'name':name,'module':module,'path':'','limit':200,'offset':0,**kw}
        return navigation.execute(request)
    def toy(self):
        self.put('engine.py','def transform(x):\n    return x\ntransform(1)\n')
        self.put('use.py','from engine import transform as apply\nimport other\nimport engine as e\napply(1)\nother.transform(2)\nobj.transform(3)\ne.transform(4)\nimportlib.import_module("engine").transform(5)\n')
    def test_bound_alias_other_module_and_unknown_receivers(self):
        self.toy();r=self.query();rows=r['rows']
        self.assertTrue(r['complete'],r)
        alias=next(x for x in rows if x['name']=='apply')
        self.assertEqual((alias['evidence'],alias['ambiguous'],alias['matches_target']),('import_bound_alias',False,True))
        other=next(x for x in rows if x.get('receiver')=='other')
        self.assertEqual((other['evidence'],other['matches_target']),('module_attribute',False))
        for receiver in ('obj','<expression>'):
            row=next(x for x in rows if x.get('receiver')==receiver)
            self.assertEqual(row['evidence'],'attribute_receiver_unknown');self.assertTrue(row['ambiguous'])
        self.assertTrue(r['coverage']['dynamic_imports'])
        required={'path','file_sha256','start','end','name','qualname','kind','evidence','method','ambiguous','snippet'}
        self.assertTrue(all(required<=x.keys() for x in rows))
        self.assertTrue(all(required<=x.keys() for x in self.query('references')['rows']))
    def test_scope_shadowing_is_not_import_evidence(self):
        self.put('use.py','from engine import transform\ndef a(transform):\n    transform()\ndef b():\n    transform()\n    transform = other\ndef c():\n    if flag:\n        from engine import transform\n    transform()\n')
        rows=self.query()['rows'];self.assertEqual(len(rows),3)
        self.assertTrue(all(x['ambiguous'] and x['evidence']=='name_match_other_scope' for x in rows),rows)
    def test_relative_imports_and_invalid_parent(self):
        self.put('pkg/use.py','from .engine import transform as alias\nalias()\n')
        self.put('bad.py','from ..engine import transform\ntransform()\n')
        rows=self.query(module='pkg.engine')['rows']
        self.assertTrue(next(x for x in rows if x['path']=='pkg/use.py')['matches_target'])
        self.assertTrue(next(x for x in rows if x['path']=='bad.py')['ambiguous'])
    def test_deleted_exception_and_pattern_names_are_ambiguous(self):
        self.put('a.py','from engine import transform\ndel transform\ntransform()\n')
        self.put('b.py','from engine import transform\ntry:\n    x()\nexcept Exception as transform:\n    transform()\n')
        self.put('c.py','from engine import transform\nmatch obj:\n    case {"x": transform}:\n        transform()\n')
        self.assertTrue(all(r['ambiguous'] for r in self.query()['rows']))
    def test_class_chain_is_unknown_but_explicit_submodule_is_known(self):
        self.put('a.py','import engine\nimport engine.sub\nengine.C.transform()\nengine.sub.transform()\n')
        # Duplicate binding of engine is conservative too; use separate files.
        self.put('a.py','import engine\nengine.C.transform()\n')
        self.put('b.py','import engine.sub\nengine.sub.transform()\n')
        rows=self.query(module='engine.sub')['rows']
        self.assertEqual(next(r for r in rows if r['path']=='a.py')['evidence'],'attribute_receiver_unknown')
        self.assertTrue(next(r for r in rows if r['path']=='b.py')['matches_target'])
    def test_decorated_nested_methods_and_callees(self):
        self.put('engine.py','@decorate(transform())\ndef transform():\n    first()\n    def nested():\n        second()\n    return first\nclass C:\n    def method(self):\n        self.transform()\n')
        callees=self.query('callees')['rows']
        self.assertEqual([x['name'] for x in callees],['first'])
        defs=self.query('symbols',name='',module='')['rows']
        self.assertIn('transform.nested',{x['qualname'] for x in defs})
        self.assertIn('C.method',{x['qualname'] for x in defs})
    def test_two_definitions_are_separate(self):
        self.put('engine.py','def transform(): pass\n');self.put('other.py','def transform(): pass\n')
        rows=self.query('definition')['rows'];self.assertEqual(len(rows),2)
        self.assertEqual({x['matches_target'] for x in rows},{True,False})
    def test_comments_literals_are_mentions_not_calls_and_not_cached(self):
        secret='private literal marker transform'
        self.put('a.py','# transform comment\ns = '+repr(secret)+'\n')
        cache=self.root/'memory/.cache/code-query'
        r=self.query('references',cache_dir=str(cache))
        self.assertEqual([x['evidence'] for x in r['rows']],['text_mention','text_mention'])
        self.assertNotIn(secret,' '.join(p.read_text() for p in cache.glob('*/*.json')))
        code_query_cache._MEMORY.clear()
        warm=self.query('references',cache_dir=str(cache))
        self.assertEqual(warm['cache']['hits'],1);self.assertEqual(warm['rows'],r['rows'])
    def test_broken_candidate_keeps_other_results_and_incomplete(self):
        self.put('good.py','transform()\n');self.put('bad.py','def transform(:\n')
        r=self.query();self.assertFalse(r['complete']);self.assertEqual(r['total'],1)
        self.assertEqual(r['coverage']['failed'][0]['reason'],'SyntaxError')
    def test_prefilter_and_unicode_nfkc(self):
        self.put('unrelated.py','x=1\n');self.put('u.py','def K(): pass\nK()\n')
        r=self.query(name='K',module='u');self.assertEqual(r['total'],1);self.assertEqual(r['coverage']['prefiltered'],1)
    def test_unicode_crlf_columns(self):
        self.put('u.py','π = 1; transform()\r\n')
        row=self.query()['rows'][0]
        self.assertEqual(row['start'],{'line':1,'char':7,'byte':8})
        self.assertNotIn('\r',row['snippet'])
    def test_javascript_regex_and_missing_call_grammar(self):
        self.put('a.ts',"export const transform = () => 1;\r\n")
        r=self.query('symbols');self.assertEqual(r['rows'][0]['method'],'regex')
        r=self.query();self.assertFalse(r['complete']);self.assertIn('typescript',r['coverage']['missing_grammars'])
    def test_gitignored_nested_and_outside_symlink_disclosed(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        self.put('.gitignore','ignored.py\n');self.put('ignored.py','transform()')
        self.put('a.py','transform()');(self.root/'nested/.git').mkdir(parents=True);self.put('nested/x.py','transform()')
        outside=self.root.parent/(self.root.name+'-outside.py');outside.write_text('transform()');self.addCleanup(outside.unlink)
        try:(self.root/'escape.py').symlink_to(outside)
        except OSError:self.skipTest('symlinks unavailable')
        r=self.query();reasons={x['reason'] for x in r['coverage']['skipped']}
        self.assertTrue({'gitignored','nested_repository','symlink_outside_root'}<=reasons,reasons)
        self.assertEqual([x['path'] for x in r['rows']],['a.py'])
    def test_budget_empty_answer_is_incomplete(self):
        self.put('a.py','x=1');self.put('b.py','transform()')
        r=navigation.execute({'op':'callers','root':{'path':str(self.root)},'name':'transform','module':'engine'},budget=intelligence.Budget(files=1))
        self.assertFalse(r['complete']);self.assertIsNone(r['next_cursor'])
    def test_mutation_during_parse_refuses_mixed_snapshot(self):
        p=self.put('a.py','transform()\n')
        original=code_query_cache.get
        def mutate(*args,**kw):
            value=original(*args,**kw);p.write_text('transform(1)\n');return value
        with patch.object(code_query_cache,'get',side_effect=mutate):r=self.query()
        self.assertEqual(r['error'],'snapshot_changed');self.assertEqual(r['changed_paths'],['a.py'])
    def test_deadline_and_path_escape_are_explicit(self):
        self.put('a.py','transform()\n')
        budget=intelligence.Budget(seconds=.001);budget.deadline=time.monotonic()-1
        r=navigation.execute({'op':'callers','root':{'path':str(self.root)},'name':'transform'},budget=budget)
        self.assertFalse(r['complete']);self.assertIsNone(r['next_cursor'])
        self.assertEqual(code_query.query('symbols',str(self.root),path='../')['error'],'path_outside_root')
        self.assertEqual(code_query.query('symbols',str(self.root),limit=201)['error'],'limit_must_be_1_to_200')
    def test_overview_and_impact_share_inventory_and_disclose_heuristic(self):
        self.put('engine.py','def transform(): pass\n');self.put('use.py','import engine\n')
        r=self.query('impact',name='',module='',path='engine.py')
        self.assertIn('use.py',{x['path'] for x in r['rows']})
        self.assertTrue(r['detail']['verification']['full'])
        self.assertTrue(all(x['method']=='heuristic' for x in r['rows']))
        r=self.query('overview',name='',module='');self.assertTrue(r['complete'],r)
        self.assertEqual(r['rows'][0]['files'],2)
        self.put('sub/only.py','x=1\n')
        r=self.query('overview',name='',module='',path='sub')
        self.assertEqual([(x['path'],x['files']) for x in r['rows']],[('sub',1)])
    def test_page_file_mutation_same_stat_and_addition_refused(self):
        self.toy()
        first=code_query.query('callers',str(self.root),name='transform',module='engine',limit=1)
        self.assertTrue(first['next_cursor'],first)
        p=self.root/'engine.py';stat=p.stat();p.write_text(p.read_text().replace('return x','return 1'));os.utime(p,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        second=code_query.query('callers',str(self.root),name='transform',module='engine',limit=1,cursor=first['next_cursor'])
        self.assertEqual(second['error'],'stale_cursor');self.assertIn('engine.py',second['changed_paths'])
        self.assertTrue({'seen','parsed','failed','skipped','budget','complete'}<=second['coverage'].keys())
        self.assertEqual(second['coverage']['parsed'],0)
        first=code_query.query('callers',str(self.root),name='transform',module='engine',limit=1)
        self.put('new.py','transform()')
        second=code_query.query('callers',str(self.root),name='transform',module='engine',cursor=first['next_cursor'])
        self.assertEqual(second['error'],'stale_cursor');self.assertIn('new.py',second['changed_paths'])
    def test_page_complete_no_duplicates_and_query_binding(self):
        self.toy();rows=[];cursor=''
        while True:
            r=code_query.query('callers',str(self.root),name='transform',module='engine',limit=2,cursor=cursor)
            self.assertNotIn('error',r);rows+=r['rows'];cursor=r['next_cursor']
            if not cursor:break
        self.assertEqual(len(rows),6)
        first=code_query.query('callers',str(self.root),name='transform',module='engine',limit=1)
        bad=code_query.query('references',str(self.root),name='transform',module='engine',cursor=first['next_cursor'])
        self.assertEqual(bad['error'],'cursor_expired_or_query_changed')
        code_query._SNAPSHOTS.clear()
        bad=code_query.query('callers',str(self.root),name='transform',module='engine',cursor=first['next_cursor'])
        self.assertEqual(bad['error'],'cursor_expired_or_query_changed')
    def test_external_has_no_disk_cache_or_registration_and_remote_no_rpc(self):
        import forge
        self.put('a.py','transform()')
        with patch.object(forge,'create',side_effect=AssertionError('no create')) if hasattr(forge,'create') else patch.object(forge,'_remote_client',side_effect=AssertionError('no RPC')):
            r=code_query.query('callers',str(self.root),name='transform')
        self.assertFalse(r['cache']['persistent']);self.assertFalse((self.root/'memory').exists())
        with patch.object(forge,'_task_root',return_value=({'scope':'host'},Path('/remote'),'')),patch.object(forge,'_remote_client',side_effect=AssertionError('no RPC')):
            r=code_query.query('callers','task:remote',name='transform')
            self.assertEqual(r['error'],'unsupported_remote_task')
            self.assertIn('unsupported_remote_task',str(forge.inspect('remote','callers',query='transform')))
    def test_forge_and_worker_share_engine(self):
        import forge,forge_worker
        self.put('a.py','transform()')
        task={'scope':'local','root':str(self.root)}
        with patch.object(forge,'_task_root',return_value=(task,self.root,'')),patch.object(forge,'BASE',self.root):
            r=json.loads(str(forge.inspect('fixture','callers',query='transform')))
            w=json.loads(str(forge_worker._dispatch({'task_id':'fixture','role':'reviewer'},'inspect',{'action':'callers','query':'transform'})))
        self.assertEqual(r['rows'],w['rows'])
    def test_schema_and_every_audience_capability(self):
        # Native agent import installs its pyhands path for child programs.
        # Standalone execution must restore that import-time environment change.
        with patch.dict(os.environ):
            import agent,forge_worker
        from catalog_families import CODE_QUERY_TOOL
        self.assertEqual(set(CODE_QUERY_TOOL['input_schema']['properties']),{'op','root','path','name','module','limit','cursor'})
        self.assertTrue(callable(agent.TOOL_IMPL['code_query']))
        for catalog in (agent.OWNER_TOOLS,agent.PRAXIS_SELF_TOOLS):
            self.assertEqual(sum(t['name']=='code_query' for t in catalog),1)
        self.assertIn('code_query',agent.HAND_PURPOSE)
        self.assertIn('code_query',[n for _,ns in agent.HAND_GROUPS for n in ns])
        self.assertTrue({'definition','callers','callees'}<=set(forge_worker.INSPECT_TOOL['input_schema']['properties']['action']['enum']))


@unittest.skipUnless(sys.platform.startswith('linux'),'Linux owned semantic search')
class Owned(unittest.TestCase):
    def setUp(self):
        import test_step_control_1311 as fixture
        fixture.ForceControl.setUp(self)
    def stop_all(self):
        import test_step_control_1311 as fixture
        return fixture.ForceControl.stop_all(self)
    def cancel(self):
        import test_step_control_1311 as fixture
        return fixture.ForceControl.cancel(self)
    def wait_for(self,fn):
        import test_step_control_1311 as fixture
        return fixture.ForceControl.wait_for(self,fn)
    def process(self):
        with self.registry.lock:ps=list(self.registry.handles.get(self.ctx.run_id,()))
        return next((p for p in ps if type(p).__name__=='CommandProcess'),None)
    def test_real_owned_pages_cross_child_lifetimes(self):
        import run_context
        (self.base/'a.py').write_text('def f(): pass\nf()\nf()\n')
        with patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.base)}),run_context.bind_run(self.ctx):
            first=code_query.query('callers',str(self.base),name='f',limit=1)
            second=code_query.query('callers',str(self.base),name='f',limit=1,cursor=first['next_cursor'])
        self.assertNotIn('error',second,second);self.assertNotEqual(first['rows'][0]['start'],second['rows'][0]['start'])
    def test_reset_reaps_semantic_inventory_and_its_git_child(self):
        import run_context,step_control
        bindir=self.base/'bin';bindir.mkdir()
        git=bindir/'git';git.write_text('#!/bin/sh\ncase "$*" in *--show-toplevel*) echo "'+str(self.base)+'";; *) sleep 120;; esac\n');git.chmod(0o755)
        (self.base/'a.py').write_text('f()')
        with patch.dict(os.environ,{'PRAXIS_TEST':'','PRAXIS_BASE':str(self.base),'PATH':str(bindir)+os.pathsep+os.environ['PATH']}),run_context.bind_run(self.ctx):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(contextvars.copy_context().run,lambda:code_query.query('callers',str(self.base),name='f'))
                self.wait_for(lambda:self.process() is not None);process=self.process();time.sleep(.3)
                self.assertFalse(future.done());self.cancel()
                with self.assertRaises(step_control.StepCancelled):future.result(3)
                self.assertIsNotNone(process.poll())


if __name__=='__main__':unittest.main()
