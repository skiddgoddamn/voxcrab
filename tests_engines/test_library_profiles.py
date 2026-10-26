"""Import into the characters folder, loading, deletion, emotion profiles for all ten states."""
import pytest

import characters
import make_models as M
from engines import library, profiles
from engines.model_check import ModelError


def test_import_vrm_creates_character(tmp_path):
    cid, info = library.import_model('vrm', M.write_vrm(tmp_path / 'src.vrm'), 'Мой аватар')
    d = characters.find_dir(cid)
    assert (d / 'model' / 'src.vrm').is_file() and library.engine_of(cid) == 'vrm'
    meta = characters.char_meta(cid)
    assert meta['engine'] == 'vrm' and meta['base'] == 'crab' and 'license_lines' in meta['info']
    assert [m['id'] for m in library.list_models('vrm')] == [cid] and library.list_models('live2d') == []
    assert next(c for c in characters.list_characters() if c['id'] == cid)['engine'] == 'vrm'


def test_import_live2d_copies_folder_and_unique_ids(tmp_path):
    src = M.write_live2d(tmp_path / 'hero')
    a, _ = library.import_model('live2d', src, 'hero'); b, _ = library.import_model('live2d', src / 'hero.model3.json', 'hero')
    assert a != b and (characters.find_dir(a) / 'model' / 'm.moc3').is_file()
    loaded = library.load_model(b)
    assert loaded['engine'] == 'live2d' and loaded['entry'] == 'hero.model3.json' and set(loaded['profiles']) == set(profiles.EMOTIONS)


def test_failed_import_leaves_nothing(tmp_path):
    (tmp_path / 'bad.vrm').write_bytes(b'junk')
    with pytest.raises(ModelError): library.import_model('vrm', tmp_path / 'bad.vrm', 'bad')
    assert not characters.user_root().exists() or not any(characters.user_root().iterdir())


def test_load_model_detects_deleted_files(tmp_path):
    cid, _ = library.import_model('vrm', M.write_vrm(tmp_path / 's.vrm'), 'x')
    (characters.find_dir(cid) / 'model' / 's.vrm').unlink()
    with pytest.raises(ModelError, match='не найдены'): library.load_model(cid)
    with pytest.raises(ModelError): library.load_model('crab')


def test_delete_and_standard_untouched(tmp_path):
    cid, _ = library.import_model('vrm', M.write_vrm(tmp_path / 's.vrm'), 'x')
    characters.delete_character(cid)
    assert library.list_models() == [] and library.engine_of('crab') == 'gif' and library.engine_of('nonexistent') == 'gif'


def test_all_ten_states_have_profiles():
    assert len(profiles.STATES) == 10
    p = profiles.build_profiles({'engine': 'vrm', 'expressions': ['happy', 'sad', 'surprised', 'relaxed', 'blink', 'aa']})
    for st in profiles.STATES: assert profiles.STATE_ALIASES.get(st, st) in p
    assert p['success']['expression'] == 'happy' and p['error']['expression'] == 'sad' and p['question']['expression'] == 'surprised'
    assert p['thinking']['expression'] == 'relaxed' and p['idle']['expression'] is None
    assert p['speaking']['mouth'] == 'talk' and p['idle']['mouth'] == 'none' and p['sleep']['blink'] is False and p['idle']['blink'] is True


def test_profile_emotions_match_character_emotions():
    assert set(profiles.EMOTIONS) == set(characters.ALL_EMOTIONS)


def test_vrm0_names_and_missing_expressions_fall_back_to_body_motion():
    p = profiles.build_profiles({'engine': 'vrm', 'expressions': ['joy', 'sorrow']})
    assert p['success']['expression'] == 'joy' and p['error']['expression'] == 'sorrow'
    bare = profiles.build_profiles({'engine': 'vrm', 'expressions': []})
    assert all(v['expression'] is None for v in bare.values())
    assert bare['error']['body']['sway'] > 0.5 and bare['success']['body']['bob'] > 0.5     # procedural alternative is always present


def test_live2d_keyword_matching_and_motions():
    p = profiles.build_profiles({'engine': 'live2d', 'expressions': ['F01_smile', 'F02_Sad', 'angry'], 'motions': {'Idle': 3, 'TapBody': 2}})
    assert p['success']['expression'] == 'F01_smile' and p['error']['expression'] == 'F02_Sad' and p['working']['expression'] == 'angry'
    assert p['idle']['motion'] == ['Idle', 0] and p['success']['motion'] == ['TapBody', 0] and p['error']['motion'] is None


def test_profile_overrides_validated():
    info = {'engine': 'live2d', 'expressions': ['a', 'b'], 'motions': {'Idle': 1}}
    p = profiles.build_profiles(info, {'error': {'expression': 'b'}, 'clarification': {'expression': 'a'}, 'success': {'expression': 'nope'}})
    assert p['error']['expression'] == 'b' and p['question']['expression'] == 'a' and p['success']['expression'] is None


def test_describe_lists_capabilities():
    text = ' '.join(profiles.describe({'has_blink': False, 'has_mouth': False, 'expressions': [], 'motions': {}}))
    assert 'Моргание: нет' in text and 'покачивание' in text and 'эмоции передаются позой' in text
