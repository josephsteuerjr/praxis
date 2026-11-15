import fields from '../resources/machine-display.json';
import {t as uiText} from '../lang/lang';
type Row=Record<string,unknown>;
const record=(v:unknown):Row|null=>v!==null&&typeof v==='object'&&!Array.isArray(v)?v as Row:null;
const spec=fields as Record<string,string[]>;
function valid(v:unknown,depth=0):boolean{
  if(depth>8)return false;
  if(typeof v==='string')return true;
  if(typeof v==='number')return Number.isFinite(v);
  const row=record(v),values=record(row?.values),key=row?.key;
  if(!row||Object.keys(row).sort().join(',')!=='key,schema,values'||row.schema!=='helene.machine-display.v1'
    ||typeof key!=='string'||!Object.hasOwn(spec,key)||!values)return false;
  const wanted=spec[key];
  if(Object.keys(values).length!==wanted.length||wanted.some(k=>!Object.hasOwn(values,k)))return false;
  return key==='join'?Array.isArray(values.parts)&&values.parts.length<=32&&values.parts.every(v=>valid(v,depth+1))
    :Object.values(values).every(v=>valid(v,depth+1));
}
export function machineWords(value:unknown):string{
  if(!record(value)||!valid(value))return '';
  const walk=(value:unknown):string=>{
    const row=record(value);if(!row)return String(value);
    const values=row.values as Row;
    if(row.key==='join')return (values.parts as unknown[]).map(walk).join('; ');
    return uiText('machine.'+row.key,Object.fromEntries(Object.entries(values).map(([k,v])=>[k,walk(v)])));
  };
  return walk(value);
}
export function machineCaption(value:unknown,field:string):string{
  const row=record(value);return machineWords(row?.['display_'+field])||(typeof row?.[field]==='string'?row[field] as string:'');
}
