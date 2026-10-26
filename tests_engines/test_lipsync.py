"""Mouth opening from the TTS audio amplitude."""
from engines import lipsync


def test_lipsync_rms_level():
    import numpy as np
    assert lipsync.rms_level(np.zeros(100)) == 0.0 and lipsync.rms_level([]) == 0.0
    quiet, loud = lipsync.rms_level(np.full(100, 0.03)), lipsync.rms_level(np.full(100, 0.5))
    assert 0 < quiet < loud == 1.0
    assert lipsync.rms_level(np.full(100, 0.005)) == 0.0
