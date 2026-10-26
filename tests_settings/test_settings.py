"""Offline tests (no display, no audio, no Vosk model): settings store, asset import, emotions, wake grammar."""
import json, sys, time
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import characters as C, settings_store as S, wake as W


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('VOICE_WIDGET_DATA', str(tmp_path / 'data'))
    return tmp_path


def mkframes(n=4, size=(16, 16)):
    out = []
    for i in range(n):
        im = Image.new('RGBA', size, (0, 0, 0, 0)); im.paste((255 * (i % 2), 80, 200 - 30 * i, 255), (2, 2, 10 + i, 12)); out.append(im)
    return out


# ---------------------------------------------------------------- settings store
def test_defaults_and_missing_file(tmp_path):
    cfg = S.load_config(tmp_path / 'config.json', {'a': 1, **S.NEW_DEFAULTS})
    assert cfg['a'] == 1 and cfg['wake_word'] == 'клод' and cfg['character'] == 'crab' and cfg['mascot_opacity'] == 1.0


def test_roundtrip_unicode(tmp_path):
    p = tmp_path / 'config.json'
    S.save_config(p, {'wake_word': 'джарвис', 'x': [1, 2]})
    assert S.load_config(p, S.NEW_DEFAULTS)['wake_word'] == 'джарвис'
    assert 'джарвис' in p.read_text('utf8')  # not \u-escaped


def test_atomic_save_keeps_old_file_on_failure(tmp_path, monkeypatch):
    p = tmp_path / 'config.json'; S.save_config(p, {'v': 1})
    def boom(*a, **k): raise OSError('disk full')
    monkeypatch.setattr(S.os, 'replace', boom)
    with pytest.raises(OSError): S.save_config(p, {'v': 2})
    assert json.loads(p.read_text('utf8')) == {'v': 1}
    assert [f.name for f in tmp_path.iterdir() if f.name != 'data'] == ['config.json']  # no temp leftovers


def test_corrupt_config_falls_back_and_is_kept(tmp_path):
    p = tmp_path / 'config.json'; p.write_text('{broken', 'utf8')
    cfg = S.load_config(p, {'a': 1})
    assert cfg['a'] == 1 and (tmp_path / 'config.bad').exists()


def test_wrong_types_are_dropped(tmp_path):
    p = tmp_path / 'config.json'; p.write_text(json.dumps({'mascot_opacity': 'lots', 'wake_word': 5}), 'utf8')
    cfg = S.load_config(p, S.NEW_DEFAULTS)
    assert cfg['mascot_opacity'] == 1.0 and cfg['wake_word'] == 'клод'


def test_validate_changes_clamps_and_rejects():
    clean, errs = S.validate_changes({'mascot_opacity': 0.01, 'mascot_scale': 99, 'hotkey': 'space', 'toggle_hotkey': '', 'wake_mode': 'x',
                                      'wake_extra_variants': 'клауд; клот', 'junk': 1, 'speak_mode': 'on_request'})
    assert clean['mascot_opacity'] == 0.3 and clean['mascot_scale'] == 12 and clean['toggle_hotkey'] is None
    assert clean['wake_extra_variants'] == ['клауд', 'клот'] and clean['speak_mode'] == 'on_request' and 'junk' not in clean
    assert set(errs) == {'wake_mode'} or set(errs) == {'hotkey', 'wake_mode'}
    assert S.valid_hotkey('ctrl+alt+space') and not S.valid_hotkey('ctrl+alt') and not S.valid_hotkey('')


def test_emotion_map_validation():
    clean, _ = S.validate_changes({'emotion_map': {'completed': 'confirm', 'bogus': 'idle', 'failed': 'nope'}}, C.ALL_EMOTIONS, list(C.STATE_TO_EMOTION))
    assert clean['emotion_map'] == {'completed': 'confirm'}


# ---------------------------------------------------------------- assets
def test_import_gif_and_fps(tmp_path):
    fr = mkframes(5); g = tmp_path / 'a.gif'
    fr[0].save(g, save_all=True, append_images=fr[1:], duration=100, loop=0, disposal=2, transparency=0)
    cid = C.create_character('mine'); assert C.import_emotion(cid, 'idle', g) == 'gif'
    a = C.load_animation(cid, 'idle')
    assert a.source == 'gif' and len(a.frames) == 5 and a.fps == pytest.approx(10, abs=1) and a.loop
    assert a.frames[0].getpixel((0, 0))[3] == 0  # alpha survived


def test_import_frames_folder_natural_order(tmp_path):
    d = tmp_path / 'fr'; d.mkdir(); src = mkframes(3)
    for i, f in zip((1, 2, 10), src): f.save(d / f'f{i}.png')
    cid = C.create_character('mine'); assert C.import_emotion(cid, 'working', d) == 'frames'
    a = C.load_animation(cid, 'working'); assert a.source == 'frames' and len(a.frames) == 3
    assert [f.getpixel((2, 2)) for f in a.frames] == [s.getpixel((2, 2)) for s in src]  # f1, f2, f10 order kept


