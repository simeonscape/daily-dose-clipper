"""YouTube channel banner (2560x1440): round icon + small top line + big name +
white tagline pill, kept inside the 1546x423 strip every device shows.

    python scripts/banner.py icon.png "STREAMERS" banner.png \
        --top "THE BEST OF" --tag "The best clips, every day" --color "#D97757"

Fonts: Inter Display (bundled in assets/ and template/assets/).
"""
import os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import math
HERE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W,H=2560,1440; OR=(217,119,87); DARK=(193,95,64)
def _font_path(w):
    for d in (os.path.join(HERE,'assets'), os.path.join(HERE,'template','assets'), '/usr/share/fonts/opentype/inter'):
        p=os.path.join(d,'InterDisplay-'+w+'.otf')
        if os.path.exists(p): return p
    raise FileNotFoundError('InterDisplay-'+w+'.otf')
def font(w,s): return ImageFont.truetype(_font_path(w),s)
def spaced(d,xy,t,f,fill,sp):
    x,y=xy
    for ch in t: d.text((x,y),ch,font=f,fill=fill); x+=d.textlength(ch,font=f)+sp
def spaced_w(d,t,f,sp): return sum(d.textlength(c,font=f) for c in t)+sp*(len(t)-1)
def banner(icon,name,dst,tag='The best clips, every day',top='THE BEST OF',color=None):
    global OR,DARK
    if color:
        c=color.lstrip('#'); OR=tuple(int(c[i:i+2],16) for i in (0,2,4)); DARK=tuple(int(v*0.89) for v in OR)
    shadow=tuple(int(v*0.55) for v in OR); pill_ink=tuple(int(v*0.78) for v in OR)
    bg=Image.new('RGB',(W,H),OR); d=ImageDraw.Draw(bg)
    # soft diagonal bands for depth
    band=Image.new('L',(W,H),0); bd=ImageDraw.Draw(band)
    for i in range(-3,8):
        x=i*520; bd.polygon([(x,0),(x+160,0),(x+160-900,H),(x-900,H)],fill=28)
    bg.paste(DARK,(0,0),band.filter(ImageFilter.GaussianBlur(2)))
    vign=Image.new('L',(W,H),0); ImageDraw.Draw(vign).ellipse((300,250,W-300,H-250),fill=255)
    bg=Image.composite(bg,Image.new('RGB',(W,H),OR),vign.filter(ImageFilter.GaussianBlur(220)))
    d=ImageDraw.Draw(bg)
    # icon in a white ring
    K=0.70; D=int(400*K); ring=max(8,int(12*K))
    ic=Image.open(icon).convert('RGB').resize((D,D),Image.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.2,percent=60,threshold=2))
    m=Image.new('L',(D*4,D*4),0); ImageDraw.Draw(m).ellipse((0,0,D*4,D*4),fill=255); m=m.resize((D,D),Image.LANCZOS)
    f1=font('SemiBold',int(62*K)); f2=font('Black',int(210*K)); f3=font('Medium',int(56*K))
    sp=int(14*K)
    nf=f2; 
    while d.textlength(name,font=nf)>1300-D-ring*2-int(90*K) : nf=font('Black',nf.size-4)
    tw=max(spaced_w(d,top,f1,sp),d.textlength(name,font=nf),d.textlength(tag,font=f3))
    gap=int(90*K); total=D+2*ring+gap+tw; x0=int((W-total)/2); cy=H//2
    # shadow + ring
    sh=Image.new('L',(W,H),0); ImageDraw.Draw(sh).ellipse((x0+4,cy-D//2-ring+12,x0+D+2*ring+4,cy+D//2+ring+12),fill=70)
    bg.paste(shadow,(0,0),sh.filter(ImageFilter.GaussianBlur(16)))
    rm=Image.new('L',(W*1,H),0)
    big=Image.new('L',((D+2*ring)*4,)*2,0); ImageDraw.Draw(big).ellipse((0,0,(D+2*ring)*4,(D+2*ring)*4),fill=255)
    bg.paste((255,255,255),(x0,cy-D//2-ring),big.resize((D+2*ring,)*2,Image.LANCZOS))
    bg.paste(ic,(x0+ring,cy-D//2),m)
    tx=x0+D+2*ring+gap
    a1=f1.getbbox('D'); a2=nf.getbbox('Hg'); a3=f3.getbbox('Th')
    h1=a1[3]-a1[1]; h2=nf.getbbox('H')[3]-nf.getbbox('H')[1]; h3=a3[3]-a3[1]
    desc=nf.getbbox('Hgjpy')[3]-nf.getbbox('H')[3]; g1,g2=int(38*K),int(30*K)+desc; blockh=h1+g1+h2+g2+h3; ty=cy-blockh//2
    spaced(d,(tx,ty-a1[1]),top,f1,(255,240,232),sp)
    y2=ty+h1+g1; d.text((tx-int(6*K),y2-nf.getbbox('H')[1]),name,font=nf,fill=(255,255,255))
    y3=y2+h2+g2
    # pill tagline
    pw=d.textlength(tag,font=f3)+int(64*K); 
    d.rounded_rectangle((tx,y3-int(14*K),tx+pw,y3+h3+int(22*K)),radius=int(40*K),fill=(255,255,255))
    d.text((tx+int(32*K),y3-a3[1]+int(4*K)),tag,font=f3,fill=pill_ink)
    bg.save(dst,optimize=True)


if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument('icon'); ap.add_argument('name'); ap.add_argument('out')
    ap.add_argument('--top',default='THE BEST OF'); ap.add_argument('--tag',default='The best clips, every day')
    ap.add_argument('--color',default='#D97757'); a=ap.parse_args()
    banner(a.icon,a.name,a.out,tag=a.tag,top=a.top,color=a.color); print('saved',a.out)
