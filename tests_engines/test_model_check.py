"""Validators: VRM 0.x / 1.0 and Live2D Cubism 3+, license metadata, readable errors."""
import json

import pytest

import make_models as M
from engines import model_check as mc
from engines.model_check import ModelError


def test_vrm1_info_and_license(tmp_path):
    info = mc.inspect_vrm(M.write_vrm(tmp_path / 'a.vrm'))
    assert info['engine'] == 'vrm' and info['version'] == 'VRM 1.0' and info['title'] == 'Test Avatar'
    assert {'happy', 'sad', 'blink', 'aa'} <= set(info['expressions']) and info['has_blink'] and info['has_mouth']
    text = ' '.join(info['license_lines'])
    assert 'Test Author' in text and 'vrm.dev/licenses' in text and 'личное некоммерческое' in text and 'запрещены' in text


def test_vrm0_info(tmp_path):
    info = mc.inspect_vrm(M.write_vrm(tmp_path / 'b.vrm', version=0, name='Old'))
    assert info['version'] == 'VRM 0.x' and info['title'] == 'Old' and info['has_mouth'] and 'joy' in info['expressions']
    assert any('CC0' in x for x in info['license_lines'])


def test_vrm_without_license_says_so(tmp_path):
    j, b = M.vrm_json()
    j['extensions']['VRMC_vrm']['meta'] = {'name': 'x'}
    (tmp_path / 'c.vrm').write_bytes(M.glb(j, b))
    assert 'нет сведений о лицензии' in ' '.join(mc.inspect_vrm(tmp_path / 'c.vrm')['license_lines'])


def test_vrm_warnings_without_blink_and_mouth(tmp_path):
    info = mc.inspect_vrm(M.write_vrm(tmp_path / 'd.vrm', expressions=('happy',)))
    assert not info['has_blink'] and not info['has_mouth'] and len(info['warnings']) == 2


@pytest.mark.parametrize('data', [b'hello world, not a model', b''])
def test_vrm_not_glb(tmp_path, data):
    (tmp_path / 'x.vrm').write_bytes(data)
    with pytest.raises(ModelError, match='не GLB'): mc.inspect_vrm(tmp_path / 'x.vrm')


def test_vrm_truncated(tmp_path):
    p = M.write_vrm(tmp_path / 't.vrm'); p.write_bytes(p.read_bytes()[:-40])
    with pytest.raises(ModelError, match='обрезан'): mc.inspect_vrm(p)


def test_plain_glb_is_not_vrm(tmp_path):
    j, b = M.vrm_json(); j.pop('extensions'); j.pop('extensionsUsed')
    (tmp_path / 'p.glb').write_bytes(M.glb(j, b))
    with pytest.raises(ModelError, match='без данных VRM'): mc.inspect_vrm(tmp_path / 'p.glb')


def test_vrm_missing_head_bone(tmp_path):
    with pytest.raises(ModelError, match='head'): mc.inspect_vrm(M.write_vrm(tmp_path / 'h.vrm', with_head=False))


def test_vrm_wrong_extension_and_missing(tmp_path):
    (tmp_path / 'a.txt').write_text('x')
    with pytest.raises(ModelError, match='Нужен файл .vrm'): mc.inspect_vrm(tmp_path / 'a.txt')
    with pytest.raises(ModelError, match='не найден'): mc.inspect_vrm(tmp_path / 'none.vrm')


def test_live2d_valid_folder(tmp_path):
    d = M.write_live2d(tmp_path / 'hero')
    info = mc.inspect_live2d(d)
    assert info['engine'] == 'live2d' and info['entry'] == 'hero.model3.json' and info['version'] == 'moc3 v3'
    assert info['expressions'] == ['F01_smile', 'F02_sad'] and info['motions'] == {'Idle': 1, 'TapBody': 1}
    assert info['has_blink'] and info['has_mouth'] and 'Лицензия модели' in info['license_lines'][0]
    assert mc.inspect_live2d(d / 'hero.model3.json')['entry'] == 'hero.model3.json'


def test_live2d_cubism5_warns_core(tmp_path):
    info = mc.inspect_live2d(M.write_live2d(tmp_path / 'm5', moc_ver=5))
    assert any('Cubism Core 5' in w for w in info['warnings'])


def test_live2d_readme_shown_as_license(tmp_path):
    d = M.write_live2d(tmp_path / 'm'); (d / 'README.txt').write_text('Free for non-commercial use only', 'utf8')
    assert any('non-commercial' in x for x in mc.inspect_live2d(d)['license_lines'])


def test_live2d_without_expressions_warns(tmp_path):
    info = mc.inspect_live2d(M.write_live2d(tmp_path / 'm', expressions=(), motions=()))
    assert len(info['warnings']) == 2


def test_live2d_cubism2_rejected(tmp_path):
    d = tmp_path / 'old'; d.mkdir(); (d / 'shizuku.model.json').write_text('{}'); (d / 'a.moc').write_bytes(b'x')
    with pytest.raises(ModelError, match='Cubism 2'): mc.inspect_live2d(d)
    with pytest.raises(ModelError, match='Cubism 2'): mc.inspect_live2d(d / 'shizuku.model.json')


def test_live2d_missing_pieces(tmp_path):
    d = M.write_live2d(tmp_path / 'm'); (d / 'm.moc3').unlink()
    with pytest.raises(ModelError, match='Moc'): mc.inspect_live2d(d)
    d2 = M.write_live2d(tmp_path / 'm2'); (d2 / 'tex0.png').unlink()
    with pytest.raises(ModelError, match='Textures'): mc.inspect_live2d(d2)
    with pytest.raises(ModelError, match='model3.json'): mc.inspect_live2d(_empty(tmp_path))


def test_live2d_bad_moc_signature(tmp_path):
    d = M.write_live2d(tmp_path / 'm'); (d / 'm.moc3').write_bytes(b'NOPE' + b'\0' * 70)
    with pytest.raises(ModelError, match='MOC3'): mc.inspect_live2d(d)


def test_live2d_path_traversal_rejected(tmp_path):
    d = M.write_live2d(tmp_path / 'm'); (tmp_path / 'secret.moc3').write_bytes(b'MOC3\x03')
    p = d / 'hero.model3.json'; j = json.loads(p.read_text()); j['FileReferences']['Moc'] = '../secret.moc3'; p.write_text(json.dumps(j))
    with pytest.raises(ModelError, match='недопустимый путь'): mc.inspect_live2d(d)


def test_live2d_bad_json(tmp_path):
    d = tmp_path / 'm'; d.mkdir(); (d / 'x.model3.json').write_text('{oops')
    with pytest.raises(ModelError, match='JSON'): mc.inspect_live2d(d)


def test_guess_engine(tmp_path):
    assert mc.guess_engine(tmp_path / 'a.vrm') == 'vrm' and mc.guess_engine(tmp_path / 'a.model3.json') == 'live2d' and mc.guess_engine(tmp_path / 'a.png') is None
    assert mc.guess_engine(M.write_live2d(tmp_path / 'f')) == 'live2d'


def _empty(tmp_path):
    d = tmp_path / 'emptyd'; d.mkdir(); return d
