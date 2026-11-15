import {t as uiText,locale} from '../lang/lang';

const fields:Record<string,readonly string[]>={
  reader_failed:[],not_running:[],quota_fallback:['until'],quota_no_fallback:['until'],key_missing:[],model_missing:[],
  relay_signin:[],sleeping:[],thinking:[],wake_wait:['at'],online:[],paused:[],
  'error.auth':[],'error.quota':[],'error.timeout':[],'error.network':[],'error.address':[],
  'error.parameter':[],'error.context':[],'error.policy':[],'error.request':[],'error.server':[],'error.unknown':[],
  'action.restart':[],'action.settings':[],'action.key':[],'action.configure':[],'action.signin':[],
  'alarm.reader_state':[],'alarm.reader_pulse':[],'alarm.restart_loop':['count'],
  'alarm.debt':['minutes','count'],'alarm.long_silence':['minutes'],'alarm.defer_storm':['count'],
};
const record=(value:unknown):Record<string,unknown>|null=>value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:null;
const literal=(value:unknown)=>typeof value==='string'?value:'';
const clock=(at:number)=>new Date(at*1000).toLocaleTimeString(locale(),{hour:'2-digit',minute:'2-digit'});

export function statusWords(value:unknown):string{
  const row=record(value),values=record(row?.values);
  if(!row||Object.keys(row).sort().join(',')!=='kind,schema,values'||row.schema!=='helene.agent-state.v1'
    ||typeof row.kind!=='string'||!Object.hasOwn(fields,row.kind)||!values)return '';
  const required=fields[row.kind];
  if(Object.keys(values).length!==required.length||required.some(key=>!Object.hasOwn(values,key))
    ||Object.values(values).some(value=>typeof value!=='number'||!Number.isFinite(value)||value<0))return '';
  if((row.kind==='wake_wait'&&Number.isNaN(new Date((values.at as number)*1000).getTime()))
    ||(row.kind.startsWith('quota_')&&Number.isNaN(new Date((values.until as number)*1000).getTime())))return '';
  if(row.kind==='quota_fallback'||row.kind==='quota_no_fallback'){
    const until=values.until as number;
    return uiText('status.'+row.kind,{reset:until?uiText('status.reset_time',{time:clock(until)}):''});
  }
  if(row.kind==='wake_wait')return uiText('status.wake_wait',{time:clock(values.at as number)});
  return uiText('status.'+row.kind,Object.fromEntries(Object.entries(values).map(([key,value])=>[key,(value as number).toLocaleString(locale(),{maximumFractionDigits:0})])));
}

export function agentStatePhrase(value:unknown):string{
  const row=record(value),view=record(row?.display_phrase),kind=literal(view?.kind);
  return (kind.startsWith('action.')||kind.startsWith('alarm.')?'':statusWords(view))||literal(row?.phrase);
}
export function stateActionLabel(value:unknown):string{
  const row=record(value),view=record(row?.display_label);
  return (literal(view?.kind).startsWith('action.')?statusWords(view):'')||literal(row?.label);
}
export function stateAlarmText(value:unknown):string{
  const row=record(value),view=record(row?.display_text);
  return (literal(view?.kind).startsWith('alarm.')?statusWords(view):'')||literal(row?.text);
}
