import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
const main=readFileSync(new URL('../../ui-kit/window/main.ts',import.meta.url),'utf8');
const block=main.slice(main.indexOf('  let roomsReading:'),main.indexOf('  async function newRoom()'));
assert.ok(block.includes('async function readRooms'));
const stand=stripTypeScriptTypes(block);
const fixture=new Function('fetchRooms',`
 const cfg={base:'old',key:'old-key'}, S={room:'-1',roomName:'group',rooms:[{key:'-1',count:1,mtime:1}],runs:[]};
 const WINDOW_ROOM='window';let roomPicked=true,rendered=0,refreshed=0;
 const foreignHarness=()=>false,buildRooms=(_,chats)=>chats;
 const renderRooms=()=>rendered++,talk={onArchiveEvent:()=>refreshed++};
 ${stand}
 return {cfg,S,loadRooms,get rendered(){return rendered},get refreshed(){return refreshed},get attempted(){return lastRoomsAttempt}};
`);
let pending=[];
const f=fixture(()=>new Promise(resolve=>pending.push(resolve)));
const a=f.loadRooms(),b=f.loadRooms();assert.strictEqual(a,b);assert.equal(pending.length,1);
pending.shift()({runs:[],chats:[{key:'-1',name:'group',count:2,mtime:2}]});await a;
assert.equal(f.refreshed,1,'idle passive message changes feed');assert.equal(f.S.rooms[0].count,2);
const noChange=f.loadRooms();pending.shift()({runs:[],chats:[{key:'-1',name:'group',count:2,mtime:2}]});await noChange;
assert.equal(f.refreshed,1,'unchanged metadata does not reread archive');
const old=f.loadRooms();f.cfg.base='new';f.cfg.key='new-key';const fresh=f.loadRooms();
assert.notStrictEqual(old,fresh);assert.equal(pending.length,2);
const oldResolve=pending.shift(),newResolve=pending.shift();
oldResolve({runs:[],chats:[{key:'-1',name:'wrong profile',count:99,mtime:99}]});await old;
assert.equal(f.S.rooms[0].count,2,'late previous profile cannot replace current rooms');
assert.strictEqual(f.loadRooms(),fresh,'old finalizer cannot clear new flight');
newResolve({runs:[],chats:[{key:'-1',name:'new profile',count:3,mtime:3}]});await fresh;
assert.equal(f.S.rooms[0].name,'new profile');assert.equal(f.refreshed,2);

const timerStart=main.lastIndexOf('  setInterval(',main.indexOf('    const active = !!engineOperation'));
assert.ok(timerStart>=0,'the production passive polling timer must be present');
const timer=main.slice(main.indexOf('() => {',timerStart),main.indexOf('\n  }, 1500);',timerStart)+4);
let polled=0,statePolled=0;
const timerFactory=new Function('loadRooms','refreshState',`
 const document={hidden:false},engineOperation=null,channelAvailable=false,S={agentState:{runner:{busy:false}}};
 const refreshReviewActivity=()=>Promise.resolve();
 const lastStateAttempt=0,lastRoomsAttempt=0;
 return ${stripTypeScriptTypes(timer)};
`);
timerFactory(()=>{polled++;return Promise.resolve()},()=>statePolled++)();
assert.equal(polled,1,'passive polling is independent of busy state and socket availability');assert.equal(statePolled,1);

const load=async name=>import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(readFileSync(new URL('../../ui-kit/'+name,import.meta.url),'utf8'))).toString('base64'));
const {foldWords}=await load('memory-fold.ts');
const state={room:'-1',hot:1360,keep:250,offer_at:400,hard_at:500,owner_context:false,offer:null,pending:{id:'old'},receipt:{state:'running',at:'2026-01-01T00:00:00Z'}};
assert.equal(foldWords(state).state,'unconfirmed');assert.equal(foldWords(state).canFold,false);
for(const status of ['queued','starting','running','interrupted','unconfirmed']) {
 const words=foldWords({...state,operation:{state:status,note:'actual operation'}});
 assert.equal(words.state,status);assert.equal(words.canFold,false,'only the current agent authors its compact');
 assert.match(words.counter,/1\s?360/);
}
assert.equal(foldWords({...state,paused:true}).canFold,false,'pause never implies a model call');
const settingsBody=readFileSync(new URL('../../ui-kit/memory-line.ts',import.meta.url),'utf8');
const predicate=settingsBody.slice(settingsBody.indexOf('export function memoryLineSettingsAvailable'));
const {memoryLineSettingsAvailable}=await import('data:text/javascript;base64,'+Buffer.from(stripTypeScriptTypes(predicate)).toString('base64'));
assert.equal(memoryLineSettingsAvailable(state,true),false,'group settings have no owner scope');
assert.equal(memoryLineSettingsAvailable({...state,owner_context:true},true),true);
assert.equal(memoryLineSettingsAvailable({...state,owner_context:true},false),false,'remote/web local settings cannot change this agent');
const roomsBody=readFileSync(new URL('../../ui-kit/window/rooms.ts',import.meta.url),'utf8');
const roomsBlock=roomsBody.slice(roomsBody.indexOf('export function buildRooms'),roomsBody.indexOf('export async function createRoom')).replace('export function','function');
const actualRooms=new Function(`
 const S={agent:'Fixture'},WINDOW_ROOM='window',LEGACY_WINDOW_KEY='legacy';
 const isWindowRoom=k=>k==='window'||k.startsWith('window-'),readStub=()=>[],runIsLive=s=>s==='running';
 ${stripTypeScriptTypes(roomsBlock)};return buildRooms;
`)();
const runs=[{kind:'chat_turn',chat_id:'-1',status:'done'},{kind:'chat_turn',chat_id:'window',status:'running'}];
const chats=[{peer_id:'-1',kind:'telegram',messages:12,mtime_ns:'123',size:456},{peer_id:'window',kind:'window',messages:7,mtime_ns:'124',size:300}];
const rooms=actualRooms(runs,chats);
assert.equal(rooms.find(r=>r.key==='-1').count,12,'archive counts do not double-count model runs');
assert.equal(rooms.find(r=>r.key==='window').count,7,'primary room retains archive counter');
assert.equal(rooms.find(r=>r.key==='window').live,true,'run activity is independent of counter');
assert.equal(rooms.find(r=>r.key==='window').archiveStamp,'124/300/7');
assert.notEqual(actualRooms(runs,[{...chats[0],mtime_ns:'125'},chats[1]])[1].archiveStamp,rooms[1].archiveStamp,'same-length archive edit changes stamp');
assert.equal(actualRooms(runs,undefined).find(r=>r.key==='-1').count,1,'legacy missing catalog uses runs as estimate');
console.log('Passive chat metadata/counters, singleflight/profile race, idle fallback, fold live/legacy/pause and actual settings scope PASS');
