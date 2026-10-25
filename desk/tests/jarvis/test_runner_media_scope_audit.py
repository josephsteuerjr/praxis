"""Offline actual-function regression: runner archive -> real spool -> model image.

No runner/tree-agent startup. Optional RUNNER_SCOPE_SOURCE selects saved baseline;
HELENE_TREE_SOURCE overrides the sibling installed tree used for scope/media facts.
"""
import ast
import base64
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

APP = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get('RUNNER_SCOPE_SOURCE', APP / 'localharness/runner.py'))
TREE = Path(os.environ.get('HELENE_TREE_SOURCE', APP.parent / 'tree'))
sys.path.insert(0, str(TREE))
import media


def functions(path, names, env):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])), str(path), 'exec'), env)
    return env


def scope_context(**fields):
    tree = ast.parse((TREE / 'agent.py').read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ChannelContext')
    prop = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'scope')
    env = {}
    exec(compile(ast.Module(body=[prop], type_ignores=[]), str(TREE / 'agent.py'), 'exec'), env)
    context = type('ActualScopeContext', (), {'scope': env['scope']})()
    context.__dict__.update(is_dm=True, owner=False, known=False, family=False, _scope_override=None)
    context.__dict__.update(fields)
    return context


class MediaScopeAudit(unittest.TestCase):
    def test_photo_reaches_model_for_each_actual_audience(self):
        cases = [('owner-group', False, True, True, False, 'group'),
                 ('known-group', False, False, True, False, 'group'),
                 ('unknown-group', False, False, False, False, 'group'),
                 ('owner-dm', True, True, True, False, 'owner'),
                 ('known-dm', True, False, True, False, 'known'),
                 ('unknown-dm', True, False, False, False, 'unknown'),
                 ('family-dm', True, False, True, True, 'family')]
        for label, dm, owner, known, family, expected in cases:
            with self.subTest(audience=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                chat = '123' if dm else '-100123_topic_8'
                ctx = scope_context(chat_id=chat, is_dm=dm, owner=owner, known=known, family=family)
                self.assertEqual(ctx.scope, expected)
                image = root / 'memory/telegram-media/photo.png'
                image.parent.mkdir(parents=True)
                image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aZ1sAAAAASUVORK5CYII='))
                archive = root / 'memory/groups' / (chat + '.jsonl')
                archive.parent.mkdir(parents=True)
                archive.write_text(json.dumps({'media_preview_path': str(image.relative_to(root)), 'source_message_id': '42', 'text': 'photo'}) + '\n', encoding='utf-8')
                spool = media.MediaSpool(root / 'spool')
                env = {'Path': Path, 'json': json, '_tree': root, '_last_n': lambda chat_id="": 10,
                       '_agent': NS(_media_spool=lambda: spool)}
                functions(SOURCE, {'_telegram_media_refs'}, env)
                refs, notes = env['_telegram_media_refs'](chat, ctx)
                self.assertEqual(notes, [])
                self.assertEqual(len(refs), 1)
                self.assertEqual(refs[0].scope, expected)
                spool.validate_ref(refs[0], expected_scope=ctx.scope, expected_chat_id=chat)
                prompt = functions(TREE / 'agent.py', {'_media_prompt'}, {
                    '_media_spool': lambda: spool, 'media': media,
                    '_MODEL_IMAGE_MIME': {'image/png', 'image/jpeg', 'image/webp'},
                    'log': logging.getLogger(__name__)})
                _, content = prompt['_media_prompt']('photo', tuple(refs), ctx)
                self.assertIsInstance(content, list)
                self.assertEqual(content[1]['type'], 'image')
                self.assertEqual(content[1]['path'], str(refs[0].path))
                with self.assertRaises(media.MediaSecurityError):
                    spool.validate_ref(refs[0], expected_scope='unknown' if expected != 'unknown' else 'owner', expected_chat_id=chat)
                with self.assertRaises(media.MediaSecurityError):
                    spool.validate_ref(refs[0], expected_scope=ctx.scope, expected_chat_id='other-chat')


class TelegramDiagnosticAudit(unittest.TestCase):
    def test_blank_is_not_labeled_deliberate_silence(self):
        import test_runner_boundary_audit as boundary
        from unittest.mock import patch
        for row, chosen in [({'held': 'unspoken', 'note': 'private note'}, False),
                            ({'held': 'voice', 'why': 'chosen silence'}, True)]:
            harness = boundary.BoundaryAudit()
            with patch.object(boundary, 'SOURCE', SOURCE):
                harness.setUp()
            messages = []
            original = harness.env['handle_bot']

            def capture(*args, **kwargs):
                harness.env['log'] = NS(info=lambda fmt, *args: messages.append(fmt % args),
                                        warning=lambda *a: None, exception=lambda *a: None)
                return original(*args, **kwargs)

            harness.env['handle_bot'] = capture
            delivered, closed = harness.exercise_caller('telegram', row)
            self.assertEqual(delivered, [])
            self.assertEqual(closed[0]['delivered_text'], '')
            self.assertEqual(any('это её решение' in m for m in messages), chosen)
            if not chosen:
                self.assertTrue(any('без реплики' in m for m in messages))


if __name__ == '__main__':
    unittest.main(verbosity=2)
