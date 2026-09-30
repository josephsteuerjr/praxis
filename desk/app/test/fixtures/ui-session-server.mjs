// Isolated UI replay: built assets + frame.desk.v1 + a simulated native bridge.
// No installed data, model calls, children or platform service commands.
import http from 'node:http';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = fileURLToPath(new URL('../../dist/', import.meta.url));
const version = JSON.parse(await readFile(new URL('../../package.json', import.meta.url), 'utf8')).version;
let pid = 4100, stopped = false, alive = true, busy = true, phase = 'model', room = 'window';
let run = 'run-20260930T190000000000Z-abcdef01', since = Date.now()/1000;
let receipt = null, interruptScope = '', voiceReads = 0, voiceInFlight = 0, maxVoiceInFlight = 0;
let voiceDelay = 0, voiceBusy = 'both', holdInterrupt = false;
let detailOverride = null, pulseOverride = null;
const sockets = new Set();
const messages = Array.from({length:36}, (_, i) => ({timestamp:new Date(Date.now()-(36-i)*60000).toISOString(),
  outgoing: i%2===0, sender_name: i%2===0?'Hélène':'Егор', text: i===35?'Повтор':`Сообщение ${i+1}. Проверка стабильной прокрутки и читаемости переписки.`}));
const stamp = () => new Date().toISOString();
const state = () => ({agent:'Hélène',owner:'Егор',level:busy?'live':'ok',phrase:'На связи',action:null,
  runner:{alive,busy:busy&&alive,run:busy?run:'',since,pid,age_s:0,ever:true},
  activity:busy&&alive?{run_id:run,phase,tool:phase==='tool'?'shell':'',chat_id:room,kind:'chat_turn',event_seq:5}:null,
  brain:{configured:true,model:'fixture',base_url:'',last_call_at:null,last_error:null,last_error_raw:null},
  relay:{used:false,authorized:true},telegram:{enabled:false},next_wake:null,alarms:[],sleep:null,anatomy:true});
const runs = () => [{id:run,kind:'chat_turn',status:busy?'running':'done',chat_id:room,created_at:stamp(),goal_head:'Проверка окна'}];
const owner = () => ({agent_id:'main',supported:true,stopped,runner_alive:alive,pid,note:stopped?'Остановлено владельцем':''});
const config = {agent:{name:'Hélène'},owner:{name:'Егор'},model:{name:'fixture',base_url:'',api_key:''},
  telegram:{enabled:false},voice:{enabled:true,model:'turbo',speak:true,voice:'irina'},mode:'local',agent_mode:'sandbox'};
