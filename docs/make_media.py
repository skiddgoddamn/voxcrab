"""Regenerates docs/media/*.png|gif from the built-in pixel characters (no third-party art). Run: python docs/make_media.py"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import characters as C

BG = (30, 33, 46); S = 8
CAST = ['crab', 'girl_sakura', 'girl_mio', 'girl_yuki', 'cat', 'cat_black']


def put(canvas, fr, x, y, s=S):
    big = fr.resize((C.GW * s, C.GH * s), Image.NEAREST); canvas.paste(big, (x, y), big)


def gif(path, emo='idle', frames=24, fps=8):
    w, h = C.GW * S, C.GH * S; pad = 16
    out = []
    for i in range(frames):
        im = Image.new('RGB', (len(CAST) * (w + pad) + pad, h + 2 * pad), BG)
        for k, cid in enumerate(CAST): put(im, C.procedural_frames(cid, emo)[i % C.PROC_FRAMES], pad + k * (w + pad), pad)
        out.append(im.convert('P', palette=Image.ADAPTIVE, colors=128))
    out[0].save(path, save_all=True, append_images=out[1:], duration=int(1000 / fps), loop=0, disposal=2)


def sheet(path, ids=('crab', 'girl_sakura', 'cat_black'), emos=('idle', 'listening', 'thinking', 'speaking', 'success', 'error', 'question', 'sleep'), s=5):
    w, h = C.GW * s, C.GH * s
    im = Image.new('RGB', (len(emos) * w, len(ids) * h), BG)
    for r, cid in enumerate(ids):
        for c, e in enumerate(emos): put(im, C.procedural_frames(cid, e)[6], c * w, r * h, s)
    im.save(path)


if __name__ == '__main__':
    out = ROOT / 'docs' / 'media'; out.mkdir(parents=True, exist_ok=True)
    gif(out / 'characters.gif'); sheet(out / 'emotions.png')
