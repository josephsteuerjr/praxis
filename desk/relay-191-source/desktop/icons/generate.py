"""Reproduce the checked-in vector and native icons (Pillow, no font needed)."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parent
paper, ink, edge = '#e8d5b8', '#57483a', '#c9b394'
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect x="1" y="1" width="62" height="62" rx="15" fill="{paper}" stroke="{edge}"/>
<g fill="none" stroke="{ink}" stroke-width="3" stroke-linecap="round">
<circle cx="32" cy="32" r="9"/>
<path d="M32 13v8m0 22v8M13 32h8m22 0h8M18.5 18.5l5.7 5.7m15.6 15.6 5.7 5.7M18.5 45.5l5.7-5.7m15.6-15.6 5.7-5.7"/>
</g></svg>'''
(root/'icon.svg').write_text(svg, encoding='utf-8', newline='\n')
factor=8
image=Image.new('RGBA',(64*factor,64*factor))
draw=ImageDraw.Draw(image)
draw.rounded_rectangle((factor,factor,63*factor,63*factor),15*factor,fill=paper,outline=edge,width=factor)
draw.ellipse((23*factor,23*factor,41*factor,41*factor),outline=ink,width=3*factor)
for line in [(32,13,32,21),(32,43,32,51),(13,32,21,32),(43,32,51,32),(18.5,18.5,24.2,24.2),(39.8,39.8,45.5,45.5),(18.5,45.5,24.2,39.8),(39.8,24.2,45.5,18.5)]:
    points=[(line[0]*factor,line[1]*factor),(line[2]*factor,line[3]*factor)]
    draw.line(points,fill=ink,width=3*factor)
    for x,y in points:draw.ellipse((x-1.5*factor,y-1.5*factor,x+1.5*factor,y+1.5*factor),fill=ink)
image=image.resize((256,256),Image.Resampling.LANCZOS)
image.save(root/'icon.png')
image.save(root/'icon.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print('Beige native/vector icons generated')
