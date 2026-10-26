"""Pure validators for model files (no Tk, no webview): Live2D Cubism 3+ folders and VRM 0.x / 1.0 files.
inspect_live2d / inspect_vrm return an info dict or raise ModelError with a Russian, user-readable message.

info = {engine, title, entry (path relative to root), root (folder to copy; '' for a single .vrm), version, expressions [..],
        motions {group: n}, has_blink, has_mouth, license_lines [..], warnings [..], size}
"""
import json, struct
from pathlib import Path

MAX_MODEL_BYTES = 400 * 1024 * 1024
MAX_FILES = 3000


class ModelError(Exception):
    pass


# ---------------------------------------------------------------- Live2D
def _rel_ok(ref):
    """A reference inside model3.json must stay inside the model folder."""
    if not isinstance(ref, str) or not ref: return False
    p = Path(ref)
    return not p.is_absolute() and '..' not in p.parts and ':' not in ref


def find_model3(path):
    p = Path(path)
    if p.is_file():
        n = p.name.lower()
        if n.endswith('.model3.json'): return p
        if n.endswith('.model.json') or n.endswith('.moc'): raise ModelError('Это модель Cubism 2 (.model.json / .moc). Поддерживаются только Cubism 3, 4 и 5 (.model3.json).')
        raise ModelError('Нужен файл *.model3.json или папка модели Live2D (получено: %s).' % p.name)
    if not p.is_dir(): raise ModelError('Путь не найден: %s' % path)
    found = sorted(x for x in p.rglob('*.model3.json') if len(x.relative_to(p).parts) <= 4)
    if found: return found[0]
    if any(p.rglob('*.model.json')) or any(p.rglob('*.moc')): raise ModelError('В папке модель Cubism 2. Поддерживаются только Cubism 3, 4 и 5 (.model3.json).')
    raise ModelError('В папке нет файла *.model3.json: это не модель Live2D Cubism 3+.')


