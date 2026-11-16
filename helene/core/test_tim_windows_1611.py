"""Windows sandbox findings from a Windows 10 local-model report.

* Run directories must not get a protected owner-only DACL on Windows: Python 3.13+ turns
  mkdir(mode=0o700) into one, which hid staged attachments from the sandboxed shell.
  The restricted-token Windows probe is recorded separately; this checks the chosen branch.
* busybox's "can't create /dev/null" refusal keeps its raw text and gains an owned note.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import frame_language as fl
import run_manager
from run_manager import RunContext, RunManager

RAW = "find: x\nsh: can't create /dev/null: Permission denied\nLiteral Русский 中文"


class RunDirectoryMode(unittest.TestCase):
    def mode_used(self, windows):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        target = Path(temp.name) / 'run-fixture'
        calls = []
        original = Path.mkdir

        def spy(path, *args, **kwargs):
            calls.append((path, kwargs.get('mode', args[0] if args else None)))
            return original(path, *args, **kwargs)

        with patch.object(Path, 'mkdir', spy):
            run_manager._make_run_dir(target, windows=windows)
        self.assertTrue(target.is_dir())
        with self.assertRaises(FileExistsError):
            run_manager._make_run_dir(target, windows=windows)
        self.assertEqual([path for path, _ in calls], [target])
        return calls[0][1]

    def test_windows_run_directory_inherits_the_tree_acl(self):
        self.assertIsNone(self.mode_used(True))

    def test_posix_run_directory_stays_owner_only(self):
        self.assertEqual(self.mode_used(False), 0o700)

    def test_create_uses_the_platform_branch(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        manager = RunManager(Path(temp.name) / 'runs')
        seen = []
        with patch.object(run_manager, '_make_run_dir',
                          side_effect=lambda path, *, windows: (seen.append(windows), path.mkdir())):
            manager.create(RunContext.create(kind='computer', goal='attachment staging',
                                             principal_id='owner', scope='owner'), 'input')
        self.assertEqual(seen, [os.name == 'nt'])


class SandboxDevNullNote(unittest.TestCase):
    def test_note_is_added_only_for_the_windows_busybox_refusal(self):
        for language in fl.LANGUAGES:
            with fl.use(language):
                noted = agent._with_sandbox_shell_note(RAW, windows=True)
                self.assertTrue(noted.startswith(RAW), 'raw output stays exact and first')
                self.assertEqual(noted, RAW + '\n' + fl.text('helper.agent.devnull_denied'))
                self.assertIn('$TMP/err.txt', noted)
                self.assertEqual(agent._with_sandbox_shell_note(RAW, windows=False), RAW)
                self.assertEqual(agent._with_sandbox_shell_note('ok', windows=True), 'ok')


if __name__ == '__main__':
    unittest.main()
