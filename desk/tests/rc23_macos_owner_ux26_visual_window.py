"""Observe real unmodified renderer opacity/paint over time before acceptance."""
from pathlib import Path

path=Path(__file__).with_name('rc23_macos_owner_ux_window.py')
source=path.read_text()
# Follow the product's ordinary About link once, after group/font layout.
# The earlier driver issued smooth About navigation and a second direct hint
# scroll immediately after it; its initial visible observation was transient.
anchor='    evaluate(win,"(()=>{const about=Array.from(document.querySelectorAll(\'nav a\')).find(a=>a.textContent.trim()===\'О программе\');if(about)about.click();return true})()")'
assert source.count(anchor)==1
source=source.replace(anchor,'''    wait(lambda:evaluate(win,"document.fonts.status==='loaded' && !document.getAnimations().some(a=>a.playState==='running')"))
    time.sleep(.5)
'''+anchor)
second='    evaluate(win,"document.getElementById(\'update-reopen-hint\').scrollIntoView({block:\'center\'});true")'
assert source.count(second)==1
source=source.replace(second,'    # The existing About link owns scrolling; no second competing scroll.')
before="    time.sleep(2);snapshot('owner-ux-update-warning.png')"
after='''    evaluate(win,"requestAnimationFrame(()=>requestAnimationFrame(()=>window.__qaPaintStamp=performance.now()));true")
    wait(lambda:evaluate(win,"window.__qaPaintStamp||0"))
    frames=[]
    for number,delay in enumerate((0,2,5,10)):
        time.sleep(delay)
        state=evaluate(win,"""(()=>{const n=document.getElementById('update-reopen-hint');const chain=[];for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);const r=p.getBoundingClientRect();chain.push({tag:p.tagName,id:p.id,classes:p.className,opacity:s.opacity,visibility:s.visibility,display:s.display,transform:s.transform,top:r.top,bottom:r.bottom,left:r.left,right:r.right,scrollTop:p.scrollTop,scrollHeight:p.scrollHeight})}return {hint:n.textContent,chain,visibility:document.visibilityState,animations:document.getAnimations().map(a=>({state:a.playState,time:a.currentTime,target:a.effect?.target?.className}))}})()""")
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
