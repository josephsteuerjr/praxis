/** Developer-only catalog editing/checking. Never imports the agent or edits user data. */
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, renameSync, unlinkSync, readdirSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const LANGUAGES = ['ru', 'en', 'fr', 'zh'];
export const DESK = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = path => JSON.parse(readFileSync(path, 'utf8'));
const hash = value => createHash('sha256').update(value, 'utf8').digest('hex');
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export function fields(text) {
  const result = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '{') {
      if (text[i + 1] === '{') { i++; continue; }
      const end = text.indexOf('}', i + 1);
      if (end < 0) throw Error('Unclosed template field');
      const name = text.slice(i + 1, end).split(/[!:]/, 1)[0];
      if (!/^[A-Za-z_][A-Za-z_0-9]*$/.test(name)) throw Error(`Invalid template field: ${name}`);
      result.push(name); i = end;
    } else if (text[i] === '}') {
      if (text[i + 1] !== '}') throw Error('Unmatched closing brace');
      i++;
    }
  }
  return result.sort();
}

export function directory(family, desk = DESK) {
  if (family === 'ui') return join(desk, 'lang');
  if (family === 'frame') return resolve(desk, '../helene/core/frame_text');
  throw Error('Family must be ui or frame');
}

export function catalogs(family, desk = DESK) {
  const folder = directory(family, desk);
  return Object.fromEntries(LANGUAGES.map(language => [language, read(join(folder, language + '.json'))]));
}

export function validateCatalogs(all, family = 'frame') {
  const keys = Object.keys(all.ru).sort();
  // UI t() substitutes named {fields}; source snippets may contain literal braces.
  // Frame text uses Python Formatter, including escaped braces and conversions.
  const parameters = family === 'ui'
    ? text => [...text.matchAll(/\{([A-Za-z_][A-Za-z_0-9]*)\}/g)].map(match => match[1]).sort()
    : fields;
  for (const language of LANGUAGES) {
    if (!same(Object.keys(all[language]).sort(), keys)) throw Error(`${language}: missing or extra catalog keys`);
    for (const key of keys) {
      const value = all[language][key];
      if (typeof value !== 'string' || !value.trim()) throw Error(`${language}:${key}: empty or non-text translation`);
      if (family === 'ui' && language !== 'ru' && value.trim() === all.ru[key].trim() && value.length > 12 && /\p{L}/u.test(value.replace(/\{[A-Za-z_][A-Za-z_0-9]*\}/g, ''))) {
        throw Error(`${language}:${key}: owned caption is still the Russian source`);
      }
      let actual;
      try { actual = parameters(value); } catch (error) { throw Error(`${language}:${key}: ${error.message}`); }
      if (!same(actual, parameters(all.ru[key]))) throw Error(`${language}:${key}: template parameters differ from ru`);
    }
  }
  return keys;
}

export function frameRevision(all, key) {
  return hash(JSON.stringify(LANGUAGES.map(language => all[language][key]))).slice(0, 16);
}

/** Static callsites only; dynamic keys and arbitrary hardcoded prose still require review. */
export function checkUiReferences(all, desk = DESK) {
  const roots = ['app/src', 'miniapp/src', 'mobile/src', 'remote/src', 'setup/ui/src', 'ui-kit'];
  const files = [];
  const walk = path => {
    for (const entry of readdirSync(path, { withFileTypes: true })) {
      const child = join(path, entry.name);
      if (entry.isDirectory() && !['node_modules', 'dist', 'test', 'tests'].includes(entry.name)) walk(child);
      else if (entry.isFile() && /\.(?:ts|tsx)$/.test(entry.name) && !entry.name.endsWith('.d.ts')) files.push(child);
    }
  };
  for (const root of roots) { const path = join(desk, root); if (existsSync(path)) walk(path); }
  if (!files.length) return 0;
  let ts;
  // TypeScript 7's pure-JavaScript scanner; no native compiler worker is started.
  try { ts = createRequire(join(process.cwd(), 'package.json'))('typescript/unstable/ast'); }
  catch { ts = createRequire(join(DESK, 'app/package.json'))('typescript/unstable/ast'); }
  let checked = 0;
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    const scanner = ts.createScanner(true, ts.LanguageVariant.Standard, source);
    const tokens = [];
    let previousEnd = -1;
    for (let kind = scanner.scan(); kind !== ts.SyntaxKind.EndOfFile; kind = scanner.scan()) {
      const finish = scanner.getTokenEnd();
      if (finish <= previousEnd) {
        // Regex/template bodies can require parser rescans. This lexical guard
        // does not parse those contexts; skip one code unit and clear the window.
        // Forward progress and constant memory are required even on partial code.
        scanner.resetTokenState(Math.min(source.length, previousEnd + 1));
        tokens.length = 0;
        continue;
      }
      previousEnd = finish;
      tokens.push({ kind, text: scanner.getTokenText(), value: scanner.getTokenValue(), start: scanner.getTokenStart() });
      if (tokens.length < 4) continue;
      if (tokens.length > 4) tokens.shift();
      const [callee, open, argument, end] = tokens;
      if (callee.kind !== ts.SyntaxKind.Identifier || !['t', 'uiText', 'tn'].includes(callee.text) || open.kind !== ts.SyntaxKind.OpenParenToken) continue;
      if (![ts.SyntaxKind.StringLiteral, ts.SyntaxKind.NoSubstitutionTemplateLiteral].includes(argument.kind) || ![ts.SyntaxKind.CommaToken, ts.SyntaxKind.CloseParenToken].includes(end.kind)) continue;
      const selected = callee.text === 'tn' ? argument.value + '.other' : argument.value;
      if (!Object.hasOwn(all.ru, selected)) {
        const line = source.slice(0, argument.start).split('\n').length;
        throw Error(`${file}:${line}: unknown UI catalog key ${selected}`);
      }
      checked++;
    }
  }
  return checked;
}

