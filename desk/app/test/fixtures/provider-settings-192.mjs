import { ready, extendFixture } from './ui-session-server.mjs';

let revision = 1;
let config = {agent:{name:'Hélène'}, owner:{name:'Fixture'}, model:{provider:'local', framework:'openai',
  base_url:'http://127.0.0.1:1234/v1', model:'fixture-vision', key:'fixture-local-token', reasoning_effort:'max',
  fallback_framework:'openai', fallback_base_url:'https://fallback.invalid/v1', fallback_key:'fixture-fallback-token', fallback_model:'fixture-spare'},
  telegram:{}, relay:{enabled:false,port:5011,key:'fixture-image-token'}, images:{enabled:true}, voice:{enabled:false},
  mode:'local', agent_mode:'interactive'};
const probes=[];
extendFixture((command,args={})=>{
  if(command==='config_load')return {config:structuredClone(config),path:'fixture/helene.json',tree:'fixture/data',exe_dir:'fixture',mtime_ns:revision};
  if(command==='config_save') {config=JSON.parse(args.config);return {ok:true,mtime_ns:++revision};}
  if(command==='probe_model') {probes.push(args);return {ok:true,models:['fixture-vision'],note:'Fixture catalog only; generation not tested'};}
  if(command==='relay_account')return {reachable:true,active_slot:'primary',port:5011,slots:[]};
}, async path=>{
  if(new URL(path,'http://fixture').pathname==='/__provider/status')return {type:'application/json',bytes:JSON.stringify({config,probes,revision})};
});
console.log('PROVIDER_FIXTURE_URL='+await ready);
