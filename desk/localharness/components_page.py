"""Small native fallback page using the interface axis, before a session exists."""
from __future__ import annotations
import json
from . import ui_language

KEYS=('intro','start','installed','available','unavailable','install','installing')


def page(root,bridge):
    language=ui_language.for_root(root)
    data={'words':{key:ui_language.text('native.components.'+key,language) for key in KEYS},
          'errors':{code:{'text':ui_language.text('native.app.error.'+code,language),
                          'params':list(params)} for code,params in ui_language.NATIVE_ERRORS.items()}}
    encoded=json.dumps(data,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    document=r'''<!doctype html><html lang="LANGUAGE"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hélène</title>
<style>body{background:#1d1914;color:#e5d7b8;font:18px system-ui;max-width:760px;margin:8vh auto;padding:24px;overflow-wrap:anywhere}button{font:inherit;max-width:100%;white-space:normal;background:#3b3126;color:#e5d7b8;border:1px solid #95846d;border-radius:6px;padding:8px 12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}p{line-height:1.5}button:disabled{opacity:.65}</style></head>
<body><h1>Hélène</h1><p id="intro"></p><div id="components"></div><button id="start"></button><pre id="status" role="status"></pre>
<script id="component-words" type="application/json">WORDS</script><script>BRIDGE</script><script>
(()=>{'use strict';
 const data=JSON.parse(document.getElementById('component-words').textContent),words=data.words;
 const status=document.getElementById('status');
 const caption=(key,values={})=>words[key].replace(/\{([a-zA-Z_][a-zA-Z_0-9]*)\}/g,(full,key)=>Object.hasOwn(values,key)?String(values[key]):full);
 function failure(error){
  const value=error?.native_error,keys=value&&Object.keys(value).sort().join(',');
  if(keys==='code,schema,values'&&value.schema==='helene.native-error.v1'&&Object.hasOwn(data.errors,value.code)){
   const entry=data.errors[value.code],values=value.values;
   if(values&&typeof values==='object'&&!Array.isArray(values)&&Object.keys(values).sort().join(',')===[...entry.params].sort().join(',')&&Object.values(values).every(v=>typeof v==='string'||(typeof v==='number'&&Number.isFinite(v))))
    return entry.text.replace(/\{([a-zA-Z_][a-zA-Z_0-9]*)\}/g,(_,key)=>String(values[key]));
  }
  return String(error);
 }
 document.getElementById('intro').textContent=words.intro;
 const start=document.getElementById('start');start.textContent=words.start;
 async function paint(){
  const rows=await window.__HELENE__.invoke('components_status'),box=document.getElementById('components');box.replaceChildren();
  for(const row of rows){
   const p=document.createElement('p'),state=row.installed===true?'installed':row.ready===true?'available':'unavailable';
   p.textContent=String(row.id)+' · '+words[state];
   if(row.error){const detail=document.createElement('pre');detail.textContent=String(row.error);p.append(detail);}
   const button=document.createElement('button');button.textContent=caption('install',{name:row.id});button.disabled=row.ready!==true||row.installed===true;
   button.onclick=async()=>{button.disabled=true;button.textContent=caption('installing',{name:row.id});status.textContent='';
    try{await window.__HELENE__.invoke('component_install',{name:row.id});await paint();}
    catch(error){status.textContent=failure(error);button.disabled=false;button.textContent=caption('install',{name:row.id});}};
   p.append(' ',button);box.append(p);
  }
 }
 start.onclick=async()=>{start.disabled=true;status.textContent='';try{await window.__HELENE__.invoke('components_start');location.reload();}
  catch(error){status.textContent=failure(error);start.disabled=false;}};
 paint().catch(error=>status.textContent=failure(error));
})();</script></body></html>'''
    # Substitute each owned slot once. Inserted bridge/catalog bytes are never
    # reparsed as template syntax; raw IDs, errors and params only reach textContent.
    slots={'LANGUAGE':language,'WORDS':encoded,'BRIDGE':bridge.replace('</','<\\/')}
    import re
    return re.sub(r'LANGUAGE|WORDS|BRIDGE',lambda match:slots[match[0]],document)
