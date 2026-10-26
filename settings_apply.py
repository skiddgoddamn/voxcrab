"""Applies validated settings to the running widget: persist (atomic), rebuild the wake grammar, devices, hotkeys, mascot look."""
import threading

import characters
import settings_store
import wake

WAKE_KEYS = {'wake_word', 'wake_extra_variants', 'wake_mode', 'wake_min_conf'}
HOTKEY_KEYS = {'hotkey', 'toggle_hotkey', 'settings_hotkey'}
LOOK_KEYS = {'character', 'mascot_scale', 'mascot_opacity', 'mascot_anim_speed', 'sleep_after_min', 'emotion_map',
            'model_scale', 'window_mode', 'pet_patrol', 'compat_mode', 'fullscreen_policy', 'anim_enabled', 'render_fps', 'live2d_core_path', 'click_through', 'prewarm_engine'}


def apply(w, changes, wake_words, log):
    """w = Widget. Returns (messages, errors). wake_words = the widget's live WAKE_WORDS list (updated in place)."""
    clean, errs = settings_store.validate_changes(changes, characters.ALL_EMOTIONS, list(characters.STATE_TO_EMOTION))
    msgs = []
    if 'wake_word' in clean or 'wake_extra_variants' in clean:  # refuse a word the model cannot recognise
        probe = dict(w.cfg); probe.update(clean)
        try: bad = wake.has_errors(wake.conflicts(probe.get('wake_word', 'клод'), wake.model_find))
        except Exception as e: bad = False; log('[settings] vocab check unavailable:', e)
        if bad:
            errs['wake_word'] = 'слово не подходит (см. проверку)'
            for k in WAKE_KEYS: clean.pop(k, None)
    old = {k: w.cfg.get(k) for k in clean}
    w.cfg.update(clean); changed = {k for k in clean if old[k] != clean[k]}
    w.save(); log('[settings] applied', sorted(changed))
    if changed & WAKE_KEYS:
        wake_words[:] = wake.words(w.cfg)
        threading.Thread(target=_rebuild_wake, args=(w, log), daemon=True).start(); msgs.append('Слово активации: %s' % ', '.join(wake_words))
    if 'mic_device' in changed: w.mic_changed = True; msgs.append('Микрофон переключен')
    if 'tts_speaker' in changed and getattr(w, 'tts', None): w.tts.speaker = w.cfg['tts_speaker']
    if 'tts_rate' in changed and getattr(w, 'tts', None): w.tts.rate_mul = w.cfg['tts_rate']
    if changed & HOTKEY_KEYS and getattr(w, 'ready', False):
        try: w.hotkeys(); msgs.append('Горячие клавиши обновлены')
        except Exception as e: errs['hotkey'] = str(e); log('[settings] hotkeys failed', e)
    if w.ui:
        if {'mascot_x', 'mascot_y'} & changed and w.cfg.get('mascot_x') is not None and w.cfg.get('mascot_y') is not None: w.ui.call(w.ui.move_to, w.cfg['mascot_x'], w.cfg['mascot_y'])
        if changed & (LOOK_KEYS | {'mascot_x', 'mascot_y'}): w.ui.apply_settings()
    return msgs, errs


def _rebuild_wake(w, log):
    try:
        det = wake.Detector(w.cfg, log=log); w.rec = det; log('[wake] rebuilt live')
    except Exception as e: log('[wake] rebuild failed', e)
