"""Builds assets/icons/* from the built-in pixel crab (our own sprite). Needs only Pillow. Run: python tools/make_icons.py"""
import sys
from pathlib import Path
from PIL import Image
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import characters as C

OUT = ROOT / 'assets' / 'icons'; OUT.mkdir(parents=True, exist_ok=True)
SIZES = [16, 24, 32, 48, 64, 128, 256, 512]


def master(size=512):
    fr = C.procedural_frames('crab', 'success')[0]                       # happy crab, arms up
    bb = fr.getbbox(); fr = fr.crop(bb)
    k = max(1, int((size * 0.8) // max(fr.size)))                        # integer scale: crisp pixels
    big = fr.resize((fr.width * k, fr.height * k), Image.NEAREST)
    im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    r = int(size * 0.18); bg = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    from PIL import ImageDraw
    ImageDraw.Draw(bg).rounded_rectangle((0, 0, size - 1, size - 1), r, fill=(38, 40, 58, 255))   # dark tile so the crab reads on any taskbar
    im.alpha_composite(bg); im.alpha_composite(big, ((size - big.width) // 2, (size - big.height) // 2 + int(size * 0.02)))
    return im


def small(m, s):
    """Down-size with nearest first when it is an exact fraction (keeps pixels sharp), else LANCZOS."""
    return m.resize((s, s), Image.NEAREST if 512 % s == 0 and s <= 64 else Image.LANCZOS)


if __name__ == '__main__':
    m = master()
    for s in SIZES: small(m, s).save(OUT / ('icon-%d.png' % s))
    m.save(OUT / 'icon.ico', sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    m.save(OUT / 'icon.icns')                                              # Pillow writes .icns itself (no iconutil needed)
    print('ok', sorted(p.name for p in OUT.iterdir()))
