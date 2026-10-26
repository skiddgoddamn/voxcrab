"""State -> what a model does. Pure data + matching, so it is testable without a renderer.

STATES are the ten dialog states from the spec; they map onto the emotion names already used by characters.py
(recognizing = transcribing, clarification = question, sleeping = sleep). The renderer receives one profile per emotion:
  {expression: name|None, expression_weight, motion: [group, index]|None, mouth: 'talk'|'none', blink: bool, body: {...}}
`body` is the procedural fallback (bob / sway / tilt / scale pulse) that works for ANY model, even without expressions or motions.
"""
STATE_ALIASES = {'recognizing': 'transcribing', 'clarification': 'question', 'sleeping': 'sleep'}
STATES = ['idle', 'listening', 'recognizing', 'thinking', 'working', 'speaking', 'success', 'error', 'clarification', 'sleeping']
EMOTIONS = ['idle', 'listening', 'transcribing', 'thinking', 'working', 'speaking', 'success', 'error', 'question', 'confirm', 'sleep']

# emotion -> abstract mood key
MOOD = {'idle': 'neutral', 'listening': 'attentive', 'transcribing': 'attentive', 'thinking': 'think', 'working': 'focus', 'speaking': 'neutral',
        'success': 'happy', 'error': 'sad', 'question': 'surprised', 'confirm': 'surprised', 'sleep': 'sleep'}
MOOD_WEIGHT = {'neutral': 1.0, 'attentive': 0.4, 'think': 0.5, 'focus': 0.3, 'happy': 1.0, 'sad': 1.0, 'surprised': 0.6, 'sleep': 1.0}

# VRM preset names (1.0 and 0.x): first one that the model has
VRM_EXPR = {'neutral': ['neutral'], 'attentive': ['surprised'], 'think': ['relaxed'], 'focus': ['neutral'], 'happy': ['happy', 'joy', 'fun'],
            'sad': ['sad', 'sorrow'], 'surprised': ['surprised'], 'sleep': ['blink']}
# Live2D expressions are model specific: match by name keywords (en / romaji / ru)
L2D_KEYS = {'neutral': ['neutral', 'normal', 'default'], 'attentive': ['surpris', 'wonder'], 'think': ['think', 'trouble', 'worry', 'hmm'],
            'focus': ['serious', 'focus', 'angry'], 'happy': ['happy', 'smile', 'joy', 'laugh', 'fun', 'warai', 'улыб'], 'sad': ['sad', 'cry', 'tear', 'sorrow', 'грус'],
            'surprised': ['surpris', 'shock', 'wow', 'odoroki', 'удив'], 'sleep': ['sleep', 'close', 'shy']}
L2D_MOTION_KEYS = {'happy': ['happy', 'joy', 'tap', 'flick'], 'sad': ['sad'], 'surprised': ['surpris', 'shake'], 'think': ['think']}

# procedural body motion per emotion (alternative when the model has nothing better); speed in rad/s, amplitudes 0..1 of a small base
BODY = {
    'idle': dict(bob=0.15, sway=0.1, tilt=0.0, pulse=0.0, speed=1.2),
    'listening': dict(bob=0.1, sway=0.0, tilt=0.35, pulse=0.0, speed=1.0),
    'transcribing': dict(bob=0.2, sway=0.0, tilt=0.25, pulse=0.2, speed=3.0),
    'thinking': dict(bob=0.05, sway=0.4, tilt=-0.35, pulse=0.0, speed=1.5),
    'working': dict(bob=0.5, sway=0.0, tilt=0.0, pulse=0.0, speed=5.0),
    'speaking': dict(bob=0.3, sway=0.15, tilt=0.0, pulse=0.0, speed=4.0),
    'success': dict(bob=0.8, sway=0.0, tilt=0.0, pulse=0.5, speed=7.0),
    'error': dict(bob=0.0, sway=0.7, tilt=0.0, pulse=0.0, speed=14.0),
    'question': dict(bob=0.1, sway=0.0, tilt=0.6, pulse=0.2, speed=2.0),
    'confirm': dict(bob=0.1, sway=0.0, tilt=0.5, pulse=0.2, speed=2.0),
    'sleep': dict(bob=0.1, sway=0.0, tilt=0.5, pulse=0.0, speed=0.5),
}


def _find(names, keys):
    for k in keys:
        for n in names:
            if k.lower() in str(n).lower(): return n
    return None


def _exact(names, cands):
    low = {str(n).lower(): n for n in names}
    for c in cands:
        if c in low: return low[c]
    return None


def build_profiles(info, overrides=None):
    """info from model_check -> {emotion: profile}. overrides: character.json 'emotion_profile' {emotion|state: {expression, motion}}."""
    engine = info.get('engine'); exprs = list(info.get('expressions') or []); motions = info.get('motions') or {}
    ov = {STATE_ALIASES.get(k, k): v for k, v in (overrides or {}).items() if isinstance(v, dict)}
    out = {}
    for emo in EMOTIONS:
        mood = MOOD[emo]
        expr = _exact(exprs, VRM_EXPR[mood]) if engine == 'vrm' else _find(exprs, L2D_KEYS.get(mood, []))
        if mood == 'neutral' and engine == 'vrm': expr = None   # neutral is the default pose, no need to set it
        motion = None
        if engine == 'live2d' and motions:
            g = _find(motions, ['idle']) if emo == 'idle' else _find(motions, L2D_MOTION_KEYS.get(mood, []))
            if g: motion = [g, 0]
        p = {'expression': expr, 'expression_weight': MOOD_WEIGHT[mood] if expr else 0.0, 'motion': motion,
             'mouth': 'talk' if emo == 'speaking' else 'none', 'blink': emo != 'sleep', 'body': dict(BODY[emo])}
        o = ov.get(emo, {})
        if 'expression' in o and (o['expression'] is None or o['expression'] in exprs):
            p['expression'] = o['expression']; p['expression_weight'] = MOOD_WEIGHT[mood] if o['expression'] else 0.0
        if 'motion' in o and (o['motion'] is None or o['motion'] in motions): p['motion'] = [o['motion'], 0] if o['motion'] else None
        out[emo] = p
    return out


def describe(info):
    """Human-readable capabilities (shown in the settings tab)."""
    return ['Моргание: ' + ('да' if info.get('has_blink') else 'нет'),
            'Рот при речи: ' + ('да' if info.get('has_mouth') else 'нет, вместо этого покачивание головы'),
            'Выражения: ' + (', '.join(info.get('expressions') or []) or 'нет, эмоции передаются позой'),
            'Движения: ' + (', '.join('%s(%d)' % kv for kv in (info.get('motions') or {}).items()) or 'нет')]
