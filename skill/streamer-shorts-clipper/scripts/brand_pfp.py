"""Channel / streamer profile picture: the subject cut out of a photo, with a
slim white outline, on the brand colour. 800x800 PNG.

    pip install "rembg[cpu]" scipy pillow numpy
    python scripts/brand_pfp.py photo.jpg out.png [preview.png] [model] --color "#D97757"

model: isnet-general-use (clean photos, default) or u2net_human_seg (messy
backgrounds). Small source photos look better upscaled 4x first.
For a Short's streamer badge, resize the result to 240x240 and save it as
assets/streamers/<login>.png.
"""
import sys, os
from PIL import Image, ImageFilter, ImageDraw
from rembg import remove, new_session
ORANGE=(217,119,87,255); SIZE=800
if '--color' in sys.argv:
    _i=sys.argv.index('--color'); _c=sys.argv[_i+1].lstrip('#'); del sys.argv[_i:_i+2]
    ORANGE=tuple(int(_c[j:j+2],16) for j in (0,2,4))+(255,); FIT=600; OUTLINE=8
import numpy as np
from scipy import ndimage
_S={}
def sess_for(m):
    if m not in _S: _S[m]=new_session(m)
    return _S[m]
def clean(cut):
    a=np.array(cut.split()[3]).astype(float)
    a[a<90]=0
    lab,n=ndimage.label(a>0)
    if n>1:
        sizes=ndimage.sum(np.ones_like(a),lab,range(1,n+1)); keep=np.argmax(sizes)+1
        a[lab!=keep]=0
    a=ndimage.binary_fill_holes(a>0)*np.maximum(a,0)
    a=np.where(a>200,255,a)
    cut.putalpha(Image.fromarray(a.astype('uint8'))); return cut
def brand(src,dst,preview=None,model='isnet-general-use',fit=None,fix=None,hi=None,K=2):
    SIZE=800*K; FIT=int(fit or 600)*K; OUTLINE=8*K
    im=Image.open(src).convert('RGBA')
    cut=remove(im,session=sess_for(model))
    if fix: cut=fix(cut,im)
    cut=clean(cut)
    a=cut.split()[3]; bbox=a.point(lambda v:255 if v>20 else 0).getbbox()
    touches=bbox[3]>=im.height-3
    cut=cut.crop(bbox)
    s=(FIT/cut.height if touches else FIT/max(cut.size)); cut=cut.resize((max(1,round(cut.width*s)),max(1,round(cut.height*s))),Image.LANCZOS)
    pad=OUTLINE*3
    layer=Image.new('RGBA',(cut.width+2*pad,cut.height+2*pad),(0,0,0,0)); layer.paste(cut,(pad,pad),cut)
    al=layer.split()[3].point(lambda v:255 if v>100 else 0)
    d=ndimage.distance_transform_edt(np.array(al)==0)
    ring=Image.fromarray((np.clip(OUTLINE+0.5-d,0,1)*255).astype('uint8'))
    canvas=Image.new('RGBA',(SIZE,SIZE),ORANGE)
    x=(SIZE-layer.width)//2; y=(SIZE-layer.height)//2
    if touches: y=SIZE-layer.height+pad+1  # bust photo: sit on bottom edge
    white=Image.new('RGBA',layer.size,(255,255,255,255))
    canvas.paste(white,(x,y),ring); canvas.alpha_composite(layer,(x,y))
    if hi: canvas.convert('RGB').save(hi,optimize=True)
    canvas=canvas.resize((800,800),Image.LANCZOS); SIZE=800
    canvas.convert('RGB').save(dst,optimize=True)
    if preview:
        m=Image.new('L',(SIZE,SIZE),0); ImageDraw.Draw(m).ellipse((0,0,SIZE,SIZE),fill=255)
        p=Image.new('RGB',(SIZE,SIZE),(24,24,24)); p.paste(canvas.convert('RGB'),(0,0),m); p.save(preview)
if __name__=='__main__': brand(*sys.argv[1:])