def test_sprite_sheet_slicing(tmp_path):
    sheet = Image.new('RGBA', (64, 32), (0, 0, 0, 0))
    for i in range(3): sheet.paste((10 * i, 200, 50, 255), (i * 16, 0, i * 16 + 16, 32))   # 3 filled cells of 16 px, rest transparent
    assert len(C.slice_sheet(sheet, 16, 32)) == 3 and len(C.slice_sheet(sheet, 32, 32)) == 2
    sheet.save(tmp_path / 's.png'); cid = C.create_character('mine')
    C.import_emotion(cid, 'success', tmp_path / 's.png', kind='sheet', frame_w=16, frame_h=32, fps=5, loop=False)
    a = C.load_animation(cid, 'success')
    assert a.source == 'sheet' and len(a.frames) == 3 and a.fps == 5 and not a.loop and a.frames[0].size == (16, 32)
    assert C.guess_cell(Image.new('RGBA', (96, 32))) == (32, 32)
    with pytest.raises(C.AssetError): C.slice_sheet(sheet, 100, 100)


def test_corrupt_import_changes_nothing(tmp_path):
    cid = C.create_character('mine'); good = tmp_path / 'g.png'; mkframes(1)[0].save(good); C.import_emotion(cid, 'idle', good, kind='frames')
    bad = tmp_path / 'bad.gif'; bad.write_bytes(b'GIF89a-not-really')
    with pytest.raises(C.AssetError): C.import_emotion(cid, 'idle', bad)
    assert C.emotion_source(cid, 'idle') == 'frames' and C.load_animation(cid, 'idle').source == 'frames'


def test_corrupt_or_missing_file_falls_back_to_builtin_with_warning():
    cid = C.create_character('mine'); d = C.emotion_dir(cid, 'error') / 'frames'; d.mkdir(parents=True)
    (d / '0.png').write_bytes(b'not a png'); logs = []
    a = C.load_animation(cid, 'error', lambda *x: logs.append(' '.join(map(str, x))))
    assert a.source == 'builtin' and a.grid and len(a.frames) == C.PROC_FRAMES and any('fallback' in l for l in logs)
    assert C.load_animation('no-such-char', 'idle', logs.append).source == 'builtin'   # missing character -> crab


def test_import_image_as_character_uses_it_for_all_emotions(tmp_path):
    p = tmp_path / 'x.png'; mkframes(1)[0].save(p)
    cid = C.import_character_image(p, 'Мой Бот')
    assert cid in [c['id'] for c in C.list_characters()]
    assert C.load_animation(cid, 'sleep').source == 'frames'      # fallback = idle image
    assert C.load_animation(cid, 'idle').source == 'frames'


def test_replace_reset_and_builtin_is_read_only(tmp_path):
    p = tmp_path / 'x.png'; mkframes(1)[0].save(p)
    with pytest.raises(C.AssetError): C.import_emotion('crab', 'idle', p, kind='frames')
    cid = C.editable_copy('crab'); assert cid == 'crab-custom' and C.editable_copy(cid) == cid
    C.import_emotion(cid, 'idle', p, kind='frames'); C.import_emotion(cid, 'idle', p, kind='frames', fps=3)
    assert C.emotion_meta(cid, 'idle')['fps'] == 3
    C.reset_emotion(cid, 'idle'); assert C.emotion_source(cid, 'idle') == 'builtin'
    C.set_emotion_meta(cid, 'success', duration=7.0); assert C.load_animation(cid, 'success').duration == 7.0   # meta works for built-in art too


def test_all_builtin_emotions_render():
    for ch in C.BUILTIN:
        for e in C.ALL_EMOTIONS:
            a = C.load_animation(ch, e); assert a.source == 'builtin' and a.frames[0].size == (C.GW, C.GH)
            im = C.render(a.frames[3], 90, 85, True); assert im.size == (90, 85) and im.mode == 'RGB'


def test_render_key_color_and_alpha_edges():
    f = Image.new('RGBA', (4, 4), (255, 0, 255, 255))          # sprite that is literally the key colour
    assert C.render(f, 40, 40, True).getpixel((20, 20)) != C.KEY_RGB
    semi = Image.new('RGBA', (8, 8), (10, 200, 10, 90))
    assert C.render(semi, 8, 8, True).getpixel((4, 4)) == C.KEY_RGB   # faint alpha is cut, no coloured halo


