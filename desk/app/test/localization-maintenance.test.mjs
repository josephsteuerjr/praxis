import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { catalogs, checkFamily, fields, frameRevision, LANGUAGES, setEntries } from '../../qa/localize.mjs';

const translations = { ru: 'Текст {value}', en: 'Text {value}', fr: 'Texte {value}', zh: '文本 {value}' };

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'helene-localize-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const desk = join(root, 'desk');
  for (const dir of ['desk/lang', 'helene/core/frame_text']) {
    mkdirSync(join(root, dir), { recursive: true });
    for (const language of LANGUAGES) writeFileSync(join(root, dir, language + '.json'), '{}\n');
    writeFileSync(join(root, dir, dir === 'desk/lang' ? 'stamps.json' : 'reviewed.json'), '{}\n');
  }
  return { root, desk };
}

test('real product catalogs are complete and freshly reviewed', () => {
  assert.ok(checkFamily('ui') > 100);
  assert.ok(checkFamily('frame') > 100);
});

test('missing language and mismatched fields refuse the entire batch before any write', t => {
  const { desk } = fixture(t);
  for (const values of [{ ru: 'One' }, { ...translations, fr: 'Texte {other}' }]) {
    assert.throws(() => setEntries('ui', { first: translations, invalid: values }, desk));
    assert.deepEqual(catalogs('ui', desk), Object.fromEntries(LANGUAGES.map(code => [code, {}])));
    assert.deepEqual(JSON.parse(readFileSync(join(desk, 'lang/stamps.json'), 'utf8')), {});
  }
});

test('one reviewed batch updates the four catalogs and preserves the other language axis', t => {
  const { root, desk } = fixture(t);
  setEntries('frame', { existing: translations }, desk);
  const frameBefore = LANGUAGES.map(code => readFileSync(join(root, 'helene/core/frame_text', code + '.json')));
  const authored = join(root, 'owner.md'); writeFileSync(authored, 'Authored Русский {value}');
  assert.equal(setEntries('ui', { caption: translations }, desk), 1);
  assert.equal(checkFamily('ui', desk), 1);
  assert.equal(checkFamily('frame', desk), 1);
  for (const [index, code] of LANGUAGES.entries()) assert.deepEqual(readFileSync(join(root, 'helene/core/frame_text', code + '.json')), frameBefore[index]);
  assert.equal(readFileSync(authored, 'utf8'), 'Authored Русский {value}');
});

test('editing the Russian UI caption without reviewing translations names the stale key', t => {
  const { desk } = fixture(t);
  setEntries('ui', { changed: translations }, desk);
  writeFileSync(join(desk, 'lang/ru.json'), JSON.stringify({ changed: 'Другая формулировка {value}' }));
  assert.throws(() => checkFamily('ui', desk), /ui:changed: en translation review is stale/);
});

test('changing any frame language without a fresh four-language review is rejected', t => {
  const { root, desk } = fixture(t);
  setEntries('frame', { changed: translations }, desk);
  const original = catalogs('frame', desk);
  assert.equal(frameRevision(original, 'changed').length, 16);
  writeFileSync(join(root, 'helene/core/frame_text/en.json'), JSON.stringify({ changed: 'Changed text {value}' }));
  assert.throws(() => checkFamily('frame', desk), /frame:changed: translation review is stale/);
  setEntries('frame', { changed: { ...translations, en: 'Changed text {value}' } }, desk);
  assert.equal(checkFamily('frame', desk), 1);
});

test('template validation preserves repeats, escaped braces and format/conversion parameters', () => {
  assert.deepEqual(fields('{{literal}} {value!r} {count:03d} {value}'), ['count', 'value', 'value']);
  for (const text of ['{', '}', '{not a field}']) assert.throws(() => fields(text));
});

test('UI catalog checks follow the UI renderer and preserve literal source-code braces', t => {
  const { desk } = fixture(t);
  setEntries('ui', { manual: Object.fromEntries(LANGUAGES.map(language => [language, `Code for ${language}:\nexample.org {\n reverse_proxy localhost:1234\n}\n{value}`])) }, desk);
  assert.equal(checkFamily('ui', desk), 1);
  assert.ok(catalogs('ui', desk).en.manual.includes('example.org {\n reverse_proxy'));
});

test('data-only format templates can match across languages; copied owned words cannot', t => {
  const { desk } = fixture(t);
  setEntries('ui', { data: Object.fromEntries(LANGUAGES.map(code => [code, '{title} {suffix}'])) }, desk);
  assert.equal(checkFamily('ui', desk), 1);
  assert.throws(() => setEntries('ui', { copied: Object.fromEntries(LANGUAGES.map(code => [code, 'Непереведённый текст {value}'])) }, desk), /still the Russian source/);
  assert.equal(checkFamily('ui', desk), 1);
});

test('the ordinary check catches new static UI callsites with an unknown key', t => {
  const { desk } = fixture(t);
  setEntries('ui', { caption: translations }, desk);
  mkdirSync(join(desk, 'app/src'), { recursive: true });
  const file = join(desk, 'app/src/example.ts');
  writeFileSync(file, String.raw`// uiText("comment.is.not.a.call")
const data = 'uiText("quoted.data.is.not.a.call")';
const pattern = /[\/@?#\\\s]/;
uiText("caption", {value: "literal"});`);
  assert.equal(checkFamily('ui', desk), 1);
  writeFileSync(file, 'uiText("missing.new.caption");');
  assert.throws(() => checkFamily('ui', desk), /example.ts:1: unknown UI catalog key missing.new.caption/);
});