def _folder_stats(root):
    n = size = 0
    for f in Path(root).rglob('*'):
        if f.is_symlink(): raise ModelError('В папке модели есть символические ссылки, импорт отклонён: %s' % f.name)
        if f.is_file(): n += 1; size += f.stat().st_size
        if n > MAX_FILES: raise ModelError('В папке слишком много файлов (больше %d).' % MAX_FILES)
        if size > MAX_MODEL_BYTES: raise ModelError('Модель больше %d МБ.' % (MAX_MODEL_BYTES // 2**20))
    return size


def inspect_live2d(path):
    m3 = find_model3(path); root = m3.parent
    try: data = json.loads(m3.read_text('utf-8-sig'))
    except Exception as e: raise ModelError('%s: файл не читается как JSON (%s).' % (m3.name, e))
    if not isinstance(data, dict) or data.get('Version') != 3: raise ModelError('%s: ожидается Version 3 (Cubism 3+).' % m3.name)
    refs = data.get('FileReferences')
    if not isinstance(refs, dict): raise ModelError('%s: нет раздела FileReferences.' % m3.name)
    warnings = []

    def need(key, ref):
        if not _rel_ok(ref): raise ModelError('%s: недопустимый путь %r (абсолютные пути и ".." запрещены).' % (key, ref))
        f = root / ref
        if not f.is_file(): raise ModelError('%s: файл не найден: %s' % (key, ref))
        return f

    moc = need('Moc', refs.get('Moc'))
    blob = moc.read_bytes()
    head = blob[:64]
    if head[:4] != b'MOC3': raise ModelError('Moc: %s не является файлом moc3 (нет сигнатуры MOC3).' % moc.name)
    moc_ver = head[4]
    if moc_ver > 5: warnings.append('moc3 версии %d новее известных (1-5), возможна несовместимость с Core.' % moc_ver)
    if moc_ver == 5: warnings.append('Модель собрана в Cubism 5: нужен Cubism Core 5.x (файл live2dcubismcore.min.js из SDK 5).')
    texs = refs.get('Textures')
    if not isinstance(texs, list) or not texs: raise ModelError('Textures: список текстур пуст.')
    for t in texs: need('Textures', t)
    for key in ('Physics', 'Pose', 'DisplayInfo', 'UserData'):
        if refs.get(key):
            if not _rel_ok(refs[key]) or not (root / refs[key]).is_file(): warnings.append('%s: файл %r не найден, пропущен.' % (key, refs[key]))
    expressions = []
    for e in refs.get('Expressions') or []:
        if isinstance(e, dict) and _rel_ok(e.get('File')) and (root / e['File']).is_file(): expressions.append(str(e.get('Name') or Path(e['File']).stem))
        else: warnings.append('Выражение %r: файл не найден, пропущено.' % (e.get('Name') if isinstance(e, dict) else e))
    motions = {}
    for g, items in (refs.get('Motions') or {}).items():
        ok = [i for i in (items or []) if isinstance(i, dict) and _rel_ok(i.get('File')) and (root / i['File']).is_file()]
        if len(ok) != len(items or []): warnings.append('Группа движений %r: часть файлов не найдена.' % g)
        if ok: motions[g] = len(ok)
    groups = {g.get('Name'): g.get('Ids') or [] for g in data.get('Groups') or [] if isinstance(g, dict)}
    has_blink = bool(groups.get('EyeBlink')) or b'ParamEyeLOpen' in blob
    has_mouth = bool(groups.get('LipSync')) or b'ParamMouthOpenY' in blob
    if not expressions: warnings.append('Нет файлов выражений: эмоции будут показаны движениями и покачиванием.')
    if not motions: warnings.append('Нет движений: модель оживляется только дыханием/морганием и покачиванием.')
    lic = ['Лицензия модели в файлах Live2D не хранится. Проверьте условия автора модели.']
    for f in sorted(root.iterdir()):
        if f.is_file() and f.suffix.lower() in ('.txt', '.md') and any(k in f.name.lower() for k in ('licen', 'readme', 'terms', 'rule')):
            try: lic.append('%s: %s' % (f.name, ' '.join(f.read_text('utf8', errors='replace').split())[:400]))
            except OSError: pass
    return {'engine': 'live2d', 'title': m3.name[:-len('.model3.json')], 'entry': m3.relative_to(root).as_posix(), 'root': str(root),
            'version': 'moc3 v%d' % moc_ver, 'expressions': expressions, 'motions': motions, 'has_blink': has_blink, 'has_mouth': has_mouth,
            'license_lines': lic, 'warnings': warnings, 'size': _folder_stats(root)}


# ---------------------------------------------------------------- VRM
V1_LICENSE_RU = {
    'avatarPermission': ('Кто может использовать аватар', {'onlyAuthor': 'только автор', 'onlySeparatelyLicensedPerson': 'только лицензированные лица', 'everyone': 'все'}),
    'commercialUsage': ('Коммерческое использование', {'personalNonProfit': 'личное некоммерческое', 'personalProfit': 'личное коммерческое', 'corporation': 'корпоративное'}),
    'creditNotation': ('Указание авторства', {'required': 'обязательно', 'unnecessary': 'не требуется'}),
    'modification': ('Модификации', {'prohibited': 'запрещены', 'allowModification': 'разрешены', 'allowModificationRedistribution': 'разрешены с распространением'}),
}
V1_EMOTIONS = {'neutral', 'blink', 'blinkleft', 'blinkright', 'aa', 'ih', 'ou', 'ee', 'oh'}


def read_glb_json(path):
    p = Path(path)
    size = p.stat().st_size
    if size > MAX_MODEL_BYTES: raise ModelError('Файл больше %d МБ.' % (MAX_MODEL_BYTES // 2**20))
    with p.open('rb') as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b'glTF': raise ModelError('Это не GLB/VRM файл (нет сигнатуры glTF).')
        _, ver, total = struct.unpack('<4sII', head)
        if ver != 2: raise ModelError('Поддерживается только glTF 2.0 (в файле версия %d).' % ver)
        if total != size: raise ModelError('Файл повреждён или обрезан (размер %d, в заголовке %d).' % (size, total))
        ch = f.read(8)
        if len(ch) < 8: raise ModelError('Файл повреждён: нет JSON-блока.')
        ln, typ = struct.unpack('<II', ch)
        if typ != 0x4E4F534A or ln > 64 * 1024 * 1024: raise ModelError('Файл повреждён: первый блок не JSON.')
        raw = f.read(ln)
    try: return json.loads(raw.decode('utf8')), size
    except Exception: raise ModelError('Файл повреждён: JSON-блок не читается.')


def inspect_vrm(path):
    p = Path(path)
    if not p.is_file(): raise ModelError('Файл не найден: %s' % path)
    if p.suffix.lower() not in ('.vrm', '.glb'): raise ModelError('Нужен файл .vrm (получено: %s).' % p.suffix)
    j, size = read_glb_json(p)
    ext = j.get('extensions') or {}
    warnings, lic, title = [], [], p.stem
    if 'VRMC_vrm' in ext:
        v = ext['VRMC_vrm']; meta = v.get('meta') or {}; version = 'VRM 1.0'
        title = meta.get('name') or title
        bones = set((v.get('humanoid') or {}).get('humanBones') or {})
        pre = list(((v.get('expressions') or {}).get('preset') or {})); cust = list(((v.get('expressions') or {}).get('custom') or {}))
        expressions = pre + cust
        if meta.get('authors'): lic.append('Авторы: ' + ', '.join(map(str, meta['authors'])))
        for k, (label, names) in V1_LICENSE_RU.items():
            if k in meta: lic.append('%s: %s' % (label, names.get(meta[k], meta[k])))
        for k, label in (('allowExcessivelyViolentUsage', 'Жестокое использование'), ('allowExcessivelySexualUsage', 'Сексуальное использование'),
                         ('allowPoliticalOrReligiousUsage', 'Политика/религия'), ('allowAntisocialOrHateUsage', 'Антисоциальное/ненависть'), ('allowRedistribution', 'Перераспространение')):
            if k in meta: lic.append('%s: %s' % (label, 'разрешено' if meta[k] else 'запрещено'))
        if meta.get('licenseUrl'): lic.append('Лицензия: ' + str(meta['licenseUrl']))
        if meta.get('copyrightInformation'): lic.append('Copyright: ' + str(meta['copyrightInformation'])[:200])
        if meta.get('contactInformation'): lic.append('Контакт: ' + str(meta['contactInformation'])[:200])
    elif 'VRM' in ext:
        v = ext['VRM']; meta = v.get('meta') or {}; version = 'VRM 0.x'
        title = meta.get('title') or title
        bones = {b.get('bone') for b in (v.get('humanoid') or {}).get('humanBones') or [] if isinstance(b, dict)}
        groups = (v.get('blendShapeMaster') or {}).get('blendShapeGroups') or []
        expressions = [str(g.get('presetName') if g.get('presetName') not in (None, 'unknown') else g.get('name')) for g in groups if isinstance(g, dict)]
        if meta.get('author'): lic.append('Автор: %s' % meta['author'])
        if meta.get('licenseName'): lic.append('Лицензия: %s' % meta['licenseName'])
        for k, label in (('allowedUserName', 'Кто может использовать'), ('commercialUssageName', 'Коммерческое использование'), ('violentUssageName', 'Насилие'), ('sexualUssageName', 'Сексуальный контент')):
            if meta.get(k): lic.append('%s: %s' % (label, meta[k]))
        for k in ('otherLicenseUrl', 'otherPermissionUrl'):
            if meta.get(k): lic.append('%s: %s' % (k, meta[k]))
    else:
        raise ModelError('Это обычный GLB без данных VRM (нет расширения VRM / VRMC_vrm). Экспортируйте модель как VRM.')
    missing = [b for b in ('hips', 'head') if b not in bones]
    if missing: raise ModelError('В VRM нет обязательных костей гуманоида: %s.' % ', '.join(missing))
    if not lic: lic.append('В метаданных VRM нет сведений о лицензии. Проверьте условия автора.')
    low = {e.lower() for e in expressions}
    has_blink = bool(low & {'blink'}); has_mouth = bool(low & {'aa', 'a'})
    if not has_blink: warnings.append('Нет выражения blink: моргание недоступно.')
    if not has_mouth: warnings.append('Нет выражения aa: рот при речи заменён покачиванием головы.')
    if not (low & {'happy', 'angry', 'sad', 'relaxed', 'surprised', 'joy', 'fun', 'sorrow'}):
        warnings.append('Нет эмоциональных выражений (happy/sad/...): эмоции передаются позой.')
    return {'engine': 'vrm', 'title': str(title)[:80], 'entry': p.name, 'root': '', 'version': version, 'expressions': expressions, 'motions': {},
            'has_blink': has_blink, 'has_mouth': has_mouth, 'license_lines': lic, 'warnings': warnings, 'size': size}


def inspect(engine, path):
    if engine == 'live2d': return inspect_live2d(path)
    if engine == 'vrm': return inspect_vrm(path)
    raise ModelError('Неизвестный движок: %s' % engine)


def guess_engine(path):
    p = Path(path); n = p.name.lower()
    if p.is_dir(): return 'live2d' if any(p.rglob('*.model*.json')) or any(p.rglob('*.moc*')) else None
    if n.endswith('.vrm') or n.endswith('.glb'): return 'vrm'
    if n.endswith('.json') or n.endswith('.moc3') or n.endswith('.moc'): return 'live2d'
    return None
