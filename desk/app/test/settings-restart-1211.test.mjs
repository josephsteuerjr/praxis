// Run the actual settings decisions and button receipt against runtime boundaries.
import {readFileSync,writeFileSync,mkdtempSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const source=readFileSync(new URL('../../ui-kit/window/views/settings-frame.ts',import.meta.url),'utf8');
const dir=mkdtempSync(join(tmpdir(),'helene-restart-'));
try {
  const file=join(dir,'decision.ts');
  writeFileSync(file,source.slice(source.indexOf('export const RESTART_BLOCKS'),source.indexOf('export interface Config')));
  const {restartForSettings,blocksNeedingRestart,configureRestart}=await import(pathToFileURL(file));
  const base={mode:'local',port:8094,model:{model:'sol'},telegram:{bot_token:'fixture',status_message:false,allow_from:'owner'},
    sandbox:{enabled:true,mounts:[]},computer:{enabled:true,scopes:[]},voice:{enabled:false}};
  const cases=[
    [{},''],[{model:{model:'astra'}},''],[{voice:{enabled:true}},''],
    [{telegram:{...base.telegram,status_message:true,allow_from:'any',allowed_ids:[1]}},''],
    [{sandbox:{...base.sandbox,mounts:['workspace/project']}},''],
    [{computer:{...base.computer,scopes:['computer.read']}},''],
    [{telegram:{...base.telegram,bot_token:'changed'}},'engine'],
    [{owner_context:{messages:900}},'engine'],[{computer:{enabled:false}},'engine'],
    [{mode:'remote',base:'https://fixture.invalid',key:'private'},'window'],[{port:8095},'window'],
    [{tree:'new-data'},'application'],[{python:'new-runtime'},'application'],
  ];
  for(const [change,expected] of cases) {
    const after={...base,...change};const scope=restartForSettings(base,after);
    assert.equal(scope,expected,JSON.stringify(change));
    const button={dataset:{},hidden:true,textContent:''};
    const receipt=configureRestart(button,scope,blocksNeedingRestart(base,after));
    assert.equal(button.hidden,!expected);assert.equal(button.dataset.scope,expected);
    if(expected==='application')assert.match(button.textContent,/программу/);
    if(expected==='engine')assert.match(button.textContent,/движок/);
    if(expected==='window')assert.match(button.textContent,/окно/);
    assert.equal(Boolean(receipt),Boolean(expected));
  }
  assert.equal(restartForSettings(base,base,true),'window');
  console.log('settings restart: hot changes, engine, window and whole application PASS');
} finally {rmSync(dir,{recursive:true,force:true});}
