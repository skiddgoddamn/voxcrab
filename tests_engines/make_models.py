"""Synthetic model files for tests: a tiny valid VRM 1.0 / 0.x (GLB) and a Live2D-shaped folder (structure only, not renderable).
Nothing here is a copy of third-party assets."""
import json, struct
from pathlib import Path

HUMAN = ['hips', 'spine', 'chest', 'neck', 'head', 'leftUpperArm', 'leftLowerArm', 'leftHand', 'rightUpperArm', 'rightLowerArm', 'rightHand',
         'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'rightUpperLeg', 'rightLowerLeg', 'rightFoot']
POS = {'hips': (0, 0.9, 0), 'spine': (0, 0.1, 0), 'chest': (0, 0.2, 0), 'neck': (0, 0.2, 0), 'head': (0, 0.1, 0),
       'leftUpperArm': (0.2, 0.15, 0), 'leftLowerArm': (0.25, 0, 0), 'leftHand': (0.25, 0, 0),
       'rightUpperArm': (-0.2, 0.15, 0), 'rightLowerArm': (-0.25, 0, 0), 'rightHand': (-0.25, 0, 0),
       'leftUpperLeg': (0.1, -0.05, 0), 'leftLowerLeg': (0, -0.4, 0), 'leftFoot': (0, -0.4, 0),
       'rightUpperLeg': (-0.1, -0.05, 0), 'rightLowerLeg': (0, -0.4, 0), 'rightFoot': (0, -0.4, 0)}
PARENT = {'spine': 'hips', 'chest': 'spine', 'neck': 'chest', 'head': 'neck', 'leftUpperArm': 'chest', 'leftLowerArm': 'leftUpperArm', 'leftHand': 'leftLowerArm',
          'rightUpperArm': 'chest', 'rightLowerArm': 'rightUpperArm', 'rightHand': 'rightLowerArm', 'leftUpperLeg': 'hips', 'leftLowerLeg': 'leftUpperLeg',
          'leftFoot': 'leftLowerLeg', 'rightUpperLeg': 'hips', 'rightLowerLeg': 'rightUpperLeg', 'rightFoot': 'rightLowerLeg'}


def _box():
    x0, x1, y0, y1, z0, z1 = -0.3, 0.3, 0.0, 1.7, -0.15, 0.15
    v = [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)]
    idx = [0, 1, 2, 0, 2, 3, 5, 4, 7, 5, 7, 6, 4, 0, 3, 4, 3, 7, 1, 5, 6, 1, 6, 2, 3, 2, 6, 3, 6, 7, 4, 5, 1, 4, 1, 0]
    return v, idx


def glb(j, binary):
    js = json.dumps(j).encode(); js += b' ' * (-len(js) % 4); binary += b'\0' * (-len(binary) % 4)
    body = struct.pack('<II', len(js), 0x4E4F534A) + js + struct.pack('<II', len(binary), 0x004E4942) + binary
    return struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body


