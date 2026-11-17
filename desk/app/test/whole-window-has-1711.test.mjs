// 2.8.7 ("тормозит всё": scrolling, typing, animations). body:has(.paper-audio-bar:not([hidden])...)
// and #main:not(:has([data-page="talk"])) #head re-styled a whole-window container on every small
// DOM change; with it the full-window paper (body::before) was repainted and the page waited in
// Commit ~0.5 s per frame. Stand on WebView2 157, 600-message feed, 3/3 rounds each:
// 2.8.6 typing 410-480 ms -> with only these two rules replaced 18-21 ms.
// Guard: no :has() whose subject is a whole-window box, in any stylesheet the window loads.
import assert from 'node:assert/strict';
import {readFileSync,readdirSync,statSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const kit=fileURLToPath(new URL('../../ui-kit/',import.meta.url));
const files=[];
const walk=dir=>{for(const name of readdirSync(dir)){const path=join(dir,name);
  if(statSync(path).isDirectory())walk(path);else if(name.endsWith('.css'))files.push(path);}};
walk(kit);
assert.ok(files.length>10,'window stylesheets found');
const WHOLE=/^(?:html|:root|body|#app|#main)(?=[:.\[#\s>~+]|$)/;
const offenders=[];
for(const file of files){
  const css=readFileSync(file,'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
  // Selector lists: the text before each "{" back to the previous "}" or ";".
  for(const match of css.matchAll(/(?:^|[};])\s*([^{};@]+)\{/g)){
    for(const selector of match[1].split(/,(?![^(]*\))/)){
      const s=selector.trim();
      // The subject compound's own pseudo-classes: :has() on a whole-window box.
      const head=s.match(/^[^\s>~+]+/)?.[0]||'';
      if(WHOLE.test(head)&&/:has\(/.test(head))offenders.push(file.slice(kit.length)+': '+s);
    }
  }
}
assert.deepEqual(offenders,[],'whole-window :has() re-styles the window on every DOM change');

// The replacements are wired: the class and the data-view the CSS now reads.
const audio=readFileSync(new URL('../../ui-kit/paper-audio.ts',import.meta.url),'utf8');
assert.match(audio,/document\.body\.classList\.toggle\('paper-audio-floating'/);
assert.match(audio,/document\.body\.classList\.remove\('paper-audio-floating'\)/);
const files_css=readFileSync(new URL('../../ui-kit/paper-files.css',import.meta.url),'utf8');
assert.match(files_css,/body\.paper-audio-floating \{ padding-bottom:76px; \}/);
const main=readFileSync(new URL('../../ui-kit/window/main.ts',import.meta.url),'utf8');
assert.match(main,/mainBox\.dataset\.view = id/);
const refresh=readFileSync(new URL('../../ui-kit/window/styles/interface-refresh.css',import.meta.url),'utf8');
assert.match(refresh,/#main:not\(\[data-view="talk"\]\) #head \{/);
console.log(JSON.stringify({ok:true,stylesheets:files.length}));
