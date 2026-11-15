/* Only explicitly keyed product nodes are localized. Form values stay untouched. */
(function () {
  'use strict';
  const source=window.HELENE_UI;
  if(!source || !source.catalogs)throw new Error('Native interface catalogs are unavailable');
  const languages=['ru','en','fr','zh'];
  let preference=source.preference || '';
  const current=()=>preference || source.system_language || source.lang || 'en';
  function text(key,values) {
    const choices=[...new Set([current(),'en','ru'])];
    const value=choices.map(lang=>source.catalogs[lang]?.[key]).find(s=>typeof s==='string'&&s);
    if(!value)throw new Error('Unknown native interface caption: '+key);
    if(values===undefined)return value;
    const used=new Set();
    const formatted=value.replace(/\{([A-Za-z_][A-Za-z_0-9]*)\}/g,(_,name)=>{
      if(!Object.hasOwn(values,name))throw new Error('Missing native caption parameter: '+key+':'+name);
      used.add(name);return String(values[name]);
    });
    if(Object.keys(values).some(name=>!used.has(name)))throw new Error('Unexpected native caption parameter: '+key);
    return formatted;
  }
  class OwnedError extends Error {
    constructor(key,values){super(text(key,values));this.key=key;this.values=values;}
  }
  function apply(root=document) {
    document.documentElement.lang=current()==='zh'?'zh-Hans':current();
    for(const node of root.querySelectorAll('[data-i18n]'))node.textContent=text(node.dataset.i18n);
    for(const attr of ['placeholder','aria-label','title']){
      const name='data-i18n-'+attr;
      for(const node of root.querySelectorAll('['+name+']'))node.setAttribute(attr,text(node.getAttribute(name)));
    }
  }
  function setLanguage(value) {
    if(value!==''&&!languages.includes(value))throw new Error('Unsupported interface language');
    preference=value;
  }
  function progress(value) {
    if(value?.label_key)return text(value.label_key,value.label_values);
    const row=(source.progress || []).find(row=>row.stage===value?.stage&&row.text===value?.label);
    return row?text(row.key):String(value?.label || '');
  }
  function errorText(value){
    if(value instanceof OwnedError)return text(value.key,value.values);
    const caption=value?.native_error,fields=source.native_errors;
    if(caption&&Object.keys(caption).sort().join(',')==='code,schema,values'
      &&caption.schema==='helene.native-error.v1'&&typeof caption.code==='string'
      &&fields&&Object.hasOwn(fields,caption.code)){
      const expected=fields[caption.code],values=caption.values;
      if(Array.isArray(expected)&&values&&typeof values==='object'&&!Array.isArray(values)
        &&Object.keys(values).length===expected.length&&expected.every(key=>Object.hasOwn(values,key))
        &&Object.values(values).every(v=>typeof v==='string'||typeof v==='number'&&Number.isFinite(v)))
        return text('native.app.error.'+caption.code,values);
    }
    return value instanceof Error?value.message:String(value);
  }
  window.HELENE_I18N={text,apply,setLanguage,current,preference:()=>preference,progress,errorText,
    ownedError:(key,values)=>new OwnedError(key,values)};
})();
