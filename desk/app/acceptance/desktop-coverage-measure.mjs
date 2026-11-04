// Geometry is evidence, not visual acceptance. Invoked after font readiness and a paint.
export function measureDesktop() {
 const rect=r=>({x:r.x,y:r.y,width:r.width,height:r.height});
 const selector=e=>e.id?'#'+e.id:e.tagName.toLowerCase()+(e.classList.length?'.'+[...e.classList].join('.'): '');
 const visible=e=>{const c=getComputedStyle(e),r=e.getBoundingClientRect();return c.display!=='none'&&c.visibility!=='hidden'&&r.width>0&&r.height>0&&!e.closest('[hidden]');};
 const intersects=r=>r.right>0&&r.left<innerWidth&&r.bottom>0&&r.top<innerHeight;
 const textInventory=[],clipping=[],overflow=[],controls=[],alignment=[];
 const zoom=Number(getComputedStyle(document.documentElement).zoom)||1;
 for(const e of document.body.querySelectorAll('*')){
  if(!visible(e))continue;
  const c=getComputedStyle(e),r=e.getBoundingClientRect();
  if(e.scrollWidth>e.clientWidth+2&&e.clientWidth>0&&['hidden','clip'].includes(c.overflowX)&&intersects(r))overflow.push({selector:selector(e),text:e.textContent?.trim().slice(0,160),scrollWidth:e.scrollWidth,clientWidth:e.clientWidth,rect:rect(r),overflowX:c.overflowX,ellipsis:c.textOverflow==='ellipsis'});
  if(e.matches('button,input,textarea,select,summary,[role=button],[role=tab],[role=radio],[role=switch],a')&&intersects(r))controls.push({selector:selector(e),label:(e.getAttribute('aria-label')||e.textContent||e.value||'').trim().slice(0,160),rect:rect(r),disabled:!!e.disabled,fontFamily:c.fontFamily,fontSize:c.fontSize,lineHeight:c.lineHeight,padding:c.padding,textAlign:c.textAlign,display:c.display,alignItems:c.alignItems,smallTarget:r.width<24||r.height<24});
  for(const node of e.childNodes){
   if(node.nodeType!==Node.TEXT_NODE||!node.textContent.trim())continue;
   const range=document.createRange();range.selectNodeContents(node);const rects=[...range.getClientRects()].filter(r=>r.width>0&&intersects(r));
   if(!rects.length)continue;
   const record={text:node.textContent.trim(),selector:selector(e),fontFamily:c.fontFamily,fontSize:c.fontSize,effectiveFontSize:parseFloat(c.fontSize)*zoom,lineHeight:c.lineHeight,rects:rects.map(rect)};textInventory.push(record);
   for(let p=e;p&&p!==document.body;p=p.parentElement){
    const pc=getComputedStyle(p),pr=p.getBoundingClientRect();
    for(const tr of rects){
     const xClip=['hidden','clip'].includes(pc.overflowX)&&(tr.left<pr.left-2||tr.right>pr.right+2);
     const yClip=['hidden','clip'].includes(pc.overflowY)&&(tr.top<pr.top-2||tr.bottom>pr.bottom+2);
     if(xClip||yClip)clipping.push({text:record.text,selector:record.selector,ancestor:selector(p),rect:rect(tr),clip:rect(pr),xClip,yClip,ellipsis:pc.textOverflow==='ellipsis',lineClamp:pc.webkitLineClamp});
    }
   }
  }
 }
 for(const row of document.querySelectorAll('.actions,.tools-row,.settings-tabs,.form-grid,.look-fields,.lrn-h-row')){
  if(!visible(row)||!intersects(row.getBoundingClientRect()))continue;
  alignment.push({selector:selector(row),rect:rect(row.getBoundingClientRect()),gap:getComputedStyle(row).gap,children:[...row.children].filter(visible).map(e=>({selector:selector(e),rect:rect(e.getBoundingClientRect()),padding:getComputedStyle(e).padding}))});
 }
 const scroll=document.querySelector('#view');
 return {viewport:{width:innerWidth,height:innerHeight,devicePixelRatio},theme:document.documentElement.dataset.theme,zoom,look:{font:document.documentElement.dataset.lookFont,density:document.documentElement.dataset.density},fonts:{status:document.fonts.status,faces:[...document.fonts].map(f=>({family:f.family,status:f.status,weight:f.weight}))},documentWidth:document.documentElement.scrollWidth,scroll:scroll?{top:scroll.scrollTop,height:scroll.clientHeight,total:scroll.scrollHeight}:null,textInventory,clipping,overflow,controls,alignment,smallText:textInventory.filter(t=>t.effectiveFontSize<11)};
}