def vrm_json(version=1, name='Test Avatar', expressions=('happy', 'sad', 'blink', 'aa', 'surprised'), with_head=True):
    v, idx = _box()
    pos = b''.join(struct.pack('<fff', *p) for p in v); ind = b''.join(struct.pack('<H', i) for i in idx)
    binary = pos + ind
    nodes = [{'name': 'Root', 'children': [1, 100 + 0], 'mesh': 0}]       # placeholder, rebuilt below
    names = [h for h in HUMAN if with_head or h != 'head']
    nodes = [{'name': 'Body', 'mesh': 0}]
    index = {}
    for n in names:
        index[n] = len(nodes); nodes.append({'name': n, 'translation': list(POS[n])})
    for n in names:
        p = PARENT.get(n)
        if p and p in index: nodes[index[p]].setdefault('children', []).append(index[n])
    root_children = [index['hips']]
    nodes.append({'name': 'Scene', 'children': [0] + root_children}); scene_node = len(nodes) - 1
    j = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [scene_node]}], 'nodes': nodes,
         'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'material': 0}]}],
         'materials': [{'name': 'skin', 'pbrMetallicRoughness': {'baseColorFactor': [0.9, 0.6, 0.5, 1], 'metallicFactor': 0, 'roughnessFactor': 1}}],
         'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 8, 'type': 'VEC3', 'min': [-0.3, 0, -0.15], 'max': [0.3, 1.7, 0.15]},
                       {'bufferView': 1, 'componentType': 5123, 'count': len(idx), 'type': 'SCALAR'}],
         'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(pos), 'target': 34962}, {'buffer': 0, 'byteOffset': len(pos), 'byteLength': len(ind), 'target': 34963}],
         'buffers': [{'byteLength': len(binary)}]}
    colors = {'happy': [0.1, 0.9, 0.1, 1], 'sad': [0.1, 0.2, 0.95, 1]}
    if version == 1:
        j['extensionsUsed'] = ['VRMC_vrm']
        ex = {}
        for e in expressions:
            ex[e] = {'materialColorBinds': [{'material': 0, 'type': 'color', 'targetValue': colors[e]}]} if e in colors else {}
        j['extensions'] = {'VRMC_vrm': {'specVersion': '1.0', 'meta': {'name': name, 'version': '1', 'authors': ['Test Author'], 'licenseUrl': 'https://vrm.dev/licenses/1.0/',
                                                                       'avatarPermission': 'everyone', 'commercialUsage': 'personalNonProfit', 'creditNotation': 'required',
                                                                       'modification': 'prohibited', 'allowRedistribution': False},
                                        'humanoid': {'humanBones': {n: {'node': index[n]} for n in names}}, 'expressions': {'preset': ex}}}
    else:
        j['extensionsUsed'] = ['VRM']
        groups = [{'name': e.capitalize(), 'presetName': {'happy': 'joy', 'sad': 'sorrow', 'aa': 'a'}.get(e, e), 'binds': [], 'materialValues': []} for e in expressions]
        j['extensions'] = {'VRM': {'specVersion': '0.0', 'meta': {'title': name, 'author': 'Test Author', 'licenseName': 'CC0', 'allowedUserName': 'Everyone',
                                                                    'commercialUssageName': 'Disallow'},
                                   'humanoid': {'humanBones': [{'bone': n, 'node': index[n]} for n in names]},
                                   'blendShapeMaster': {'blendShapeGroups': groups}}}
    return j, binary


def write_vrm(path, **kw):
    j, b = vrm_json(**kw); Path(path).write_bytes(glb(j, b)); return Path(path)


def write_live2d(folder, expressions=('F01_smile', 'F02_sad'), motions=('Idle', 'TapBody'), with_physics=True, moc_ver=3):
    d = Path(folder); d.mkdir(parents=True, exist_ok=True)
    (d / 'm.moc3').write_bytes(b'MOC3' + bytes([moc_ver]) + b'\0' * 59 + b'ParamEyeLOpen\0ParamMouthOpenY\0')
    (d / 'tex0.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    refs = {'Moc': 'm.moc3', 'Textures': ['tex0.png']}
    if with_physics: (d / 'm.physics3.json').write_text('{}'); refs['Physics'] = 'm.physics3.json'
    if expressions:
        refs['Expressions'] = []
        for e in expressions:
            (d / (e + '.exp3.json')).write_text('{"Type":"Live2D Expression"}'); refs['Expressions'].append({'Name': e, 'File': e + '.exp3.json'})
    if motions:
        refs['Motions'] = {}
        for m in motions:
            (d / (m + '.motion3.json')).write_text('{}'); refs['Motions'][m] = [{'File': m + '.motion3.json'}]
    (d / 'hero.model3.json').write_text(json.dumps({'Version': 3, 'FileReferences': refs, 'Groups': [{'Target': 'Parameter', 'Name': 'EyeBlink', 'Ids': ['ParamEyeLOpen']}]}))
    return d
