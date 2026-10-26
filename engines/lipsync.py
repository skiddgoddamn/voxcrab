"""Mouth opening from the TTS audio that is actually being played (RMS envelope), instead of a made-up oscillation."""
import numpy as np


def rms_level(chunk, floor=0.01, ceil=0.22):
    """float audio chunk (-1..1) -> mouth opening 0..1. Below `floor` the mouth is closed; the curve is compressed so quiet speech still moves it."""
    a = np.asarray(chunk, dtype=np.float32).ravel()
    if a.size == 0: return 0.0
    rms = float(np.sqrt(np.mean(a * a)))
    x = (rms - floor) / (ceil - floor)
    return float(min(1.0, max(0.0, x)) ** 0.7)
