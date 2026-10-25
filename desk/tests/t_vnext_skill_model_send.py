"""Source-only Hélène Stage 0: exact composition and actual model-call audience.

This never sends a provider request or starts an installed service. Run with a
Python >=3.11 interpreter; the source tree is assembled in a disposable folder.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

DESK = Path(__file__).resolve().parents[1]
ROOT = DESK.parent
sys.path.insert(0, str(DESK / "installer"))
import vnext_candidate
sys.path.insert(0, str(DESK))
import deskpkg


def git(*args: str, root: Path = ROOT) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            timeout=30, check=True)
    return result.stdout.decode("utf-8").strip()


class SourceOnlyComposer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vnext-composer-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        for name in ("praxis", "helene/core"):
            (self.repo / name).mkdir(parents=True)
        (self.repo / "praxis" / "both.py").write_bytes(b"core\n")
        (self.repo / "helene/core" / "both.py").write_bytes(b"edition\n")
        (self.repo / "praxis" / "core_only.py").write_bytes(b"only core\n")
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "praxis", "helene/core"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.test", "commit", "-qm", "base"],
                       check=True, capture_output=True)
        self.head = git("rev-parse", "HEAD", root=self.repo)

    def test_source_precedence_manifest_and_pinned_revision(self):
        output = self.base / "candidate"
        manifest = vnext_candidate.stage(output, root=self.repo, expected_head=self.head)
        self.assertEqual((output / "both.py").read_bytes(), b"edition\n")
        self.assertEqual((output / "core_only.py").read_bytes(), b"only core\n")
        self.assertEqual(manifest["files"]["both.py"]["source"], "helene/core/both.py")
        self.assertEqual(manifest["files"]["core_only.py"]["source"], "praxis/core_only.py")
        self.assertEqual(manifest["kind"], "vnext_source_only")
        self.assertEqual(json.loads((output / ".vnext-source.json").read_text(encoding="utf-8")), manifest)
        with self.assertRaisesRegex(ValueError, "already exists"):
            vnext_candidate.stage(output, root=self.repo, expected_head=self.head)
        self.assertEqual((output / "both.py").read_bytes(), b"edition\n")
        with self.assertRaisesRegex(ValueError, "HEAD moved"):
            vnext_candidate.stage(self.base / "other", root=self.repo, expected_head="0" * 40)
        self.assertFalse((self.base / "other").exists())

    def test_unexpected_dirty_and_failed_capture_leave_no_candidate(self):
        (self.repo / "praxis" / "core_only.py").write_bytes(b"changed\n")
        output = self.base / "candidate"
        with self.assertRaisesRegex(ValueError, "source path drift"):
            vnext_candidate.stage(output, root=self.repo, expected_head=self.head)
        self.assertFalse(output.exists())
        manifest = vnext_candidate.stage(output, root=self.repo, expected_head=self.head,
                                         allowed_dirty=("praxis/core_only.py",))
        self.assertEqual(manifest["allowed_dirty"], ["praxis/core_only.py"])
        self.assertEqual((output / "core_only.py").read_bytes(), b"changed\n")
        (self.repo / "praxis" / "surprise.py").write_bytes(b"unexpected\n")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            vnext_candidate.stage(self.base / "second", root=self.repo, expected_head=self.head,
                                  allowed_dirty=("praxis/core_only.py",))
        self.assertFalse((self.base / "second").exists())

    def test_selected_symlink_is_not_followed(self):
        outside = self.base / "private.txt"
        outside.write_bytes(b"do-not-copy")
        link = self.repo / "helene/core/linked.py"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("this Windows user cannot create source symlinks")
        with self.assertRaisesRegex(ValueError, "symlink in source"):
            vnext_candidate.stage(self.base / "candidate", root=self.repo,
                                  expected_head=self.head,
                                  allowed_dirty=("helene/core/linked.py",))
        self.assertFalse((self.base / "candidate").exists())

    def test_ignored_eligible_source_is_not_silently_blessed_by_head(self):
        (self.repo / ".git/info/exclude").write_text("praxis/ignored.py\n", encoding="utf-8")
        (self.repo / "praxis/ignored.py").write_bytes(b"untracked and ignored\n")
        with self.assertRaisesRegex(ValueError, "source has no Git identity: praxis/ignored.py"):
            vnext_candidate.stage(self.base / "candidate", root=self.repo,
                                  expected_head=self.head)
        self.assertFalse((self.base / "candidate").exists())

    def test_deleted_tracked_source_refuses_even_when_declared(self):
        (self.repo / "praxis/core_only.py").unlink()
        with self.assertRaisesRegex(ValueError, "tracked source absent"):
            vnext_candidate.stage(self.base / "candidate", root=self.repo,
                                  expected_head=self.head,
                                  allowed_dirty=("praxis/core_only.py",))
        self.assertFalse((self.base / "candidate").exists())

    def test_assume_unchanged_cannot_hide_worktree_bytes(self):
        subprocess.run(["git", "-C", str(self.repo), "update-index", "--assume-unchanged",
                        "praxis/core_only.py"], check=True, capture_output=True)
        (self.repo / "praxis/core_only.py").write_bytes(b"hidden change\n")
        self.assertEqual(git("diff", "--name-only", "HEAD", "--", "praxis/core_only.py",
                             root=self.repo), "")
        with self.assertRaisesRegex(ValueError, "undeclared source bytes differ from HEAD"):
            vnext_candidate.stage(self.base / "candidate", root=self.repo,
                                  expected_head=self.head)
        self.assertFalse((self.base / "candidate").exists())


# The candidate's *own* agent and capabilities modules run in a fresh Python
# process. Importing the parent checkout here would falsely prove only the
# author's source tree, not the composed edition. All names/bodies are synthetic.
SEND_FIXTURE = r'''
import json
from pathlib import Path
from unittest.mock import patch
import agent, capabilities, frame_shadow, frame_serve
from types import SimpleNamespace
base = Path(agent.BASE)
skills = base / 'soul' / 'skills'
skills.mkdir(parents=True, exist_ok=True)
(base / 'memory').mkdir(exist_ok=True)
(skills / 'INDEX.md').write_text('| [Public display](visible.md) | owner purpose |\n'
                                 '| [Ghost](orphan.md) | stale purpose |\n', encoding='utf-8')
(skills / 'visible.md').write_text('VISIBLE_BODY_CANARY', encoding='utf-8')
(skills / 'private_filename_canary.md').write_text('PRIVATE_BODY_CANARY', encoding='utf-8')
(base / 'memory' / 'INDEX.md').write_text('recent: private_filename_canary.md', encoding='utf-8')
(base / 'soul' / 'SOUL.md').write_text('OWN_SOUL', encoding='utf-8')
(base / 'soul' / 'VOICE.md').write_text('OWN_VOICE', encoding='utf-8')

def call(ctx, *, serve=False):
    with patch.object(agent, '_persona_text', return_value='PERSONA'), \
         patch.object(agent.llm, 'chat', return_value='fake') as fake:
        persona, system, evidence = agent._build_prompt_parts(ctx=ctx)
        live = persona + system + evidence
        if serve:
            with frame_serve.bind(system=live, ctx=ctx, dynamic='DYNAMIC_TRANSPORT'):
                agent._model_call(live, [{'role':'user','content':'synthetic turn'}], [])
        else:
            agent._model_call(live, [{'role':'user','content':'synthetic turn'}], [])
        return str(fake.call_args.kwargs)

owner = agent.ChannelContext(chat_id='177', room_id='177', principal_id='177',
                             is_dm=True, owner=True, known=True, mailbox_index_override='')
other = agent.ChannelContext(chat_id='188', room_id='188', principal_id='188',
                             is_dm=True, owner=False, known=True, mailbox_index_override='')
group = agent.ChannelContext(chat_id='199', room_id='199', principal_id='177',
                             is_dm=False, owner=True, known=True, mailbox_index_override='')
owner_legacy = call(owner)
owner_v6 = call(owner, serve=True)
other_legacy, group_legacy = call(other), call(group)
nonowner_v6 = frame_shadow._block_memory_index('other', other)
index_before = agent._memory_navigation_hint(owner_audience=False)

# A non-owner v6 candidate is conditional on a KEAT receipt. Exercise the real
# frame selector and model-call logic with synthetic POSITIVE/REVOKED receipts;
# these stubs attest neither actual KEAT admission nor provider delivery.
def nonowner_selected(revoke):
    fake_run = SimpleNamespace(run_id='synthetic')
    fake_store = SimpleNamespace(store_result=lambda *args, **kwargs: None)
    response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
    with patch.object(agent, '_persona_text', return_value='PERSONA'), \
         patch.object(frame_serve.keat_runtime, 'staged', return_value=True), \
         patch.object(agent.run_context, 'current_run', return_value=fake_run), \
         patch.object(agent, '_runs', return_value=fake_store), \
         patch.object(agent, '_run_status_gate'), \
         patch.object(agent, '_run_event_strict'), \
         patch.object(agent.keat_live, 'prepare_provider_messages', side_effect=lambda _s,m,_t: m), \
         patch.object(agent.keat_live, 'select_provider', return_value={'served': True}), \
         patch.object(agent.keat_live, 'accept_provider_receipt', return_value=True), \
         patch.object(agent.keat_live, 'finalize_provider_receipt', return_value=not revoke), \
         patch.object(agent, '_chat_cancellable', side_effect=lambda role, **kw: agent.llm.chat(role, **kw)), \
         patch.object(agent.llm, 'chat', return_value=response) as fake:
        persona, system, evidence = agent._build_prompt_parts(ctx=other)
        live = persona + system + evidence
        with frame_serve.bind(system=live, ctx=other, dynamic='NONOWNER_DYNAMIC'):
            agent._model_call(live, [{'role':'user','content':'synthetic other turn'}], [])
        return str(fake.call_args.kwargs)

other_v6_served = nonowner_selected(False)
other_v6_revoked = nonowner_selected(True)
(skills / 'INDEX.md').unlink()
missing_owner = call(owner)
(skills / 'INDEX.md').write_bytes(b'bad-index-\xff')
corrupt_owner = call(owner)
result = {
 'module': str(Path(agent.__file__).resolve()),
 'capabilities': str(Path(capabilities.__file__).resolve()),
 'owner_legacy': owner_legacy,
 'owner_v6': owner_v6,
 'other_legacy': other_legacy,
 'group_legacy': group_legacy,
 'nonowner_v6': nonowner_v6,
 'nonowner_index': index_before,
 'other_v6_served': other_v6_served,
 'other_v6_revoked': other_v6_revoked,
 'missing_owner': missing_owner,
 'corrupt_owner': corrupt_owner,
}
print(json.dumps(result, ensure_ascii=True))
'''


class ComposedModelConsumer(unittest.TestCase):
    def test_complete_desk_source_package_uses_exact_kit_and_proposes_not_overwrites(self):
        with tempfile.TemporaryDirectory(prefix="vnext-kit-package-") as tmp:
            root = Path(tmp)
            app = root / "app"
            manifest = deskpkg.build(app, deskpkg.WINDOWS, allow_partial=False,
                                     log=lambda _line: None)
            self.assertEqual(manifest["skipped"], [])
            for rel in ("resources/SOUL.md", "resources/soul/VOICE.md",
                        "resources/soul/skills/INDEX.md", "localharness/boot.py",
                        "localharness/kit_proposals.py"):
                source, staged = DESK / rel, app / rel
                self.assertEqual(source.read_bytes(), staged.read_bytes(), rel)
                self.assertEqual(manifest["files"][rel],
                                 hashlib.sha256(staged.read_bytes()).hexdigest())
            # Import the *staged* boot/kit, not the parent checkout. The direct
            # empty-tree compatibility route is safe only in this disposable
            # fixture; production birth is receipt-bound and tested separately.
            script = r'''
import json
from pathlib import Path
from unittest.mock import patch
import boot, kit_proposals
tree = Path(__import__('sys').argv[1])
cfg = {'agent': {'name':'Test'}, 'owner': {'name':'Owner'}}
with patch.object(boot, 'seed_git', return_value=False), \
     patch.object(boot, '_import_self_model', return_value=None):
    boot.ensure_layout(tree, cfg)
    soul = tree/'soul/SOUL.md'
    original = soul.read_bytes()
    soul.write_text('# Authored by agent\n', encoding='utf-8')
    boot.ensure_layout(tree, cfg)
    rows = kit_proposals.active(tree)
    print(json.dumps({'module':str(Path(boot.__file__).resolve()),
                      'original_sha':__import__('hashlib').sha256(original).hexdigest(),
                      'authored':soul.read_text(encoding='utf-8'),
                      'proposal':any(row['target']=='soul/SOUL.md' for row in rows)}))
'''
            env = {**os.environ, "PYTHONPATH": str(app / "localharness"), "PYTHONUTF8": "1"}
            result = subprocess.run([sys.executable, "-c", script, str(root / "agent-data")],
                                    env=env, cwd=app, capture_output=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace")[-4000:])
            row = json.loads(result.stdout.decode("utf-8").splitlines()[-1])
            self.assertEqual(Path(row["module"]), app / "localharness/boot.py")
            self.assertEqual(row["authored"], "# Authored by agent\n")
            self.assertTrue(row["proposal"])

    def test_actual_model_call_owner_and_other_with_missing_index(self):
        head = git("rev-parse", "HEAD")
        changed = set(git("diff", "--name-only", "HEAD", "--", "praxis", "helene/core").splitlines())
        untracked = git("ls-files", "--others", "--exclude-standard", "--", "praxis", "helene/core")
        changed.update(untracked.splitlines())
        approved = {"helene/core/agent.py", "helene/core/frame_shadow.py",
                    "helene/core/skill_navigation.py"}
        self.assertLessEqual(changed, approved, "unexpected source edit during test")
        with tempfile.TemporaryDirectory(prefix="vnext-consumer-") as tmp:
            root = Path(tmp)
            candidate = root / "edition"
            manifest = vnext_candidate.stage(candidate, root=ROOT, expected_head=head,
                                             allowed_dirty=tuple(sorted(changed)))
            for rel in ("agent.py", "frame_shadow.py", "capabilities.py", "skill_navigation.py"):
                entry = manifest["files"][rel]
                self.assertEqual(entry["sha256"], hashlib.sha256((candidate / rel).read_bytes()).hexdigest())
            self.assertEqual(manifest["files"]["agent.py"]["source"], "helene/core/agent.py")
            self.assertEqual(manifest["files"]["capabilities.py"]["source"], "praxis/capabilities.py")
            self.assertEqual(manifest["files"]["skill_navigation.py"]["source"],
                             "helene/core/skill_navigation.py")
            env = {**os.environ, "PYTHONPATH": str(candidate), "PRAXIS_BASE": str(root / "data"),
                   "PRAXIS_FRAME_V6": "serve", "PRAXIS_FRAME_V6_STREAMS": "dm-177",
                   "PRAXIS_OWNER_ID": "177", "PRAXIS_FRAME_INDEX_BODY": "on", "PYTHONUTF8": "1"}
            run = subprocess.run([sys.executable, "-c", SEND_FIXTURE], cwd=candidate, env=env,
                                 capture_output=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace")[-4000:])
            row = json.loads(run.stdout.decode("utf-8").splitlines()[-1])
            self.assertEqual(Path(row["module"]), candidate / "agent.py")
            self.assertEqual(Path(row["capabilities"]), candidate / "capabilities.py")
            self.assertIn("[frame v6 canary:", row["owner_v6"],
                          "owner v6 request must actually be served, not just shadowed")
            self.assertIn("[frame v6 canary:", row["other_v6_served"],
                          "non-owner v6 must be the selected candidate under positive synthetic KEAT")
            self.assertNotIn("[frame v6 canary:", row["other_v6_revoked"],
                             "revoked KEAT must restore the live system")
            for field in ("owner_legacy", "owner_v6"):
                text = row[field]
                self.assertIn("soul/skills/visible.md", text)
                self.assertIn("owner purpose", text)
                self.assertIn("private_filename_canary.md", text)
                self.assertIn("orphan.md", text)
                self.assertNotIn("VISIBLE_BODY_CANARY", text)
                self.assertNotIn("PRIVATE_BODY_CANARY", text)
            self.assertIn("INDEX.md не найден", row["missing_owner"])
            self.assertIn("private_filename_canary.md", row["missing_owner"])
            self.assertIn("INDEX.md не читается", row["corrupt_owner"])
            self.assertNotIn("owner purpose", row["corrupt_owner"])
            for field in ("other_legacy", "group_legacy", "nonowner_v6", "nonowner_index",
                          "other_v6_served", "other_v6_revoked"):
                text = row[field]
                self.assertNotIn("private_filename_canary", text, field)
                self.assertNotIn("visible.md", text, field)
                self.assertNotIn("PRIVATE_BODY_CANARY", text, field)
            print("vnext_source_files=%d digest=%s head=%s" % (
                len(manifest["files"]), manifest["digest"], head))


if __name__ == "__main__":
    unittest.main(verbosity=2)
