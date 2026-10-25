"""Offline actual decoder + runner/spool regressions; no app launch/restart."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP / 'localharness'), str(APP), str(APP.parent / 'tree'), str(APP / 'tests')]
import telegram_media as tm
from test_runner_media_scope_audit import functions
import media
from PIL import Image


class Frames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'memory/telegram-media'
        self.folder.mkdir(parents=True)
        self.gif = self.folder / 'moving.gif'
        images = [Image.new('RGB', (32, 24), color) for color in ('red','green','blue','yellow')]
        images[0].save(self.gif, save_all=True, append_images=images[1:], duration=500, loop=0)

    def test_real_gif_decoder_multiple_timestamped_distinct_frames(self):
        self.assertTrue(shutil.which('ffmpeg'), 'test requires installed decoder')
        frames = tm.vision_frames(self.root, self.gif, 4)
        self.assertEqual(len(frames), 4)
        self.assertEqual([round(t, 2) for _, t in frames], [0, .5, 1, 1.5])
        self.assertEqual(len({p.read_bytes() for p, _ in frames}), 4)
        self.assertTrue(all(self.folder in p.parents and p.suffix == '.png' for p, _ in frames))
        self.assertFalse(list(self.folder.glob('.frames-*')))

    def test_local_safety_and_disabled_cap(self):
        outside = self.root / 'outside.gif'
        shutil.copyfile(self.gif, outside)
        with patch.object(tm.shutil, 'which') as which:
            self.assertEqual(tm.vision_frames(self.root, outside), [])
            self.assertEqual(tm.vision_frames(self.root, self.gif, 0), [])
            which.assert_not_called()
        with patch.object(tm.shutil, 'which', return_value=None):
            self.assertEqual(tm.vision_frames(self.root, self.gif), [])

    def test_decoder_timeout_and_no_shell_protocol_guard(self):
        import subprocess
        with patch.object(tm.shutil, 'which', side_effect=lambda x: x), patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffprobe', 3)) as run:
            self.assertEqual(tm.vision_frames(self.root, self.gif), [])
            args, kw = run.call_args
            self.assertIsInstance(args[0], list)
            self.assertNotIn('shell', kw)
            self.assertEqual(kw['timeout'], 3)
            self.assertIn('file', args[0])
            self.assertIn('-protocol_whitelist', args[0])
            self.assertIn('-f', args[0])

    def runner(self, rows, cap=4, chat='123', context_chat='123'):
        archive = self.root / 'memory/groups/123.jsonl'
        archive.parent.mkdir(exist_ok=True)
        archive.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        spool = media.MediaSpool(self.root / 'spool')
        spool.max_turn_media = cap
        env = {'Path': Path, 'json': json, '_tree': self.root, '_last_n': lambda chat_id="": 10,
               '_agent': NS(_media_spool=lambda: spool)}
        functions(APP / 'localharness/runner.py', {'_telegram_media_refs'}, env)
        refs, notes = env['_telegram_media_refs'](chat, NS(scope='group', chat_id=context_chat))
        for ref in refs:
            spool.validate_ref(ref, expected_scope='group', expected_chat_id=chat)
            with self.assertRaises(media.MediaSecurityError):
                spool.validate_ref(ref, expected_scope='owner', expected_chat_id=chat)
            with self.assertRaises(media.MediaSecurityError):
                spool.validate_ref(ref, expected_scope='group', expected_chat_id='other')
        return refs, notes

    def row(self):
        return {'media_path': self.gif.relative_to(self.root).as_posix(), 'media_kind': 'image',
                'media_mime': 'image/gif', 'source_message_id': '42'}

    def test_actual_runner_motion_without_thumbnail_reaches_scoped_spool(self):
        refs, notes = self.runner([self.row()])
        self.assertEqual(len(refs), 4)
        self.assertTrue(all('кадр' in r.caption for r in refs))
        self.assertIn('1.500 с', '\n'.join(notes))

    def test_global_cap_reserves_photo_and_zero_disables(self):
        photo = self.folder / 'photo.png'
        Image.new('RGB',(10,10),'white').save(photo)
        rows = [self.row(), {'media_preview_path': photo.relative_to(self.root).as_posix(), 'source_message_id': '43'}]
        refs, _ = self.runner(rows, cap=3)
        self.assertEqual(len(refs), 3)
        self.assertEqual(sum('кадр' in r.caption for r in refs), 2)
        refs, _ = self.runner(rows, cap=0)
        self.assertEqual(refs, [])

    def test_missing_decoder_falls_back_and_archive_chat_rejected(self):
        preview = self.folder / 'preview.png'
        Image.new('RGB',(10,10),'white').save(preview)
        row = dict(self.row(), media_preview_path=preview.relative_to(self.root).as_posix())
        with patch.object(tm.shutil, 'which', return_value=None):
            refs, notes = self.runner([row])
        self.assertEqual(len(refs), 1)
        self.assertIn('отдельное превью', '\n'.join(notes))
        self.assertEqual(self.runner([row], chat='../123')[0], [])
        self.assertEqual(self.runner([row], context_chat='other')[0], [])

    def test_equal_rows_and_mixed_budgets_never_exceed_cap(self):
        photo = self.folder / 'photo.png'
        Image.new('RGB', (10, 10), 'white').save(photo)
        still = {'media_preview_path': photo.relative_to(self.root).as_posix(), 'source_message_id': '43'}
        for cap in range(6):
            for rows in ([still] * 9, [self.row()] * 9,
                         [self.row(), still, still, self.row(), still]):
                with self.subTest(cap=cap, rows=len(rows)):
                    # Deliberately overproducing decoder: runner is final guard.
                    with patch.object(tm, 'vision_frames', return_value=[(photo, float(i)) for i in range(9)]):
                        refs, _ = self.runner(rows, cap=cap)
                    self.assertEqual(len(refs), cap)

    def test_recent_indices_not_equal_dict_membership(self):
        photo = self.folder / 'photo.png'
        Image.new('RGB', (10, 10), 'white').save(photo)
        row = {'media_preview_path': photo.relative_to(self.root).as_posix(), 'source_message_id': '43'}
        refs, _ = self.runner([row] * 8, cap=2)
        self.assertEqual(len(refs), 2)

    def test_unknown_oversize_dimensions_stop_before_duration_or_decode(self):
        import subprocess
        for streams in ([], [{}], [{'width': 0, 'height': 24}],
                        [{'width': 4097, 'height': 1}],
                        [{'width': 1920, 'height': 1081}]):
            with self.subTest(streams=streams), patch.object(subprocess, 'run', return_value=NS(stdout=json.dumps({'streams': streams}).encode())) as run:
                self.assertEqual(tm.vision_frames(self.root, self.gif), [])
                self.assertEqual(run.call_count, 1)
                self.assertIn('-nofind_stream_info', run.call_args.args[0])

    def test_unsupported_safety_flags_fail_closed_to_preview(self):
        import subprocess
        preview = self.folder / 'preview.png'
        Image.new('RGB', (10, 10), 'white').save(preview)
        row = dict(self.row(), media_preview_path=preview.relative_to(self.root).as_posix())
        for stage in range(3):
            replies = [NS(stdout=b'{"streams":[{"width":32,"height":24}]}'),
                       NS(stdout=b'{"format":{"duration":"2"}}')][:stage]
            replies.append(subprocess.CalledProcessError(1, 'unsupported safety option'))
            with self.subTest(stage=stage), patch.object(subprocess, 'run', side_effect=replies) as run:
                refs, notes = self.runner([row])
                self.assertEqual(len(refs), 1)
                self.assertIn('отдельное превью', '\n'.join(notes))
                self.assertFalse(list(self.folder.glob('.frames-*')))
                self.assertEqual(run.call_count, stage + 1)

    def test_input_resource_controls_present_on_every_process(self):
        import subprocess
        original = subprocess.run
        with patch.object(subprocess, 'run', wraps=original) as run:
            self.assertEqual(len(tm.vision_frames(self.root, self.gif)), 4)
        self.assertEqual(run.call_count, 6)
        for call in run.call_args_list:
            args = call.args[0]
            input_index = args.index('-i') if '-i' in args else len(args) - 1
            for flag, value in (('-threads', '1'), ('-max_pixels', str(1920 * 1080)),
                                ('-max_alloc', str(32 * 1024 * 1024)),
                                ('-probesize', str(1024 * 1024)), ('-analyzeduration', '1000000')):
                self.assertLess(args.index(flag), input_index)
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertEqual(call.kwargs['timeout'], 3)
            self.assertTrue(call.kwargs['check'])

    def test_real_mp4_webm_decoder_probes(self):
        import subprocess
        for suffix, codec in (('.mp4', 'libx264'), ('.webm', 'libvpx-vp9')):
            source = self.folder / ('moving' + suffix)
            subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error',
                            '-f', 'lavfi', '-i', 'testsrc2=size=160x120:rate=8:duration=4',
                            '-c:v', codec, '-pix_fmt', 'yuv420p', '-an', '-y', str(source)],
                           check=True, timeout=15)
            frames = tm.vision_frames(self.root, source)
            self.assertEqual([t for _, t in frames], [0, 1, 2, 3])
            self.assertEqual(len({p.read_bytes() for p, _ in frames}), 4)
            for path, _ in frames:
                with Image.open(path) as image:
                    image.load()
                    self.assertLessEqual(max(image.size), 1024)
            self.assertFalse(list(self.folder.glob('.frames-*')))
            # Also exercise the imported installed runner, not only extracted AST.
            import localharness.runner as runner
            archive = self.root / 'memory/groups/123.jsonl'
            archive.parent.mkdir(exist_ok=True)
            archive.write_text(json.dumps({'media_path': source.relative_to(self.root).as_posix(),
                                          'media_kind': 'video', 'source_message_id': '42'}), encoding='utf-8')
            spool = media.MediaSpool(self.root / 'spool')
            with patch.object(runner, '_tree', self.root), patch.object(runner, '_last_n', return_value=10), patch.object(runner, '_agent', NS(_media_spool=lambda: spool)):
                refs, notes = runner._telegram_media_refs('123', NS(scope='group', chat_id='123'))
            self.assertEqual(len(refs), 4)
            self.assertIn('3.000 с', '\n'.join(notes))
            for ref in refs:
                spool.validate_ref(ref, expected_scope='group', expected_chat_id='123')
                with self.assertRaises(media.MediaSecurityError):
                    spool.validate_ref(ref, expected_scope='owner', expected_chat_id='123')

    def test_renamed_playlist_rejected(self):
        self.gif.write_text('#EXTM3U\nhttp://127.0.0.1:1/private\n')
        self.assertEqual(tm.vision_frames(self.root, self.gif), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
