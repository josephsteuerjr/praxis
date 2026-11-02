"""One new required SQLite unit plus current native policy facts; no builds."""
from pathlib import Path
import ast,hashlib,json,os,subprocess,sys,time
import rc24_macos_final_qa as previous

base=previous.base;QA=Path(__file__).resolve();REPO=base.REPO;TEMP=base.TEMP
EXPECTED=json.loads(QA.with_name('rc24_spool_policy_expected.json').read_text())
COMMIT=EXPECTED['source_commit'];MATERIAL=EXPECTED['material_sha256']
OUT=base.WORKSPACE/('evidence-rc24-spool-'+os.environ['RC24_LANE']);OUT.mkdir(exist_ok=True)
base.OUT=OUT;base.PRODUCT_COMMIT=COMMIT;base.VERSION='2.3.0-rc.24';base.QA=QA
digest=base.digest;save=base.save
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.path[:0]=list(map(str,(REPO/'desk',REPO/'desk/installer',REPO/'helene/core',REPO/'praxis')))

def identity():
    base.subject_identity()
    d=json.loads((OUT/'SUBJECT-SOURCE.json').read_text())
    assert hashlib.sha256(json.dumps(d['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==MATERIAL

def prepare():
    previous.prior.qualified();identity()
    python=previous.prior.BASE/'runtime/bin/python3';os.execv(str(python),[str(python),'-B',str(QA),'check'])

def policy():
    import rc24_platform_guard_qa as collector
    from portable_sources import compose_core
    assert digest(QA.with_name('rc24_platform_guard_qa.py'))=='64510e10f746ca7b13aa9c2e2d2ebf40e303b157c25212eaebdeeb72ba4e431c'
    core,_=compose_core(REPO)
    candidates=[(n,r.data,'core') for n,r in core.items() if Path(n).name.startswith('test') and n.endswith('.py')]
    candidates.extend((p.relative_to(REPO).as_posix(),p.read_bytes(),p.relative_to(REPO).as_posix())
        for p in (REPO/'desk/tests').rglob('*.py') if p.name.startswith(('t_','test')))
    rows=[];unknown=[]
    for n,raw,lane in candidates:
        try:r,u=collector.inspect(n,raw,lane)
        except (SyntaxError,UnicodeError):continue
        rows.extend(r);unknown.extend(u)
    observed={r['key']:r['reason'] for r in rows};by_key={r['key']:r for r in rows}
    assert len(observed)==len(rows)
    for row in EXPECTED['evidence']:assert by_key[row['key']]==row,row['key']
    assert all(observed[k]==v for k,v in EXPECTED['policy'].items())
    facts={'sys.platform':sys.platform,'os.name':os.name,'machine':os.uname().machine,
        'macos':subprocess.check_output(['sw_vers','-productVersion'],text=True).strip(),
        'proc_directory_exists':Path('/proc').is_dir(),'HELENE_BODY_DIR_declared':bool(os.environ.get('HELENE_BODY_DIR')),
        'capabilities':{n:hasattr(os,n) for n in ('fork','WNOHANG','geteuid','O_NOFOLLOW','O_DIRECTORY','O_NONBLOCK')}}
    assert facts['sys.platform']=='darwin' and facts['os.name']=='posix' and facts['machine']=='arm64'
    extra=[r for r in rows if r['key'] not in EXPECTED['policy']]
    save('NATIVE-DARWIN-POLICY.json',{'source_commit':COMMIT,'source_material_sha256':MATERIAL,'native_facts':facts,
        'proposal_only':True,'policy_not_installed':True,'OS_only_policy':EXPECTED['policy'],'OS_only_evidence':EXPECTED['evidence'],
        'all_OS_only_cases_native_match':True,'native_collected_policy':observed,'native_collected_evidence':rows,
        'extra_native_rows_require_review':extra,'unknown_guards_unapproved':unknown,
        'additional_context_proposal':EXPECTED['additional_context_proposal'],
        'additional_context_facts_match':not facts['proc_directory_exists'] and not facts['HELENE_BODY_DIR_declared'],
        'additional_context_rules_NOT_automatically_approved':True,'no_test_modules_imported_for_policy':True})

def spool():
    path=REPO/'desk/tests/t_body.py';tree=ast.parse(path.read_bytes())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Spool');assert not cls.decorator_list
    names=[n.name for n in cls.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test')];assert len(names)==1
    case='tests.t_body.'+cls.name+'.'+names[0]
    work=TEMP/'only-spool';work.mkdir()
    env=dict(os.environ,HOME=str(work),CFFIXED_USER_HOME=str(work),TMPDIR=str(work),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=os.pathsep.join(map(str,(REPO/'desk',REPO/'desk/installer',REPO/'helene/core',REPO/'praxis'))))
    env.pop('HELENE_BODY_DIR',None)
    code="""import importlib,json,subprocess,sys,unittest
from pathlib import Path
from unittest.mock import patch
m=importlib.import_module('tests.t_body')
with patch.object(m,'_built_pair',side_effect=AssertionError('Native fixture forbidden')),patch.object(subprocess,'Popen',side_effect=AssertionError('Native process forbidden')):
 r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName(sys.argv[1]))
 value={'case':sys.argv[1],'run':r.testsRun,'passed':r.wasSuccessful(),'skipped':[(t.id(),why) for t,why in r.skipped],'native_fixture_or_process_execution_forbidden':True}
 Path(sys.argv[2]).write_text(json.dumps(value,indent=2)+'\\n')
 raise SystemExit(not (r.wasSuccessful() and r.testsRun==1 and not r.skipped))
"""
    with (OUT/'spool-unit.log').open('wb') as f:
        p=subprocess.run([sys.executable,'-B','-c',code,case,str(OUT/'SPOOL-UNIT.json')],cwd=REPO/'desk',env=env,stdout=f,stderr=subprocess.STDOUT,timeout=60)
    assert p.returncode==0

def check():
    identity();runtime=previous.prior.BASE/'runtime';before=previous.inventory(runtime)
    policy();spool();identity();after=previous.inventory(runtime)
    save('RUNTIME-EXACT-RETAINED.json',{'passed':before==after,'files':len(before),'no_build_or_package_or_dependency_resolution':True,
        'prior_native32_and_53_retained_not_repeated':True})
    assert before==after

if __name__=='__main__':globals()[sys.argv[1]]()
