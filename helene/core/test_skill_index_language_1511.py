"""Run the real filesystem writer without importing a live agent or providers."""
import ast
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import frame_language


def writers(directory):
    source=Path(__file__).with_name('agent.py').read_text(encoding='utf-8')
    functions=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef)
               and node.name in ('tool_write_skill','_ensure_skill_index_line')]
    calls=[]
    namespace={'SKILLS_DIR':directory,'frame_language':frame_language,
        '_read':lambda path:path.read_text(encoding='utf-8') if path.exists() else '',
        '_slug':lambda value:'sample',
        '_reindex':lambda path:calls.append(('index',path)),
        'selfgit':SimpleNamespace(snapshot=lambda reason:'fixture-sha'),
        'immune':SimpleNamespace(enqueue=lambda *args:None),
        'lessons':SimpleNamespace(accept=lambda note,slug:calls.append(('origin',note,slug))),
        'log':SimpleNamespace(info=lambda *args:None,debug=lambda *args,**kwargs:None)}
    exec(compile(ast.Module(body=functions,type_ignores=[]),'agent-skill-writers','exec'),namespace)
    return namespace,calls


class SkillIndexLanguage(unittest.TestCase):
    def test_real_skill_write_has_localized_receipt_and_literal_authored_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            for language in frame_language.LANGUAGES:
                directory=Path(temporary)/language;directory.mkdir()
                namespace,calls=writers(directory)
                name='AUTHORED Русский 日本語 <&>';body='# AUTHORED\nFrançais 中文 $&'
                note='note-日本語'
                with frame_language.use(language):
                    result=namespace['tool_write_skill'](name,body,note)
                    origin=frame_language.text('skills.write_origin',note_id=note)
                    expected=frame_language.text('skills.write_result',name=name,slug='sample',origin=origin)
                    heading=frame_language.text('skills.written_heading')
                self.assertEqual(result,expected)
                self.assertEqual((directory/'sample.md').read_text(encoding='utf-8'),body+'\n')
                index=(directory/'INDEX.md').read_text(encoding='utf-8')
                self.assertIn(heading,index);self.assertIn(f'- [{name}](sample.md)',index)
                self.assertIn(('origin',note,'sample'),calls)
                before=(directory/'INDEX.md').read_bytes()
                namespace['_ensure_skill_index_line']('sample',name)
                self.assertEqual((directory/'INDEX.md').read_bytes(),before)

    def test_existing_authored_and_legacy_headings_are_preserved_across_locale_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);namespace,_=writers(directory)
            headings=[frame_language.catalog(code)['skills.written_heading'] for code in frame_language.LANGUAGES]
            headings.append('## Записаны Praxis')
            for previous in headings:
                original='# AUTHORED 日本語\n\n'+previous+'\n- [Existing](existing.md)\n'
                (directory/'INDEX.md').write_text(original,encoding='utf-8')
                with frame_language.use('fr'):
                    namespace['_ensure_skill_index_line']('sample','AUTHORED')
                result=(directory/'INDEX.md').read_text(encoding='utf-8')
                self.assertEqual(result,original+'- [AUTHORED](sample.md)\n')


if __name__=='__main__':unittest.main()
