// Actual bundled title and desktop renderers, independent client/frame settings.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const desk=fileURLToPath(new URL('../../',import.meta.url));
const {rolldown}=createRequire(new URL('../../miniapp/package.json',import.meta.url))('rolldown');
const temporary=mkdtempSync(join(tmpdir(),'helene-title-'));
try {
 const file=p=>JSON.stringify(resolve(desk,p).replaceAll('\\','/'));
 writeFileSync(join(temporary,'entry.ts'),[
  'export * as text from '+file('ui-kit/text.ts')+';',
  'export * as language from '+file('lang/lang.ts')+';',
  'export * as desktop from '+file('ui-kit/window/runlist.ts')+';',
  'export * as steps from '+file('ui-kit/steps.ts')+';'
 ].join('\n'));
 const build=await rolldown({input:join(temporary,'entry.ts'),logLevel:'silent'});
 await build.write({file:join(temporary,'actual.mjs'),format:'esm'});await build.close();
 globalThis.document={addEventListener(){},querySelector(){return null},documentElement:{lang:''}};
 globalThis.window={addEventListener(){}};
 globalThis.localStorage={getItem(){return null},setItem(){}};
 const {text,language,desktop}=await import(pathToFileURL(join(temporary,'actual.mjs')).href);
 const schema='helene.run-title.v1',legacy='Пробуждение';
 let pairs=0;
 for(const ui of ['ru','en','fr','zh'])for(const frame of ['ru','en','fr','zh']){
  globalThis.DESK_CONFIG_OVERRIDE={agent:{frame_language:frame}};language.setLocale(ui);
  const recipe={schema,kind:'conversation',author_kind:'name',author:'Пробуждение',text:'<script>AUTHORED 中文</script>'};
  const before=JSON.stringify(recipe),row={id:'fixture',kind:'chat_turn',status:'done',goal_head:'WRONG legacy',display_title:recipe};
  assert.equal(text.runTitle(row),language.t('run.title.conversation',{author:recipe.author,text:recipe.text}));
  assert.equal(desktop.runLabel(row),text.runTitle(row));
  const html=desktop.runRowHTML(row);
  assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;AUTHORED 中文'));
  assert.equal(JSON.stringify(recipe),before);
  assert.equal(text.runTitle({goal_head:legacy}),legacy);
  assert.equal(text.runTitle({goal_head:legacy,display_title:{schema:'unknown',kind:'event',event:'wake'}}),legacy);
  const wake=text.runTitle({display_title:{schema,kind:'event',event:'wake'}});
  assert.equal(wake,language.t('run.title.wake'));
  assert.equal(text.runOriginText({text:'RAW',display_title:{schema,kind:'origin_unavailable',message_ids:['9']}}),
    language.t('run.title.missing_messages',{ids:'#9'}));
  assert.equal(text.runOriginText({text:'Пробуждение'}),'Пробуждение');
  const long=text.runTitle({display_title:{schema,kind:'conversation',author_kind:'name',author:'😀'.repeat(160),text:'',truncated:true}});
  assert.equal(Array.from(long).length,140);assert(long.endsWith('…'));assert(!long.includes('\uFFFD'));
  assert.equal(text.runTitle({goal_head:legacy,display_title:{schema,kind:'origin_unavailable',message_ids:[{}]}}),legacy);
  pairs++;
 }
 language.setLocale('en');const event={display_title:{schema,kind:'event',event:'wake'}};
 const en=text.runTitle(event);language.setLocale('fr');assert.notEqual(text.runTitle(event),en);
 language.setLocale('');globalThis.DESK_CONFIG_OVERRIDE={locales:['fr-CA'],agent:{frame_language:'zh'}};
 assert.equal(text.runTitle(event),'Réveil');
 // Phone closure is exercised by the final browser gate; preserve the source port.
 const phone=readFileSync(resolve(desk,'ui-kit/phone.ts'),'utf8');
 const label=phone.match(/function runLabel\(r: Run\): string \{([\s\S]*?)\n  \}/)?.[1]||'';
 assert(label.includes('runTitle(r)'));assert(!label.includes('w?.out'));
 console.log(JSON.stringify({ok:true,pairs,actual:['text','lang','runlist','steps'],unicode:true,literal:true}));
} finally {rmSync(temporary,{recursive:true,force:true});}
