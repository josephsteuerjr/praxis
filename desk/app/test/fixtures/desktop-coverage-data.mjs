// Production-render coverage data only. Never reads a profile or calls native OS/model APIs.
import {extendFixture, ready, close} from './ui-session-server.mjs';
import {readFile} from 'node:fs/promises';
export {ready,close};
const at='2026-11-04T15:00:00Z', seconds=Date.parse(at)/1000;
export const long='Проверка длинной подписи — исследование приложения, согласование изменений и подробный результат для владельца';
const markdown=`# Проверка настоящей страницы\n\n${long}. Эти данные синтетические, не переписка владельца.\n\n## Материалы и результат\n\n| Объект | Состояние | Основание |\n| --- | --- | --- |\n| Длинное название исходного документа | Проверено | Запись инструмента |\n\n\`\`\`js\nconst path = 'C:/Пользователи/Длинное название папки/Материалы/исходный-документ.md';\nconsole.log(path);\n\`\`\`\n\n- Первый пункт\n- Второй пункт с **важным содержанием**\n\n${long}.\n`;
const ref={kind:'payload',sha256:'a'.repeat(64)};
let scene='populated', conflict=false, commandState='queued', platform='windows', provider='api';
let delayedPaths=[], errorPaths=[], delayMs=1500;
export function setCoverageState(state={}) {
 scene=state.scene||'populated'; conflict=!!state.conflict; commandState=state.commandState||'queued';
 platform=state.platform||'windows'; provider=state.provider||'api';
 delayedPaths=state.delayedPaths||[]; errorPaths=state.errorPaths||[]; delayMs=state.delayMs??1500;
}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const failure=message=>Object.assign(Error(message),{status:503});
const rows=[{id:'run-live',kind:'chat_turn',status:'running',chat_id:'window',created_at:at,goal_head:long},...Array.from({length:7},(_,i)=>({id:'run-'+i,kind:i===6?'wake':'chat_turn',status:i===2?'failed':'done',chat_id:i%2?'window-long':'window',created_at:at,goal_head:long+' '+i}))];
const detail=id=>({id,origin:{text:long,source:'Синтетическая проверка'},outcome:{text:markdown,note:'Результат сохранён'},manifest:{status:id==='run-live'?'running':'done',created_at:at,goal:long,context:{kind:'chat_turn',delivery_chat_id:'window'},terminal:{status:'done',reason:'end_turn'}},iterations:[{seq:1,status:'done',at,model:'gpt-6.1-sol/проверочная-длинная-модель',ms:2430,usage:{in:123456,cache_read:110000,out:3456},text:'Сначала прочитаю исходники и проверю результат.',tools:[{seq:2,call_id:'read-1',at,tool:'read_file',args:{path:'C:/Пользователи/Материалы/длинный-исходный-документ.md'},definition:{description:long,input_schema:{type:'object'}},execution:{handler:'read_file',kind:'file',status:'done'},result:{head:markdown,size:2000}}]},{seq:3,status:'done',at,model:'fixture',text:markdown,tools:[{seq:4,call_id:'shell-2',tool:'shell',args:{command:'python -m pytest test_очень_длинное_название.py'},execution:{handler:'shell',command:'python -m pytest',cwd:'C:/Работа/Проверка',status:'done',exit_code:0},result:{head:'5 passed in 1.20s',size:22}}]}]});
const counts={calls:24,total_tokens:123456,input_tokens:120000,output_tokens:3456,cache_read_tokens:100000,cache_percent:83,legacy_semantics:false};
const spendRow={calls:24,runs:8,input_tokens:120000,cached_tokens:100000,fresh_tokens:20000,output_tokens:3456,total_tokens:123456,cache_ratio:.83,seconds:123,errors:1,kinds:{chat_turn:8},models:{fixture:24}};
const spend={days:7,summary:{...spendRow,bound_calls:23,unbound_calls:1,fields_in_log:{chat:true}},by_chat:[{...spendRow,chat_id:'window-long',title:long}],by_person:[{...spendRow,who:'123456789',name:'Владелец',chats:['Основной чат',long]}],by_run:[{...spendRow,run_id:'run-0',kind:'chat_turn',chat_id:'window',chat_title:long,who:'123',who_name:'Владелец',goal_head:long,first_ts:seconds,last_ts:seconds,status:'done'}],by_kind:[],unbound:[{...spendRow,role:'reviewer'}]};
const mode={name:'sandbox',title:'Песочница',text:'Рабочая папка и разрешённые владельцем материалы',sandbox:true,source:'helene.json',notes:[],service_here:true,service_installed:false,session0:false,firewall_set:true,choices:[{name:'sandbox',title:'Песочница',text:'Работает только в своей папке и смонтированных материалах.',sandbox:true,needs_admin:false},{name:'interactive',title:'Права владельца',text:'Файлы и процессы доступны с правами владельца.',sandbox:false,needs_admin:false}],service_option:{name:'service',title:'Служба Windows',text:'Работать, когда окно закрыто',needs_admin:true,toggles:[{key:'session0',title:'Доступ к системному сеансу',text:'Отдельные права системы',warning:'',default:false},{key:'firewall',title:'Правило брандмауэра',text:'Доступ с телефона',warning:'',default:true}]},computer_option:{name:'computer',title:'Управление компьютером',text:'Окна, файлы и процессы',warning:'Доступ к данным компьютера',default:false,needs_admin:false,scopes:['read','files','process','apps'].map(key=>({key:'computer.'+key,title:key,text:'Разрешение '+key}))},computer:{enabled:true,scopes:['computer.read'],port:5099,explicit:true},computer_live:{available:true,enabled:true,connected:true,platform:'windows',scopes:['computer.read'],logs:[]},mounts_live:{requests:[]}};
const agents=[{id:'main',name:'Hélène',port:5010,enabled:true,conflict:'',base:true},{id:'research',name:long,port:5012,enabled:false,conflict:'',base:false}];
const config={mode:'local',agent:{name:'Hélène'},owner:{name:'Егор'},agent_mode:'sandbox',model:{provider:'api',framework:'openai',model:'gpt-6.1-sol',base_url:'https://example.invalid/v1',key:'synthetic-key-not-real',reasoning_effort:'high',fallback_model:'fixture-fallback',vision_model:'fixture-vision'},relay:{enabled:false,port:5011},sandbox:{enabled:true,network:true,mounts:[]},computer:{enabled:true,scopes:['computer.read']},telegram:{mode:'bot',bot_token:'',owner_id:'123456789'},voice:{enabled:true,model:'turbo',speak:true,voice:'irina'},images:{enabled:false},phone:{enabled:false},backup:{every_days:3,keep:5,dir:''},owner_context:{messages:50,characters:30000},self_review:{enabled:true}};
const task={id:'team-fixture',goal:long,status:'active',agents:[{id:'worker-fixture',role:'Исследователь интерфейса',model:'gpt-6.1-sol/high/fast',subtask:long,status:'running',started:seconds,observation:{generation:'generation-1',phase:'tool',tool:'read_file',observed_at:at,iteration:3},liveness:{state:'unknown',observed_at:at,basis:'saved observation'},request_ref:{...ref,kind:'request'},result_ref:{...ref,kind:'result'},payload_refs:{input:ref}}]};
const reviewerActivity=()=>({reviewers:[{id:'review/standalone-critic',name:'Независимый ревьюер',execution:'running',live:true,review_id:'review-fixture',model:'fixture-review-model',started_at:seconds-90,observed_at:seconds}],recent_reviewers:[{id:'review/finished',name:'Завершённый ревьюер',execution:'done',live:false,finished_at:seconds,verdict:'findings',summary:long,findings:[{path:'fixture.ts',scenario:'Просмотр',detail:long}]}],recent_reviewers_cursor:null});
const nativeCalls=[],apiCalls=[];
export function fixtureStatus(){return {scene,platform,provider,conflict,delayedPaths,errorPaths,nativeCalls,apiCalls};}
extendFixture((cmd,args)=>{
 nativeCalls.push({cmd,args});
 if(cmd==='config_load'){
 if(scene==='settings-error')throw failure('Синтетическая ошибка чтения настроек — '+long);
 const draft=structuredClone(config);draft.mode=scene==='remote'?'remote':'local';draft.model.provider=provider;
 if(scene==='setup')draft.model.key='';
 return {config:draft,path:'C:/Синтетическая проверка/helene.json',tree:'C:/Пользователи/Очень длинная папка приложения/данные-агента',exe_dir:'C:/Проверка',mtime_ns:scene==='settings-conflict'?'1793800000000000002':'1793800000000000001'};
 }
 if(cmd==='config_save')return scene==='settings-conflict'?{ok:false,code:'stale',mtime_ns:'1793800000000000002',error:'Файл настроек изменился'}:scene==='settings-save-error'?Promise.reject(failure('Не удалось сохранить синтетический файл')):{ok:true,mtime_ns:'1793800000000000002'};
 if(cmd==='voice_shortcuts')return {active:false,bindings:{record:'Ctrl+Alt+Space',pause:'Shift',cancel:'Esc'}};
 if(cmd==='components_status')return [{id:'relay',ready:true,installed:false,download_bytes:5000000},{id:'whisper',ready:true,installed:true},{id:'xtts',ready:false,installed:false,error:'Синтетический отказ комплекта — '+long}];
 if(cmd==='relay_account')return {authorized:false,accounts:[],slots:[]};
 if(cmd==='agents_list')return {agents,current:'main'};
 if(cmd==='local_files_list')return {path:'C:/Synthetic/Папка с длинным названием для сохранения',parent:'C:/Synthetic',locations:[{name:'Домашняя папка',path:'C:/Synthetic'},{name:'Документы с длинным названием',path:'C:/Synthetic/Documents'}],entries:[{name:'Материалы проекта',path:'C:/Synthetic/Materials',folder:true,size:0},{name:long+'.txt',path:'C:/Synthetic/document.txt',folder:false,size:2000},{name:'.hidden.txt',path:'C:/Synthetic/.hidden.txt',folder:false,size:10}],total:3};
 if(cmd==='local_files_save')return args.overwrite?{path:'C:/Synthetic/'+args.name,bytes:2000}:Promise.reject(Error('exists — синтетический файл уже существует'));
 if(cmd==='local_files_read')return {name:'Синтетический документ.txt',mime:'text/plain',size:4,data:'VGVzdA=='};
 if(cmd==='restore_upstream')return {state:'admitted',operation_id:'synthetic-restore'};
 if(cmd==='relay_status')return {running:false,authorized:false,accounts:[],slots:[],note:'Реле не подключено'};
 if(cmd==='relay_accounts')return {accounts:[],slots:[]};
 if(cmd==='extensions_list')return {extensions:[],items:[]};
 if(cmd==='app_info')return {version:'2.3.0',exe_dir:'C:/Проверка',root:'fixture',log:'C:/Проверка/helene.log',platform,arch:'x86_64'};
 if(cmd==='update_status')return {active:false,state:'idle'};
 if(cmd==='update_progress')return null;
 if(cmd==='update_agent_request')return {source_modes:['preserve','clean'],default_source_mode:'preserve'};
 if(cmd==='backup_list')return {dir:'C:/Копии памяти',items:[{name:'backup-20261104.zip',path:'fixture/backup.zip',bytes:1234567,created_at:at}]};
 // Unknown commands retain the existing isolated fixture response. No OS call is ever delegated.
 return undefined;
},async path=>{
 const p=new URL(path,'http://fixture').pathname;
 if(p==='/favicon.ico')return {type:'image/x-icon',bytes:Buffer.alloc(0),status:204};
 if(p==='/api/artifact'){
  const name=new URL(path,'http://fixture').searchParams.get('path')||'';
  if(name.endsWith('.svg'))return {type:'image/svg+xml',bytes:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="680"><rect width="1200" height="680" fill="#82614a"/><text x="60" y="340" fill="white" font-size="48">Synthetic coverage image</text></svg>')};
  if(name.endsWith('.wav')){const samples=8000,bytes=Buffer.alloc(44+samples*2);bytes.write('RIFF');bytes.writeUInt32LE(bytes.length-8,4);bytes.write('WAVEfmt ',8);bytes.writeUInt32LE(16,16);bytes.writeUInt16LE(1,20);bytes.writeUInt16LE(1,22);bytes.writeUInt32LE(8000,24);bytes.writeUInt32LE(16000,28);bytes.writeUInt16LE(2,32);bytes.writeUInt16LE(16,34);bytes.write('data',36);bytes.writeUInt32LE(samples*2,40);return {type:'audio/wav',bytes};}
  return {type:'text/plain; charset=utf-8',bytes:Buffer.from(markdown)};
 }
 if(p==='/config.js')return {type:'text/javascript; charset=utf-8',bytes:Buffer.from('/* Synthetic native override is injected before entry. No installed config is read. */')};
 if(p==='/'){
  const query=new URL(path,'http://fixture').searchParams;
  if(query.has('scene'))setCoverageState({scene:query.get('scene'),platform:query.get('platform')||'windows'});
  const page=query.get('page')||'now',theme=query.get('previewTheme')||'light',scale=Number(query.get('scale')||.9),font=query.get('font')||'hand';
  const injection=`<script>window.DESK_CONFIG_OVERRIDE={base:'',key:'',agent:'Hélène',product:'Hélène'};window.__HELENE__={shell:'python',platform:${JSON.stringify(platform)},invoke:async(cmd,args)=>{const r=await fetch('/__fixture/native',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd,args})});if(!r.ok)throw Error(await r.text());return r.json();},win:()=>{},look:()=>{}};if(new URL(location).searchParams.has('scale'))localStorage.setItem('helene.look',JSON.stringify({uiScale:${scale},font:${JSON.stringify(font)}}));addEventListener('load',()=>{document.documentElement.dataset.theme=${JSON.stringify(theme)};const activate=()=>{const item=document.querySelector('.rail-item[data-view='+${JSON.stringify(page)}+']');if(item){item.click();return true}return false};if(!activate()){const observer=new MutationObserver(()=>{if(activate())observer.disconnect()});observer.observe(document.body,{childList:true,subtree:true})}});</script>`;
  const html=await readFile(new URL('../../dist/index.html',import.meta.url),'utf8');
  return {type:'text/html; charset=utf-8',bytes:Buffer.from(html.replace('</head>',injection+'</head>'))};
 }
 if(p==='/__fixture/coverage/image.svg')return {type:'image/svg+xml',bytes:Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#82614a"/><text x="40" y="180" fill="white" font-size="32">Synthetic attachment</text></svg>')};
 return undefined;
},async(path,method,body)=>{
 const url=new URL(path,'http://fixture'),p=url.pathname;apiCalls.push({path,method});
 if(p==='/__fixture/coverage/state'){if(method==='POST')setCoverageState(body);return fixtureStatus();}
 if(delayedPaths.some(path=>p===path))await wait(delayMs);
 if(errorPaths.some(path=>p===path))throw failure('Синтетический отказ чтения '+p+' — '+long);
 if(p==='/api/state')return {agent:'Hélène',owner:'Егор',level:'live',phrase:'Проверяет интерфейс',runner:{alive:true,busy:!['empty','setup','idle'].includes(scene),run:['empty','setup','idle'].includes(scene)?'':'run-live',since:seconds,pid:4100,ever:true},activity:['empty','setup','idle'].includes(scene)?null:{run_id:'run-live',phase:'tool',tool:'read_file',chat_id:'window',kind:'chat_turn'},brain:{configured:true,model:'gpt-6.1-sol/проверочная-модель',last_call_at:seconds},relay:{used:false,authorized:false},telegram:{enabled:false},anatomy:true,next_wake:at};
 if(p==='/api/runs')return scene==='empty'?[]:url.searchParams.get('kind')==='wake'?rows.filter(r=>r.kind==='wake'):rows;
 if(p.startsWith('/api/run/'))return detail(p.split('/')[3]);
 if(p==='/api/chats')return [{peer_id:'window',title:'Hélène',kind:'window',messages:4,mtime_ns:'1793800000000000001'},{peer_id:'window-long',title:long,kind:'window',messages:0,mtime_ns:'1793800000000000000'},{peer_id:'123456789',title:'Синтетический Telegram чат',kind:'telegram',messages:0,mtime_ns:'1793700000000000000'}];
 if(p.startsWith('/api/chat/')&&scene==='media')return [
 {timestamp:at,outgoing:true,sender_name:'Hélène',text:'Синтетическое изображение для проверки',media_path:'synthetic/image.svg',media_kind:'image',media_name:long+'.svg',media_mime:'image/svg+xml',media_size:2000},
 {timestamp:at,outgoing:true,sender_name:'Hélène',text:'Синтетическая запись без звука',media_path:'synthetic/voice.wav',media_kind:'audio',media_name:long+'.wav',media_mime:'audio/wav',media_size:16044},
 {timestamp:at,outgoing:true,sender_name:'Hélène',text:'Синтетический документ для чтения и сохранения',media_path:'synthetic/document.txt',media_kind:'file',media_name:long+'.txt',media_mime:'text/plain',media_size:2000}
 ];
 if(p.startsWith('/api/chat/'))return scene==='empty'?[]:[{timestamp:at,outgoing:false,sender_name:'Егор',text:long},{timestamp:at,outgoing:true,sender_name:'Hélène',text:markdown},{timestamp:at,outgoing:false,sender_name:'Егор',text:'Как проверить результат?'},{timestamp:at,outgoing:true,sender_name:'Hélène',text:markdown}];
 if(p.startsWith('/api/chat-turns/'))return [];
 if(p==='/api/usage')return {status:'ok',updated_at:seconds,today_total:counts,week_total:counts,models:[{...counts,name:'gpt-6.1-sol/длинная-модель'}],roles:[],days:Array.from({length:7},(_,i)=>({...counts,day:'2026-11-0'+(i+1),recorded:true}))};
 if(p==='/api/allowances')return {providers:[{name:'Тестовая подписка',status:'stale',observed_at:seconds,windows:[{label:'Недельное окно',used_percent:35,remaining_percent:65,resets_at:seconds+86400}]}]};
 if(p==='/api/spend')return spend;
 if(p==='/api/agenda')return {active:scene==='empty'?[]:[{id:'agenda-1',kind:'wake',goal:long,target:long,when:at,recur:'every 1 day',status:'scheduled'}],total:1};
 if(p==='/api/board')return {board:scene==='empty'?'':markdown};
 if(p==='/api/forge/tasks')return {active:scene==='empty'?[]:[task],recent:[],next_cursor:null};
 if(p==='/api/forge/events')return {events:[{seq:1,kind:'model_start',at,generation:'generation-1',iteration:1,model_requested:'gpt-6.1-sol'},{seq:2,kind:'model_result',at,generation:'generation-1',iteration:1,model:'gpt-6.1-sol',output_ref:ref},{seq:3,kind:'tool_start',at,generation:'generation-1',iteration:2,call_id:'call-1',tool:'read_file',input_ref:ref},{seq:4,kind:'tool_result',at,generation:'generation-1',iteration:2,call_id:'call-1',tool:'read_file',output_ref:ref,receipt:{status:'done',kind:'file',path:'C:/Материалы/очень-длинное-имя.md'}}],next_offset:4,next_after_seq:4,has_more:false,generation:'generation-1'};
 if(p==='/api/forge/payload'){const text=markdown.repeat(5),offset=Number(url.searchParams.get('offset')||0),part=Array.from(text).slice(offset,offset+8000).join('');return {sha256:ref.sha256,text:part,offset,total_chars:Array.from(text).length,next_offset:offset+part.length>=text.length?null:offset+part.length};}
 if(p==='/api/forge/control')return method==='POST'?{...body,state:commandState,reason:'Синтетический ответ проверки'}:url.searchParams.has('command_id')?{command_id:url.searchParams.get('command_id'),state:commandState}:{commands:[]};
 if(p==='/api/phone/activity')return reviewerActivity();
 if(p==='/api/settings')return {config:structuredClone(config),mtime_ns:'1793800000000000001'};
 if(p==='/api/agent-config')return {ok:true,why:'',path:'/synthetic-agent/helene.json',config:structuredClone(config),editable:['model','telegram','agent','owner'],mtime_ns:'1793800000000000001'};
 if(p==='/api/computer/remote/state')return {selected:'synthetic-pc',devices:[{id:'synthetic-pc',name:long,connected:true,scopes:['computer.read']},{id:'synthetic-offline',name:'Синтетический недоступный компьютер',connected:false,scopes:[]}]};
 if(p==='/api/shadow')return scene==='empty'?[]:[{stream:'chat-window',captures:2,latest:at},{stream:'chat-'+long,captures:2,latest:at}];
 if(p.endsWith('/captures'))return ['20261104T150000-frame.md','20261104T140000-frame.md'];
 if(p.endsWith('/capture'))return {text:markdown};
 if(p.endsWith('/diff'))return {prefix_bytes:1200,new_len:2500,zones:[{zone:'K — Конституция',state:'same',chars:1200,prefix:1200},{zone:'T — Текущее',state:'changed',chars:1300,was_chars:1100,prefix:30}],diff:['@@ Источник @@','- Было: '+long,'+ Стало: '+long],gone:[]};
 if(p==='/api/shadow-metrics')return Array.from({length:3},(_,i)=>({moment:at,model:'fixture/длинная-модель',K:12000,E:2000,A:3000,T:4000,total_chars:21000,cached_tokens:5000,context_mode:'full',iteration:i}));
 if(p==='/api/md-tree')return scene==='empty'?[]:[{group:'Конституция и голос',root:'soul/',total:203,files:[{path:'soul/SOUL.md',name:'SOUL.md',mtime:seconds},{path:'soul/readonly.md',name:'Длинный подписанный документ — только чтение.md',mtime:seconds,readonly:true},{path:'soul/tail.md',name:'Большой файл — хвост.md',mtime:seconds},{path:'soul/error.md',name:'Недоступный документ.md',mtime:seconds},{path:'soul/archive/SOUL_v1.md',name:'Прошлая версия.md',mtime:seconds-3600}]}];
 if(p==='/api/md'){
  const name=url.searchParams.get('path')||body?.path;
  if(method==='POST'){if(conflict&&body.mtime_ns)throw Object.assign(Error(JSON.stringify({error:'Файл изменился',code:'conflict'})),{status:409});return {ok:true};}
  if(name?.includes('error'))return {error:'Synthetic FileNotFoundError: C:/Пользователи/Длинное название папки/файл.md',code:scene.startsWith('file-')?scene.slice(5):'not_found'};
  return {text:name?.includes('archive')?'<!-- {"reason":"Уточнение источников","by":"Hélène","version":1} -->\n# Предыдущая версия\n'+long:markdown,mtime_ns:'1793800000000000001',readonly:name?.includes('readonly'),tail:name?.includes('tail'),tail_note:name?.includes('tail')?'Показан хвост: начало большого документа не загружено. Правка недоступна.':''};
 }
 if(p==='/api/errors')return scene==='empty'?{llm:[],skips:[]}:{llm:[{ts:seconds,model:'gpt-6.1-sol/длинная-модель',role:'main',err:'429: '+long+'\n'+markdown,retries:3}],skips:[{ts:seconds,stage:'perception',class:'duplicate',detail:long,chat:'window-long'}]};
 if(p==='/api/mode')return mode;
 if(p==='/api/anatomy')return {agent_name:'Hélène',written_at:at,model:{model:'fixture',framework:'openai'},transports:['window','telegram'],sandbox:{enabled:true,container:false,reason:long,hands:[{name:'read_file',touches:'файлы',fence:'path',why:long}]},computer:mode.computer_live,tools:[{name:'read_file',desc:long+' '+long,params:['path','start_line','max_lines'],required:['path'],invocation:'direct'},{name:'very_long_tool_name_for_geometry_probe',desc:long,params:['query'],required:[],invocation:'via_call'}],skills_index:markdown,knobs:{CONTEXT_WINDOW:200000,WORKSPACE_ROOT:'C:/Пользователи/Очень длинное название папки/Рабочие материалы'}};
 if(p==='/api/pulse')return {last:null,calls_day:24,cache_day:83,cache_now:90};
 if(p==='/api/retention')return {entries:[{class:'project',path:'C:/Пользователи/Длинная папка проектов/Исследование интерфейса',bytes:23456789,files:300,age_days:4},{class:'update_staging',path:'fixture/update-staging',bytes:3456789,files:12,age_days:10,sweepable:true}]};
 if(p==='/api/memory-fold/window')return {hot:50,tape_chars:30000};
 if(p==='/api/frame-stats')return {days:7,summary:{calls:24,runs:8,cache_ratio:.83,cached_tokens:100000,input_tokens:120000,output_tokens:3456,cuts:1},by:{iteration:[{group:'первая итерация',calls:24,runs:8,cache_ratio:.83,output_tokens:3456,median_ms:2300,p90_ms:4500,cuts:1}]}};
 if(p==='/api/supervisor')return {kind:'fixture',alive:true,beat_age:1,started_utc:at,in_container:false,children:[{id:'runner',name:'Код агента',alive:true,pid:4100,since_utc:at,falls:0,halted:''}],targets:[],receipt:null,pending:null,control:{available:false,why:'Изолированная проверка — управление отключено'}};
 if(p==='/api/logs')return [{id:'runner',title:'Код агента',exists:true,size:123456,changed_utc:at}];
 if(p.startsWith('/api/log/'))return {ok:true,text:markdown,lines:20};
 if(p==='/api/containers')return {available:false,why:'Локальное приложение',containers:[]};
 if(p==='/api/brain')return {ok:false,why:'Синтетическая проверка'};
 if(p==='/api/brain-models')return {ok:true,by_framework:{}};
 if(p==='/api/autonomy')return {enabled:true,level:'advises',alive:true,hold:false};
 if(p==='/api/self-review')return {enabled:true,state:'idle',history:[],...reviewerActivity()};
 if(p==='/api/update')return {available:false};
 if(p==='/api/models')return ['gpt-6.1-sol','проверочная-длинная-модель'];
 if(p==='/api/relay')return {authorized:false,accounts:[],slots:[]};
 return undefined;
});
