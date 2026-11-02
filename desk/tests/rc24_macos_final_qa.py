"""One authorized native15/26 source-delta/policy run, no packaging or builds."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import ast,hashlib,importlib,json,os,subprocess,sys,time,unittest
import rc24_macos_qa as prior
import rc23_macos_candidate_qa as base

QA=Path(__file__).resolve();REPO=base.REPO;TEMP=base.TEMP
COMMIT='1851d9f101476023e836d6195d9267d0c4495b3e'
MATERIAL='12e93724a7af683b54d001709885c87f6ccdf77a3e360b3954129bb327b643f4'
OUT=base.WORKSPACE/('evidence-rc24-final-'+os.environ['RC24_LANE']);OUT.mkdir(exist_ok=True)
base.OUT=OUT;base.PRODUCT_COMMIT=COMMIT;base.VERSION='2.3.0-rc.24';base.QA=QA
digest=base.digest;save=base.save
sys.path[:0]=list(map(str,(REPO/'desk',REPO/'desk/installer',REPO/'helene/core',REPO/'praxis')))
os.environ['PYTHONDONTWRITEBYTECODE']='1'

def identity(*,materialize=False):
    base.subject_identity(materialize=materialize)
    subject=json.loads((OUT/'SUBJECT-SOURCE.json').read_text())
    assert hashlib.sha256(json.dumps(subject['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==MATERIAL

def prepare():
    # Reconstruct only cached immutable qualified inputs, never build them.
    prior.qualified();identity()
    python=prior.BASE/'runtime/bin/python3'
    os.execv(str(python),[str(python),'-B',str(QA),'check'])

def inventory(root):return {p.relative_to(root).as_posix():{'sha256':digest(p),'mode':p.stat().st_mode&0o777} for p in root.rglob('*') if p.is_file()}

def source_delta_cases():
    cases=[]
    for name in ('test_memory_history','tests.t_verification_dirfd_230','tests.t_sealed_manifest_budget_0211'):
        module=importlib.import_module(name)
        for cls in vars(module).values():
            if isinstance(cls,type) and issubclass(cls,unittest.TestCase) and cls.__module__==name:
                cases.extend(t.id() for t in unittest.defaultTestLoader.loadTestsFromTestCase(cls))
    for filename,module,selector in [('praxis/test_run_retention.py','test_run_retention','test_bad_open_evidence_state_fails_closed_for_all_candidates'),
            ('helene/core/test_glm_effort.py','test_glm_effort','test_sdk_merges_output_config_at_wire_top_level_create_and_stream')]:
        tree=ast.parse((REPO/filename).read_bytes())
        found=[module+'.'+c.name+'.'+n.name for c in tree.body if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef) and n.name==selector]
        assert len(found)==1;cases.extend(found)
    assert len(cases)==len(set(cases))
    def execute(case):
        work=TEMP/'rc24-final-focused'/hashlib.sha256(case.encode()).hexdigest()[:16];work.mkdir(parents=True)
        env=dict(os.environ,HOME=str(work),CFFIXED_USER_HOME=str(work),TMPDIR=str(work),PYTHONNOUSERSITE='1',
            PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join(map(str,(REPO/'desk',REPO/'desk/installer',REPO/'helene/core',REPO/'praxis'))))
        log=OUT/(case+'.log');start=time.monotonic()
        with log.open('wb') as stream:
            p=subprocess.run([sys.executable,'-B','-m','unittest',case,'-v'],cwd=REPO/'desk',env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=120)
        text=log.read_text(errors='replace')
        return {'id':case,'exit':p.returncode,'seconds':time.monotonic()-start,'log':log.name,
            'passed':p.returncode==0 and 'Ran 1 test' in text and 'skipped' not in text.lower()}
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 1) as pool:rows=list(pool.map(execute,cases))
    result={'source_commit':COMMIT,'platform':sys.platform,'macos':subprocess.check_output(['sw_vers','-productVersion'],text=True).strip(),
        'unique':len(cases),'workers':os.cpu_count(),'outcomes':rows,'passed':all(r['passed'] for r in rows),
        'scope':'Only new9path delta: POSIX memory-history positive/refusal, audit-dirfd/OS mock, sealed budgets/links, changed retention and SDK child. Prior healthy53 not repeated.'}
    save('FINAL-DELTA-TARGETED.json',result);return result

def native_policy():
    import rc24_platform_guard_qa as collector
    from portable_sources import compose_core
    assert digest(QA.with_name('rc24_platform_guard_qa.py'))=='64510e10f746ca7b13aa9c2e2d2ebf40e303b157c25212eaebdeeb72ba4e431c'
    expected=json.loads(QA.with_name('rc24_darwin_policy_expected.json').read_text())
    assert expected['source_commit']==COMMIT and expected['material_sha256']==MATERIAL
    core,_=compose_core(REPO)
    candidates=[(n,r.data,'core') for n,r in core.items() if Path(n).name.startswith('test') and n.endswith('.py')]
    candidates.extend((p.relative_to(REPO).as_posix(),p.read_bytes(),p.relative_to(REPO).as_posix())
        for p in (REPO/'desk/tests').rglob('*.py') if p.name.startswith(('t_','test')))
    rows=[];unknown=[]
    for n,raw,lane in candidates:
        try:r,u=collector.inspect(n,raw,lane)
        except (SyntaxError,UnicodeError):continue
        rows.extend(r);unknown.extend(u)
    policy={r['key']:r['reason'] for r in rows};assert len(policy)==len(rows)
    exact={r['key']:r for r in rows}
    for row in expected['evidence']:
        assert exact[row['key']]==row,row['key']
    assert all(policy[k]==v for k,v in expected['policy'].items())
    extra=[r for r in rows if r['key'] not in expected['policy']]
    facts={'sys.platform':sys.platform,'os.name':os.name,'machine':os.uname().machine,
        'macos':subprocess.check_output(['sw_vers','-productVersion'],text=True).strip(),
        'capabilities':{n:hasattr(os,n) for n in ('fork','WNOHANG','geteuid','O_NOFOLLOW','O_DIRECTORY','O_NONBLOCK')},
        'proc_directory_exists':Path('/proc').is_dir()}
    assert facts['sys.platform']=='darwin' and facts['os.name']=='posix' and facts['machine']=='arm64'
    result={'format':'helene-native-darwin-policy-observation.v1','source_commit':COMMIT,'material_sha256':MATERIAL,
        'native_facts':facts,'proposal_only':True,'policy_not_installed':True,
        'reviewed_OS_only_policy':expected['policy'],'reviewed_OS_only_evidence':expected['evidence'],
        'all_OS_only_cases_native_match':True,'native_collected_policy':policy,'native_collected_evidence':rows,
        'extra_native_capability_rows_require_separate_review':extra,'unknown_guards_unapproved':unknown,
        'no_test_module_imports_for_policy':True,'no_tests_executed_by_collector':True,
        'no_network_models_component_or_audit_waiver':True}
    save('NATIVE-DARWIN-POLICY.json',result)

def check():
    identity();runtime=prior.BASE/'runtime';before=inventory(runtime)
    native_policy();result=source_delta_cases();identity()
    after=inventory(runtime);save('RUNTIME-EXACT-RETAINED.json',{'passed':before==after,'files':len(before),
        'qualified_base_archive_sha256':prior.OLD_SHA,'no_build_or_dependency_resolution':True})
    assert before==after,'Qualified Runtime changed'
    assert result['passed'],[r for r in result['outcomes'] if not r['passed']]

if __name__=='__main__':globals()[sys.argv[1]]()
