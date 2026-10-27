import types
import numpy as np
import pytest
import meeting as M

SR = M.SR


def tone(sec, amp=0.3, f=300):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def test_assemble_places_blocks_by_wall_clock_and_leaves_gaps_silent():
    a = tone(1.0)
    blocks = [(1.0, a), (4.0, a)]                        # loopback was silent between 1 s and 3 s: no blocks arrived
    out = M.assemble(blocks, 0.0, 5.0)
    assert len(out) == 5 * SR
    assert M.rms(out[:SR]) > 0.1 and M.rms(out[int(1.2 * SR):int(2.8 * SR)]) == 0 and M.rms(out[3 * SR:4 * SR]) > 0.1


def test_mix_keeps_both_tracks_and_never_clips():
    a, b = tone(1.0, 0.9), tone(2.0, 0.9, 500)
    m = M.mix(a, b)
    assert len(m) == 2 * SR and np.abs(m).max() <= 1.0 and M.rms(m[:SR]) > M.rms(m[SR:])


def test_to_mono_16k_downmixes_and_resamples():
    st = np.stack([tone(1.0), tone(1.0)], axis=1).reshape(-1)       # 16 kHz stereo
    assert len(M.to_mono_16k(st, 2, 16000)) == SR
    x = np.zeros(48000, dtype=np.float32); assert abs(len(M.to_mono_16k(x, 1, 48000)) - SR) <= 1


def test_split_points_cut_in_silence_and_cover_everything():
    audio = np.concatenate([tone(9.0), np.zeros(SR // 2, dtype=np.float32), tone(9.0), tone(5.0)])
    parts = M.split_points(audio, chunk_s=10.0, search_s=3.0)
    assert parts[0][0] == 0 and parts[-1][1] == len(audio) and all(a[1] == b[0] for a, b in zip(parts, parts[1:]))
    cut = parts[0][1]; assert 9.0 * SR <= cut <= 9.6 * SR           # moved into the silent gap, not mid-word at 10 s


def test_merge_is_chronological_with_sources():
    me = [(0.0, 2.0, 'привет'), (10.0, 11.0, 'согласен')]
    ot = [(2.5, 6.0, 'давайте начнём'), (6.5, 9.0, 'первый пункт'), (10.0, 10.5, 'да'), (12.0, 12.5, '  ')]
    segs = M.merge_segments(me, ot)
    assert [s['text'] for s in segs] == ['привет', 'давайте начнём', 'первый пункт', 'согласен', 'да']
    assert [s['who'] for s in segs] == [M.ME, M.OTHERS, M.OTHERS, M.ME, M.OTHERS]


def test_transcribe_track_offsets_and_skips_silence():
    audio = np.concatenate([tone(5.0), np.zeros(10 * SR, dtype=np.float32), tone(5.0)])
    calls = []; prog = []
    def fake(chunk): calls.append(len(chunk)); return [(0.5, 1.0, 'слово')]
    out = M.transcribe_track(fake, audio, progress=prog.append, chunk_s=5.0)
    assert out and out[0][0] == pytest.approx(0.5) and len(calls) < len(M.split_points(audio, 5.0))   # silent chunks were skipped
    assert prog[-1] == len(audio) and out == sorted(out)


def test_outputs_and_files(tmp_path):
    mic, oth = tone(2.0), tone(3.0, 0.2, 700)
    names = M.save_tracks(tmp_path / 'm', mic, oth)
    assert names == ['mic.wav', 'others.wav', 'mixed.flac'] and all((tmp_path / 'm' / n).stat().st_size > 1000 for n in names)
    segs = M.merge_segments([(0, 1, 'Привет')], [(1.5, 3, 'Здравствуйте')])
    md, txt = M.render_md(segs, '2026-10-27 10:00', 3.0, names), M.render_txt(segs)
    assert '**[00:00:00] Я:** Привет' in md and '**[00:00:01] Собеседники:** Здравствуйте' in md and '2026-10-27 10:00' in md
    assert txt.splitlines() == ['[00:00:00] Я: Привет', '[00:00:01] Собеседники: Здравствуйте']
    assert M.fmt_ts(3725) == '01:02:05'


def _w(**kw):
    import widget, queue
    ns = types.SimpleNamespace(meeting=True, muted=False, held=[], msgs=queue.Queue(), last_ask=None, working=False, cfg={'speak_mode': 'immediate'},
                               said=[], recording=False, cancel_rec=False, followup=False, ptt=False, meeting_busy=False, barge=lambda: None)
    ns.__dict__.update(kw); return widget, ns


def test_nothing_is_dispatched_or_spoken_during_a_meeting():
    widget, w = _w()
    sent = []; w.bridge = types.SimpleNamespace(send_task=lambda *a, **k: sent.append(a) or True)
    w.send = lambda t: sent.append(t)
    widget.Widget.answer_or_send(w, 'сделай что-нибудь'); assert sent == []
    assert widget.Widget.handle(w, None, False) is False
    widget.Widget.say_event(w, 'Сессия закончила', False, {}); assert w.msgs.empty() and len(w.held) == 1       # queued, not spoken
    widget.Widget.say_event(w, 'Расшифровка готова.', False, {'meeting': True}); assert w.msgs.qsize() == 1


def test_meeting_does_not_start_while_muted():
    widget, w = _w(meeting=False, muted=True)
    said = []; w.ui = types.SimpleNamespace(say=lambda *a, **k: said.append(a[0]), set_recording=lambda on: said.append('REC'))
    widget.Widget.start_meeting(w)
    assert w.meeting is False and said and 'Не слушать' in said[0] and 'REC' not in said