# ---------------------------------------------------------------- emotions / state mapping
def test_state_mapping_table():
    r = C.resolve_emotion
    assert r('completed') == 'success' and r('failed') == 'error' and r('awaiting_clarification') == 'question'
    assert r('awaiting_confirmation') == 'confirm' and r('working') == 'working' and r('idle') == 'idle' and r('whatever') == 'idle'
    assert r('completed', {'completed': 'confirm'}) == 'confirm' and r('completed', {'completed': 'nope'}) == 'success'
    assert set(C.EMOTIONS) <= set(C.STATE_TO_EMOTION.values())


def headless_mascot(cfg=None):
    from mascot import Mascot
    m = object.__new__(Mascot)
    m.cfg = {'character': 'crab', 'sleep_after_min': 10, **(cfg or {})}; m.look_override = {}; m.anims = {}; m.log = lambda *a: None
    m.emotion, m.t0, m.idle_since, m.transient_until, m.override = 'idle', time.time(), time.time(), 0.0, None
    return m


def test_emotion_switching_transient_and_sleep():
    m = headless_mascot()
    m._set_emotion('working'); assert m._current() == 'working'
    m._set_emotion('success'); assert m._current() == 'success' and m.transient_until > time.time()
    m.transient_until = time.time() - 1; assert m._current() == 'idle' and m.emotion == 'idle'     # success is short, then idle
    m.idle_since = time.time() - 11 * 60; assert m._current() == 'sleep'                          # long idle -> sleep
    m._set_emotion('working'); assert m._current() == 'working'                                   # any activity wakes it
    m.cfg['sleep_after_min'] = 0; m._set_emotion('idle'); m.idle_since = 0; assert m._current() == 'idle'   # 0 = never
    m._preview('error', 5); assert m._current() == 'error'; m.override = ('error', time.time() - 1); assert m._current() == 'idle'


def test_look_override_does_not_touch_config():
    m = headless_mascot(); m.look_override = {'character': 'cat'}
    assert m._lk('character') == 'cat' and m.cfg['character'] == 'crab' and m._anim('idle').frames[0].size == (C.GW, C.GH)


# ---------------------------------------------------------------- wake word
VOCAB = {'клод', 'клауд', 'клот', 'клоуд', 'джарвис', 'компьютер', 'привет', 'да', 'нет', 'слушай', 'тест', 'код', 'клад', 'алло', 'клевер', 'алиса', 'клев'}
find = lambda w: 1 if w in VOCAB else -1


def test_grammar_has_word_variants_decoys_unk():
    g, wk, missing = W.build_grammar({'wake_word': 'клод'}, find)
    assert wk[0] == 'клод' and {'клауд', 'клот', 'клоуд'} <= set(wk) and g[-1] == '[unk]' and 'привет' in g and 'код' in g and not missing
    assert not set(wk) & set(g[len(wk):])      # a wake word is never also a decoy


def test_new_word_replaces_old_and_filters_vocab():
    g, wk, missing = W.build_grammar({'wake_word': 'Джарвис', 'wake_extra_variants': ['жарвис']}, find)
    assert wk == ['джарвис'] and missing == ['жарвис'] and 'клод' not in g
    assert W.words({'wake_word': 'Claude'})[0] == 'клод' and W.clean_word('Jarvis') == 'джарвис'


def test_conflicts():
    lv = lambda w, f=find: [lvl for lvl, _ in W.conflicts(w, f)]
    assert 'error' in lv('да')                    # common word
    assert 'warn' in lv('ок')                     # too short
    assert lv('джарвис') == []
    assert 'error' in lv('квазимодо')             # not in vocabulary
    assert any('Попробуйте' in t for _, t in W.conflicts('квазимодо', find))
    assert 'error' in lv('')                      # empty
    assert 'info' in lv('клев')                   # close to a decoy: only informational
    assert W.has_errors(W.conflicts('да')) and not W.has_errors(W.conflicts('джарвис'))


def test_match_by_token_not_substring():
    wk = ['клод', 'клауд']
    assert W.matches('эй клод привет', wk) and W.matches('клауд', wk) and not W.matches('клодовский', wk) and not W.matches('', wk)


def test_phonetic_variants_filtered_by_vocab():
    assert 'клауд' in W.phonetic_variants('клод', find) and all(find(v) > 0 for v in W.phonetic_variants('клод', find))


# ---------------------------------------------------------------- hotkey capture (pure part)
def test_combo_from_event():
    import settings_ui as U
    assert U.combo_from_event(0x4 | 0x20000, 'space', 32) == 'ctrl+alt+space'
    assert U.combo_from_event(0x4, 'Cyrillic_es', 0x43) == 'ctrl+c'      # Russian layout, virtual key C
    assert U.combo_from_event(0, 'F9', 120) == 'f9' and U.combo_from_event(0x1, 'Control_L', 17) is None
