"""Observe real unmodified renderer opacity/paint over time before acceptance."""
from pathlib import Path

path=Path(__file__).with_name('rc23_macos_owner_ux_window.py')
source=path.read_text()
before="    time.sleep(2);snapshot('owner-ux-update-warning.png')"
after='''    evaluate(win,"requestAnimationFrame(()=>requestAnimationFrame(()=>window.__qaPaintStamp=performance.now()));true")
    wait(lambda:evaluate(win,"window.__qaPaintStamp||0"))
    frames=[]
    for number,delay in enumerate((0,2,5,10)):
        time.sleep(delay)
        state=evaluate(win,"""(()=>{const n=document.getElementById('update-reopen-hint');const chain=[];for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);const r=p.getBoundingClientRect();chain.push({tag:p.tagName,id:p.id,classes:p.className,opacity:s.opacity,visibility:s.visibility,display:s.display,transform:s.transform,top:r.top,bottom:r.bottom})}return {hint:n.textContent,chain,visibility:document.visibilityState,animations:document.getAnimations().map(a=>({state:a.playState,time:a.currentTime,target:a.effect?.target?.className}))}})()""")
        state['delay_after_previous']=delay;state['screenshot']='owner-ux26-warning-frame-'+str(number)+'.png'
        snapshot(state['screenshot']);frames.append(state)
        save('NATIVE26-WARNING-PAINT-FRAMES.json',frames)
    row=current()
    row['ancestor_opacity_visible']=all(float(n['opacity'])>=.99 and n['visibility']=='visible' and n['display']!='none' for n in frames[-1]['chain'])
    assert row['visible'] and row['ancestor_opacity_visible']
    snapshot('owner-ux-update-warning.png')'''
assert source.count(before)==1
source=source.replace(before,after)
exec(compile(source,str(path),'exec'),{'__name__':'__main__','__file__':str(path)})
