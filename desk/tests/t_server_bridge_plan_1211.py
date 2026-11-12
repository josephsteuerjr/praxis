import contextlib,json,sys,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import server_bridge_remote as remote

REQUEST={'id':'a'*32,'proxy':True,'https':True,'domain':'helene.example.org','server_addresses':['203.0.113.5']}


class Plan(unittest.TestCase):
    def inspect(self,*,ports=(),hosts=(),dns='203.0.113.5',active=True,action=remote.inspect):
        calls=[]
        def command(argv,**kwargs):
            calls.append(argv)
            if argv==['ss','-ltnH']:return 0,'\n'.join(f'LISTEN 0 10 0.0.0.0:{port} 0.0.0.0:*' for port in ports)
            if argv==['systemctl','is-active','caddy']:return (0,'active') if active else (3,'inactive')
            if argv==['systemctl','is-active','ssh']:return 0,'active'
            if argv==['/usr/bin/fixture','-t']:return 0,''
            if argv[:3]==['systemctl','show','caddy']:return 0,'/usr/bin/caddy run --config /etc/caddy/Caddyfile'
            if argv[:2]==['caddy','adapt']:return 0,json.dumps({'apps':{'http':{'routes':[{'match':[{'host':list(hosts)}]}]}}})
            if argv[:2]==['getent','passwd']:return 2,''
            raise AssertionError('Unexpected remote command: '+repr(argv))
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(remote.sys,'platform','linux'))
            stack.enter_context(mock.patch.object(remote.os,'geteuid',return_value=0,create=True))
            stack.enter_context(mock.patch.object(remote,'command',side_effect=command))
            stack.enter_context(mock.patch.object(remote,'_bytes',side_effect=lambda path,**kwargs:b'ID=debian\n' if path.as_posix()=='/etc/os-release' else b'foreign.example.org { respond "keep" }'))
            stack.enter_context(mock.patch.object(remote.shutil,'which',return_value='/usr/bin/fixture'))
            stack.enter_context(mock.patch.object(Path,'is_dir',return_value=True))
            stack.enter_context(mock.patch.object(Path,'is_symlink',return_value=False))
            stack.enter_context(mock.patch.object(Path,'exists',return_value=False))
            stack.enter_context(mock.patch.object(remote.socket,'getaddrinfo',return_value=[(2,1,6,'',(dns,443))]))
            result=action(REQUEST)
        return result,calls
    def test_existing_sites_are_preserved_and_plan_explains_managed_changes(self):
        result,calls=self.inspect(ports=[80,443],hosts=['foreign.example.org'],action=remote.plan)
        self.assertEqual(result['format'],remote.FORMAT)
        self.assertEqual([row['kind'] for row in result['changes']],['account','ssh','proxy','https','certificate'])
        self.assertTrue(result['inventory']['caddy_effective_sha256'])
        self.assertEqual(len(result['sha256']),64)
        self.assertFalse(any(any(word in ('install','restart','reload','enable','useradd','rm') for word in row) for row in calls))
    def test_foreign_ports_and_domains_refuse_without_mutation(self):
        for args,message in (({'ports':[8888]},'прокси'),({'ports':[18766]},'туннеля'),
                             ({'ports':[443],'active':False},'другой службой'),
                             ({'hosts':['helene.example.org']},'уже обслуживается'),
                             ({'hosts':['*.example.org']},'уже обслуживается'),
                             ({'dns':'203.0.113.9'},'DNS')):
            with self.subTest(args=args),self.assertRaisesRegex(ValueError,message):self.inspect(**args)
    def test_specification_rejects_ambiguous_ports_paths_and_domain_injection(self):
        for patch in ({'id':'../../elsewhere'},{'domain':'example.org { exec bad }'},
                      {'remote_app_port':True},{'remote_proxy_port':8888.9},
                      {'remote_app_port':8888},{'https':False,'proxy':False}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):remote.specification({**REQUEST,**patch})


if __name__=='__main__':unittest.main()