function frame(data) {
  const bytes=Buffer.from(JSON.stringify(data));
  const head=bytes.length<126?Buffer.from([0x81,bytes.length]):Buffer.from([0x81,126,bytes.length>>8,bytes.length&255]);
  return Buffer.concat([head,bytes]);
}
const emit = () => { for(const s of sockets) s.write(frame({event:{t:'run',run}})); };
function restart() {
  alive=false; busy=false; emit();
  setTimeout(()=>{pid++;alive=true;stopped=false;emit();},900);
}
async function native(cmd,a={}) {
  if(cmd==='owner_state') return owner();
  if(cmd==='owner_control') {
    if(a.action==='panic') {stopped=true;setTimeout(()=>{alive=false;busy=false;emit();},700);}
    else {stopped=false;setTimeout(()=>{pid++;alive=true;emit();},900);}
    return 'Команда принята';
  }
  if(cmd==='engine_restart') {if(stopped)throw Error('Сначала возобнови движок');restart();return 'Перезапуск запрошен';}
  if(cmd==='app_info') return {version,exe_dir:'fixture',root:'fixture',log:'fixture.log',platform:'windows',arch:'x86_64'};
  if(cmd==='config_load') return {config:structuredClone(config),path:'fixture/helene.json',tree:'fixture/data',exe_dir:'fixture',mtime_ns:1};
  if(cmd==='config_save') return {ok:true,mtime_ns:2};
  if(cmd==='service_state') return 'running';
  if(cmd==='agents_list') return [];
  if(cmd==='backup_list') return {dir:'fixture',items:[]};
  if(cmd==='update_check') return {current:version,latest:version,newer:false};
  if(cmd==='voice_fetch') {voiceBusy=a.kind==='speak'?'speech':'model';return 'Скачивание началось';}
  if(cmd==='autostart_get') return false;
  if(cmd==='tailscale_ip'||cmd==='lan_ip') return null;
  return {};
}
async function route(path,method='GET',body=null) {
  const url=new URL(path,'http://fixture'), p=url.pathname;
  if(p==='/__fixture/status') return {fixture:'ui-session-3009',...owner(),busy,phase,room,run,interruptScope,receipt,voiceReads,maxVoiceInFlight};
  if(p==='/__fixture/native') return native(body.cmd,body.args);
  if(p==='/__fixture/set') {
    if(Array.isArray(body.appendMessages)) messages.push(...body.appendMessages);
    if(body.detail!==undefined)detailOverride=body.detail;
    if(body.pulse!==undefined)pulseOverride=body.pulse;
    if(body.reconnect)for(const socket of sockets)socket.end();
    if(body.phase!==undefined)phase=body.phase;
    if(body.busy!==undefined)busy=body.busy;
    if(body.room!==undefined)room=body.room;
    if(body.run!==undefined){run=body.run;since=Date.now()/1000;}
    if(body.receipt!==undefined)receipt=body.receipt;
    if(body.holdInterrupt!==undefined)holdInterrupt=body.holdInterrupt;
    if(body.voiceDelay!==undefined)voiceDelay=body.voiceDelay;
    if(body.voiceBusy!==undefined)voiceBusy=body.voiceBusy;
    emit();return {ok:true};
  }
  if(!alive) throw Object.assign(Error('Движок выключен'),{status:503});
  if(p==='/api/state') return state();
  if(p==='/api/runs') return runs();
  if(p==='/api/chats') return [{peer_id:'window',title:'Hélène',kind:'window',messages:messages.length,mtime_ns:Date.now()*1e6}];
  if(p==='/api/chat/window') return messages;
  if(p==='/api/chat-turns/window') return [];
  if(p.startsWith('/api/run/')) return detailOverride || {run:{...runs()[0],context:{kind:'chat_turn',delivery_chat_id:room},iterations:0},events:[],iterations:[]};
  if(p==='/api/pulse' && pulseOverride) return pulseOverride;
  if(p==='/api/supervisor') return {interrupt_receipt:receipt};
  if(p==='/api/interrupt') {
    interruptScope=body.scope;
    const id='interrupt-'+Date.now();
    if(!holdInterrupt)setTimeout(()=>{busy=false;receipt={id,status:'cancelled'};emit();},600);
    return {ok:true,request:{id,scope:body.scope}};
  }
  if(p==='/api/supervisor/restart') {restart();return {ok:true};}
  if(p==='/api/say') {
    const id='note:fixture-'+Date.now();
    setTimeout(()=>{messages.push({timestamp:stamp(),outgoing:false,sender_name:'Егор',text:body.text,source_id:id});emit();},1200);
    return {written:['control'],source_id:id,stamp:stamp(),midturn:busy,chat:body.chat||'window',attachments:[]};
  }
  if(p==='/api/voice') {
    voiceReads++;voiceInFlight++;maxVoiceInFlight=Math.max(maxVoiceInFlight,voiceInFlight);
    await new Promise(r=>setTimeout(r,voiceDelay));voiceInFlight--;
    const downloading=k=>voiceBusy==='both'||voiceBusy===k;
    return {enabled:true,model:'turbo',ready:true,why:'',dir:'fixture/models',library:{present:true},installed:{model:'turbo',path:'fixture',bytes:500e6},
      catalog:[{id:'turbo',title:'Turbo',note:'Выбранная модель',size_mb:480,installed:true},{id:'small',title:'Small',note:'Маленькая модель',size_mb:160,installed:false}],
      download:downloading('model')?{state:'running',model:'turbo',note:'',got_bytes:30e6,total_bytes:500e6,updated_utc:stamp()}:null,
      speech:{enabled:true,voice:'irina',ready:true,why:'',dir:'fixture/voices',model:'kokoro',library:{present:true},
        catalog:[{id:'irina',title:'Ирина',note:'Русский',size_mb:30,installed:true},{id:'anna',title:'Анна',note:'Другой голос',size_mb:30,installed:false}],
        download:downloading('speech')?{state:'running',voice:'irina',done_mb:5,size_mb:30,at:stamp()}:null}};
  }
  if(p==='/api/pulse')return {last:null};
  if(p==='/api/files')return [];
  if(p==='/api/snapshot')return {};
  if(p==='/api/update')return {available:false};
  if(p==='/api/models')return [];
  if(p==='/api/relay')return {authorized:false};
  if(p==='/api/anatomy')return {};
  return [];
}
const injection=`<script>window.DESK_CONFIG_OVERRIDE={base:'',key:'',agent:'Hélène',product:'Hélène'};window.__HELENE__={shell:'electron',platform:'windows',invoke:async(cmd,args)=>{const r=await fetch('/__fixture/native',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cmd,args})});if(!r.ok)throw Error(await r.text());return r.json();},win:()=>{},look:()=>{}};</script>`;
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2'};
const server=http.createServer(async(req,res)=>{
  try {
    const p=new URL(req.url,'http://fixture').pathname;
    if(p.startsWith('/api/')||p.startsWith('/__fixture/')||p.startsWith('/pair/')) {
      let text='';for await(const chunk of req)text+=chunk;
      const answer=await route(req.url,req.method,text?JSON.parse(text):null);
      res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(answer));return;
    }
    const path=resolve(dist,'.'+decodeURIComponent(p==='/'?'/index.html':p));
    if(!path.startsWith(resolve(dist)+sep))throw Object.assign(Error('path outside fixture'),{status:403});
    let bytes=await readFile(path);
    if(extname(path)==='.html') {
      // Test-only theme override; does not change the host OS or saved preferences.
      const theme = new URL(req.url,'http://fixture').searchParams.get('previewTheme');
      const themeScript = theme==='dark'||theme==='light' ? `<script>addEventListener('load',()=>{document.documentElement.dataset.theme=${JSON.stringify(theme)}})</script>` : '';
      bytes=Buffer.from(bytes.toString().replace('</head>',injection+themeScript+'</head>'));
    }
    res.writeHead(200,{'Content-Type':mime[extname(path)]||'application/octet-stream','Cache-Control':'no-store'});res.end(bytes);
  }catch(e){res.writeHead(e.status||500,{'Content-Type':'text/plain'});res.end(String(e.message));}
});
server.on('upgrade',(req,socket)=>{
  const key=req.headers['sec-websocket-key'];
  if(new URL(req.url,'http://fixture').pathname!=='/tunnel'||!key){socket.destroy();return;}
  socket.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+createHash('sha1').update(key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64')+'\r\n\r\n');
  sockets.add(socket);socket.write(frame({hello:'frame.desk.v1'}));
  let pending=Buffer.alloc(0);
  socket.on('data',data=>{
    pending=Buffer.concat([pending,data]);
    while(pending.length>=2){
      const opcode=pending[0]&15,masked=!!(pending[1]&128);let n=pending[1]&127,offset=2;
      if(n===126){if(pending.length<4)return;n=pending.readUInt16BE(2);offset=4;}
      else if(n===127){if(pending.length<10)return;n=Number(pending.readBigUInt64BE(2));offset=10;}
      if(n>1e6){socket.destroy();return;}
      const mask=masked?pending.subarray(offset,offset+4):null; if(masked)offset+=4;
      if(pending.length<offset+n)return;
      const bytes=Buffer.from(pending.subarray(offset,offset+n));pending=pending.subarray(offset+n);
      if(mask)for(let i=0;i<bytes.length;i++)bytes[i]^=mask[i%4];
      if(opcode===8){socket.end();return;}if(opcode!==1)continue;
      let d;try{d=JSON.parse(bytes.toString());}catch{socket.destroy();return;}
      void route(d.path,d.method,d.body).then(body=>socket.write(frame({id:d.id,status:200,body})),e=>socket.write(frame({id:d.id,status:e.status||500,error:e.message})));
    }
  });
  socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket));
});
server.listen(0,'127.0.0.1',()=>console.log('UI_FIXTURE_URL=http://127.0.0.1:'+server.address().port));
process.on('SIGINT',()=>{for(const s of sockets)s.destroy();server.close(()=>process.exit(0));});