export function checkFamily(family, desk = DESK) {
  const all = catalogs(family, desk);
  const keys = validateCatalogs(all, family);
  const folder = directory(family, desk);
  const stamps = read(join(folder, family === 'ui' ? 'stamps.json' : 'reviewed.json'));
  for (const key of keys) {
    if (family === 'ui') {
      const expected = hash(all.ru[key]).slice(0, 8);
      for (const language of LANGUAGES.slice(1)) {
        if (stamps[key]?.[language] !== expected) throw Error(`${family}:${key}: ${language} translation review is stale; supply all four reviewed translations`);
      }
    } else if (stamps[key] !== frameRevision(all, key)) {
      throw Error(`${family}:${key}: translation review is stale; supply all four reviewed translations`);
    }
  }
  if (family === 'ui') checkUiReferences(all, desk);
  return keys.length;
}

/** Preflight the entire batch before staging any catalog bytes. All four values are explicit. */
export function setEntries(family, entries, desk = DESK) {
  if (!entries || typeof entries !== 'object' || Array.isArray(entries) || !Object.keys(entries).length) throw Error('Supply a nonempty object of keys and four-language translations');
  const all = catalogs(family, desk);
  for (const [key, values] of Object.entries(entries)) {
    if (!/^[a-z][a-z0-9_.-]*$/.test(key) || !values || typeof values !== 'object' || Array.isArray(values) || !same(Object.keys(values).sort(), [...LANGUAGES].sort())) throw Error(`${key}: use a named lowercase key and supply exactly ru, en, fr and zh`);
    for (const language of LANGUAGES) all[language][key] = values[language];
  }
  validateCatalogs(all, family);
  const folder = directory(family, desk);
  const stampPath = join(folder, family === 'ui' ? 'stamps.json' : 'reviewed.json');
  const stamps = read(stampPath);
  for (const key of Object.keys(entries)) {
    stamps[key] = family === 'ui'
      ? Object.fromEntries(LANGUAGES.slice(1).map(language => [language, hash(all.ru[key]).slice(0, 8)]))
      : frameRevision(all, key);
  }
  const writes = LANGUAGES.map(language => [join(folder, language + '.json'), all[language]]);
  writes.push([stampPath, stamps]);
  const staged = [];
  try {
    for (const [path, value] of writes) {
      const temp = path + `.localize-${process.pid}.tmp`;
      writeFileSync(temp, JSON.stringify(value, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' });
      staged.push([temp, path]);
    }
    for (const [temp, path] of staged) renameSync(temp, path);
  } finally {
    for (const [temp] of staged) { try { unlinkSync(temp); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
  }
  return Object.keys(entries).length;
}

export function main(args) {
  const [command, family = 'all', input] = args;
  if (command === 'check') {
    for (const one of family === 'all' ? ['ui', 'frame'] : [family]) console.log(`${one}: ${checkFamily(one)} catalog keys checked`);
  } else if (command === 'set' && input) {
    const entries = JSON.parse(input === '-' ? readFileSync(0, 'utf8') : readFileSync(input, 'utf8'));
    console.log(`${family}: ${setEntries(family, entries)} reviewed entries written`);
  } else {
    throw Error('Usage: node desk/qa/localize.mjs check [ui|frame|all] | set <ui|frame> <translations.json|->');
  }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try { main(process.argv.slice(2)); } catch (error) { console.error(error.message); process.exitCode = 1; }
}
