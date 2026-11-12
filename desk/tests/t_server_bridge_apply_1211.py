"""Actual Linux files/locks; package/service commands are inert fixture effects."""
import base64,contextlib,json,os,sys,tempfile,types,unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import server_bridge_remote as remote


@unittest.skipUnless(sys.platform=='linux','POSIX atomic files and fcntl qualification')
class Apply(unittest.TestCase):
    def test_os_release_metadata_link_is_read_but_config_links_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'os-release';target.write_bytes(b'ID=ubuntu\n')
            link=root/'alias';link.symlink_to(target)
            self.assertEqual(remote._bytes(link,metadata=True),b'ID=ubuntu\n')
            with self.assertRaisesRegex(ValueError,'символической'):remote._bytes(link)
    def test_cas_retains_original_refuses_validation_failure_and_external_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'config';backup=root/'private/original'
            original=b'foreign site\n';config.write_bytes(original)
            def refused(_):raise ValueError('fixture validation refusal')
            with self.assertRaisesRegex(ValueError,'fixture'):
                remote.append_config(config,remote.digest(original),'managed',backup,refused)
            self.assertEqual(config.read_bytes(),original);self.assertEqual(backup.read_bytes(),original)
            config.write_bytes(b'owner changed this')
            with self.assertRaisesRegex(ValueError,'изменены отдельно'):
                remote.append_config(config,remote.digest(original),'managed',backup,lambda _:None)
            self.assertEqual(config.read_bytes(),b'owner changed this')

    def test_resume_after_lost_service_ack_keeps_foreign_config_and_exact_owned_steps(self):
        import pwd
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def path(value):
                text=str(value)
                return root/text.lstrip('/') if text.startswith(('/etc/','/var/')) else Path(value)
            ssh=path('/etc/ssh/sshd_config');ssh.parent.mkdir(parents=True);ssh.write_bytes(b'# existing owner config\nPort 22\n')
            caddy=path('/etc/caddy/Caddyfile');caddy.parent.mkdir(parents=True);caddy.write_bytes(b'foreign.example.org { respond "keep" }\n')
            original_ssh=ssh.read_bytes();original_caddy=caddy.read_bytes()
            identity='a'*32;spec=remote.specification({'id':identity,'proxy':True,'https':True,'domain':'helene.example.org'})
            state={'specification':spec,'tinyproxy':True,'caddy':True,'caddy_active':True,'caddy_file_exists':True,
                   'caddy_file_sha256':remote.digest(original_caddy),'sshd_config_sha256':remote.digest(original_ssh),
                   'sshd':'/usr/sbin/sshd','ssh_service':'ssh','dns':['203.0.113.5'],'ports':[22,80,443]}
            plan={'format':remote.FORMAT,'id':identity,'inventory':state,'changes':[{'kind':'fixture'}]}
            plan['sha256']=remote.digest(json.dumps(plan,sort_keys=True,separators=(',',':')).encode())
            request={'plan':plan,'accepted_sha256':plan['sha256'],
                     'public_key':'ssh-ed25519 '+base64.b64encode(b'fixture public bytes').decode()}
            users={};calls=[];fail=[True]
            def account(name):
                if name not in users:raise KeyError(name)
                return users[name]
            def command(argv,**kwargs):
                calls.append(argv)
                if argv[0]=='useradd':
                    name=argv[-1];home=Path(argv[argv.index('--home-dir')+1]);home.mkdir(parents=True)
                    users[name]=types.SimpleNamespace(pw_dir=str(home),pw_shell='/usr/sbin/nologin',pw_uid=os.getuid(),pw_gid=os.getgid())
                if argv==['systemctl','reload','caddy'] and fail[0]:
                    fail[0]=False;raise ValueError('fixture lost acknowledgement')
                if argv[:2]==['caddy','validate']:
                    candidate=Path(argv[argv.index('--config')+1]).read_text()
                    self.assertIn('foreign.example.org',candidate);self.assertEqual(candidate.count('import '),1)
                if argv[:2]==['/usr/sbin/sshd','-T']:
                    return 0,'\n'.join(['allowtcpforwarding yes','permitopen 127.0.0.1:8888','permitlisten 127.0.0.1:18766',
                        'allowagentforwarding no','x11forwarding no','permittty no','permittunnel no','gatewayports no',
                        'passwordauthentication no','kbdinteractiveauthentication no','forcecommand /bin/false',
                        'pubkeyauthentication yes','disableforwarding no'])
                return 0,'active'
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(remote,'Path',side_effect=path))
                stack.enter_context(mock.patch.object(remote.os,'geteuid',return_value=0))
                stack.enter_context(mock.patch.object(remote,'inspect',return_value=state))
                stack.enter_context(mock.patch.object(remote,'command',side_effect=command))
                stack.enter_context(mock.patch.object(remote.shutil,'which',return_value='/usr/bin/tinyproxy'))
                stack.enter_context(mock.patch.object(pwd,'getpwnam',side_effect=account))
                with self.assertRaisesRegex(ValueError,'lost acknowledgement'):remote.apply(request)
                result=remote.apply(request)
            self.assertTrue(result['installed']);self.assertFalse(result['verified'])
            self.assertEqual(sum(row[0]=='useradd' for row in calls),1)
            self.assertEqual(caddy.read_text().count('import '),1)
            self.assertTrue(caddy.read_bytes().startswith(original_caddy))
            self.assertTrue(ssh.read_bytes().startswith(original_ssh))
            self.assertEqual(ssh.read_text().count('Match User'),1)
            self.assertIn('PermitOpen 127.0.0.1:8888',ssh.read_text())
            self.assertIn('PermitListen 127.0.0.1:18766',ssh.read_text())
            retained=path('/var/lib/helene-bridge')/identity
            self.assertEqual((retained/'sshd.before').read_bytes(),original_ssh)
            self.assertEqual((retained/'caddy.before').read_bytes(),original_caddy)
            self.assertEqual(json.loads((retained/'operation.json').read_text())['done'],['packages','account','ssh','proxy','https','installed'])
            public_key=next(path('/var/lib/helene-bridge-users').glob('*/.ssh/authorized_keys')).read_text()
            self.assertTrue(public_key.startswith('restrict,port-forwarding,command="/bin/false",permitopen='))

    def test_effective_ssh_conflict_preserves_original_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);config=root/'sshd_config';before=b'Match User hb*\n    AllowTcpForwarding no\n'
            config.write_bytes(before)
            spec=remote.specification({'id':'a'*32,'proxy':True})
            def command(argv,**kwargs):
                return (0,'allowtcpforwarding no') if '-T' in argv else (0,'')
            with mock.patch.object(remote,'command',side_effect=command):
                with self.assertRaisesRegex(ValueError,'allowtcpforwarding'):
                    remote.append_config(config,remote.digest(before),'Match User hba\n AllowTcpForwarding local',root/'before',
                        lambda path:remote.validate_ssh(path,'sshd','hba',spec))
            self.assertEqual(config.read_bytes(),before)


if __name__=='__main__':unittest.main()
