// Cheap source invariants complement (never replace) production-render inspection.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const read=name=>readFileSync(new URL('../../ui-kit/window/'+name,import.meta.url),'utf8');
const refresh=read('styles/interface-refresh.css'),learn=read('styles/learn.css'),variants=read('styles/look-variants.css');
test('onboarding presentation has one final owner, not a reintroduced louder cascade',()=>{
 assert.match(refresh,/@import '\.\/learn\.css';/);
 assert.doesNotMatch(refresh,/\.learn\s+\.lrn-|lrn-welcome-note/);
 assert.match(learn,/\.learn \.lrn-hero-title[^}]+28px\/1\.3 var\(--serif\)/);
 assert.match(learn,/\.learn \.lrn-toc[^}]+position: static/);
 assert.match(learn,/\.learn \.lrn-step,[^}]+border: 0;/);
 assert.match(learn,/\.learn \.lrn-map-item[^}]+text-align: left/);
});
test('shared page edges and form geometry are explicit; feed keeps its own reading zoom',()=>{
 assert.match(refresh,/--page-max: 1120px; --page-gutter: 24px; --control-h: 40px/);
 assert.match(refresh,/#view :is\(\.center,\.wide,\.settings,\.learn\)/);
 assert.match(refresh,/#view \.center\.talk \{ max-width: none/);
 assert.match(refresh,/#view :is\(\.field-input,\.paper-select-trigger\)[^}]+min-height: var\(--control-h\)/);
 assert.match(refresh,/#view \.btn[^}]+min-height: var\(--control-h\)/);
 assert.match(refresh,/#view \.field-label[^}]+text-transform: none/);
 assert.match(refresh,/#head \.engine-controls button, #head \.state-action[^}]+min-height: 32px/);
});
test('structured titles respect appearance and handwriting optics do not leak to other fonts',()=>{
 assert.match(refresh,/\.head-title:not\(\.hand\)[^}]+var\(--serif\)[^}]+-webkit-text-stroke: 0/);
 for(const font of ['serif','mono'])assert.match(variants,new RegExp(`data-look-font="${font}"\\] \\{ --sans:`));
 assert.match(variants,/not\(\[data-look-font="hand"\]\)[^}]+transform:none;-webkit-text-stroke:0/);
});
test('split panes contain long names and move wide data scrolling inside the table',()=>{
 assert.match(refresh,/#view \.split > :is\(\.list,\.reading\) \{ min-width: 0/);
 assert.match(refresh,/#view \.item > span:first-child[^}]+overflow-wrap: anywhere/);
 assert.match(refresh,/#view \.md table, #view table\.grid[^}]+overflow-x: auto/);
 assert.match(refresh,/@container \(max-width: 620px\)[\s\S]+#view \.split \{ grid-template-columns: minmax\(0,1fr\)/);
});

test('reviewed wide-data and disclosure failures have explicit layout owners',()=>{
 assert.match(refresh,/#view table\.grid :is\(th,td\)[^}]+white-space: nowrap/);
 assert.match(refresh,/:is\(\.md,\.msg-body,\.run-reading,\.run-reading-preview\) table :is\(th,td\)[^}]+padding: 8px 12px[^}]+text-align: left/);
 assert.match(refresh,/summary > \.muted[^}]+white-space: nowrap/);
 assert.match(refresh,/\.ev\.open > \.ev-head[^}]+padding: 16px 24px 8px/);
 assert.match(refresh,/\.ev\.open > \.ev-steps[^}]+padding: 0 24px 20px/);
 assert.match(learn,/\.learn \.task[^}]+grid-template-rows: subgrid; grid-row: span 3/);
});
test('Settings group tabs precede the subordinate index in DOM and visual order',()=>{
 const settings=read('views/settings-frame.ts');
 assert.match(settings,/wrap\.append\(tabs\)/);
 assert.ok(settings.indexOf('wrap.append(tabs)')<settings.indexOf('wrap.append(nav, body)'));
 assert.doesNotMatch(settings,/host\.before\(tabs\)/);
 assert.match(settings,/const tabs = wrap\.querySelector<HTMLElement>\("\.settings-tabs"\)/);
 assert.match(refresh,/\.settings > \.settings-tabs[^}]+grid-column: 1 \/ -1/);
});
