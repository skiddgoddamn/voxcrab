from PIL import Image
import characters


def sprite(w, h, top):
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    for y in range(top, h):
        for x in range(w): im.putpixel((x, y), (200, 100, 50, 255))
    return im


def test_rendered_top_follows_the_sprite_not_the_box():
    fr = sprite(40, 40, 10)                                 # 25% empty on top
    top = characters.rendered_top(fr, 90, 80, False)        # pixel art is fitted to the box height: 2x
    assert top == 80 - 80 + int(10 * 2)
    assert characters.rendered_top(Image.new('RGBA', (8, 8)), 90, 80, False) == 0       # empty frame


def test_small_pixel_art_fills_the_box_with_crisp_pixels():
    out = characters.render(sprite(36, 44, 0), 90, 85, False)
    assert out.size == (90, 85)
    cols = {out.getpixel((x, 84)) for x in range(90)}
    assert (200, 100, 50) in cols and len(cols) <= 3         # key colour + sprite colour only, no blended edge pixels


def test_new_builtin_characters_draw_every_emotion():
    for cid in ('girl_sakura', 'girl_mio', 'girl_yuki', 'cat_black', 'cat_white', 'cat_gray'):
        assert cid in characters.BUILTIN and cid in characters.BUILTIN_RU
        for emo in characters.ALL_EMOTIONS:
            fr = characters.procedural_frames(cid, emo)
            assert len(fr) == characters.PROC_FRAMES and fr[0].size == (characters.GW, characters.GH)
            assert fr[0].getchannel('A').getbbox() is not None
