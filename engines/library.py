"""Import / load / delete model characters. A model character is a normal character folder (character.json) with
"engine": "live2d"|"vrm" and the model copied to <char>/model/. Standard characters are untouched."""
import shutil
from pathlib import Path

import characters
from . import model_check, profiles
from .model_check import ModelError

INFO_KEYS = ('version', 'expressions', 'motions', 'has_blink', 'has_mouth', 'license_lines', 'warnings')


def _unique_id(name):
    base = characters.safe_name(name); cid = base; n = 2
    while (characters.user_root() / cid).exists() or cid in characters.BUILTIN:
        cid = '%s-%d' % (base, n); n += 1
    return cid


def import_model(engine, src, name=None):
    """Validate and copy a model into the user characters folder. -> (character id, info). Raises ModelError; leaves nothing behind on failure."""
    info = model_check.inspect(engine, src)
    cid = _unique_id(name or info['title'])
    d = characters.user_root() / cid
    try:
        (d / 'model').mkdir(parents=True)
        if engine == 'live2d': shutil.copytree(info['root'], d / 'model', dirs_exist_ok=True)
        else: shutil.copy2(src, d / 'model' / info['entry'])
        meta = {'name': name or info['title'], 'engine': engine, 'entry': info['entry'], 'base': 'crab', 'fallback': 'builtin',
                'info': {k: info[k] for k in INFO_KEYS}}
        characters.write_json(d / 'character.json', meta)
    except ModelError:
        shutil.rmtree(d, ignore_errors=True); raise
    except OSError as e:
        shutil.rmtree(d, ignore_errors=True); raise ModelError('Не удалось скопировать модель: %s' % e)
    return cid, info


def engine_of(cid):
    e = characters.char_meta(cid).get('engine', 'gif')
    return e if e in ('live2d', 'vrm') else 'gif'


def load_model(cid):
    """Re-validate the stored model (files may have been deleted by hand). -> dict for the renderer. Raises ModelError."""
    meta = characters.char_meta(cid); d = characters.find_dir(cid)
    eng = meta.get('engine')
    if d is None or eng not in ('live2d', 'vrm'): raise ModelError('Персонаж %r не является моделью Live2D/VRM.' % cid)
    mdir = d / 'model'
    entry = mdir / str(meta.get('entry', ''))
    if not entry.is_file(): raise ModelError('Файлы модели не найдены: %s' % entry)
    info = model_check.inspect(eng, entry)
    return {'engine': eng, 'dir': str(mdir), 'entry': info['entry'], 'info': info,
            'profiles': profiles.build_profiles(info, meta.get('emotion_profile'))}


def list_models(engine=None):
    """[{id, name, engine, info}] of installed model characters."""
    out = []
    for c in characters.list_characters():
        if c['builtin']: continue
        m = characters.char_meta(c['id']); e = m.get('engine')
        if e in ('live2d', 'vrm') and (engine in (None, e)): out.append({'id': c['id'], 'name': c['name'], 'engine': e, 'info': m.get('info', {})})
    return out
