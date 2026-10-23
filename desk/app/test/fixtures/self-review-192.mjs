import { ready, extendFixture } from './ui-session-server.mjs';

let revision = 1, sequence = 0, available = true, failPost = false;
let config = {agent:{name:'Hélène'},owner:{name:'Fixture'},model:{framework:'openai',model:'fixture-critic',base_url:'https://fixture.invalid/v1',key:'fixture-only'},
  telegram:{},mode:'local',agent_mode:'interactive',
  self_review:{after_update:false,after_self_edit:true,depth:'normal',models:[],max_parallel:1,max_iters:20,unknown_setting:'preserved'}};
const reviews = [], commands = [], calls = [];
const subject = 'c'.repeat(64);
function status() {
  return {schema:'helene.self-review.status.v1',settings:config.self_review,reviews,commands,
    capability:{supported:available,available,reason:available?'Source-only fixture; no model calls.':'Selected preserved Forge lacks self-review API v1.'},
    runtime:{self_edit_supported:available,self_edit_note:'Fixture callback; no agent spawned.'}};
}
extendFixture((command,args={})=>{
  if(command==='config_load')return {config:structuredClone(config),path:'fixture/helene.json',tree:'fixture/data',exe_dir:'fixture',mtime_ns:String(revision)};
  if(command==='config_save') {
    if(String(args.mtimeNs)!==String(revision))return {ok:false,code:'stale',error:'stale:'+revision};
    config=JSON.parse(args.config);return {ok:true,mtime_ns:String(++revision)};
  }
},undefined,async(path,method,body)=>{
  const name=new URL(path,'http://fixture').pathname;
  if(name==='/__fixture/self-review/status')return {revision,config,reviews,commands,calls,available};
  if(name==='/__fixture/self-review/set') {
    if(body.conflict)revision++;
    if(body.available!==undefined)available=body.available;
    if(body.failPost!==undefined)failPost=body.failPost;
    if(body.finish)for(const command of commands.filter(row=>row.state==='queued')) {
      command.state='done';
      if(command.action==='request') {
        command.review_id='review-'+(++sequence).toString(16).padStart(16,'0');
        reviews.unshift({id:command.review_id,required:true,trigger:'contested_overlay',execution:'done',state:'needs_attention',verdict:'findings',subject_sha256:subject,
          critics:[{model:'fixture-critic',execution:'done',verdict:'findings',summary:'Long concrete source-only finding; no runtime acceptance.',findings:[{path:'app/localharness/very-long-source-file-name-for-wrapping.py',scenario:'Owner edits conflict with update',detail:'Preserve both versions and inspect the merge before accepting it.'}]}],dispositions:[],gaps:[]});
      } else {
        const review=reviews.find(row=>row.id===command.id);
        if(command.action==='cancel')Object.assign(review,{execution:'cancelled',state:'needs_attention'});
        if(command.action==='disposition')review.dispositions.push({decision:command.decision,reason:command.reason});
      }
    }
    if(body.pending)reviews.unshift({id:'review-ffffffffffffffff',required:false,trigger:'on_request',execution:'running',state:'pending',verdict:'insufficient',subject_sha256:subject,critics:[],dispositions:[],gaps:[]});
    return {ok:true};
  }
  if(name==='/api/self-review') {
    calls.push({method,body});
    if(method==='GET')return status();
    if(failPost)throw Object.assign(Error('Fixture transport interrupted; outcome unknown'),{status:503});
    const command={...body,command_id:'command-'+(++sequence).toString(16).padStart(32,'0'),state:'queued'};
    commands.unshift(command);
    return {ok:true,command_id:command.command_id,state:'queued'};
  }
  if(name==='/api/agent-config') {
    calls.push({method,body,path:name});
    if(method==='GET')return {ok:true,config:structuredClone(config),mtime_ns:String(revision)};
    if(String(body.mtime_ns)!==String(revision))throw Object.assign(Error('Настройки изменились: конфликт. Перечитай их.'),{status:409});
    config={...config,...body.config};return {ok:true,mtime_ns:String(++revision)};
  }
});
console.log('SELF_REVIEW_FIXTURE_URL='+await ready);
