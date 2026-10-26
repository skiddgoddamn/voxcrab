"""Opaque-pixel mask reported by the renderer page (a coarse grid of 0/1 cells) and the hit test against a screen-space cursor position."""


class Mask:
    def __init__(self, w, h, bits):
        ok = isinstance(w, int) and isinstance(h, int) and 0 < w <= 512 and 0 < h <= 512 and isinstance(bits, str) and len(bits) == w * h
        self.w, self.h, self.bits = (w, h, bits) if ok else (0, 0, '')

    def hit(self, x, y, win_w, win_h, grow=1):
        """(x, y) relative to the window's top-left, in window pixels. True when the cell or a neighbour (grow cells) is opaque."""
        if not self.w or win_w <= 0 or win_h <= 0 or not (0 <= x < win_w and 0 <= y < win_h): return False
        cx, cy = int(x * self.w / win_w), int(y * self.h / win_h)
        for j in range(max(0, cy - grow), min(self.h, cy + grow + 1)):
            row = j * self.w
            if '1' in self.bits[row + max(0, cx - grow):row + min(self.w, cx + grow + 1)]: return True
        return False


def clean_box(box):
    """[left, top, right, bottom] as fractions of the window -> tuple, or None when it is not a sane non-empty box."""
    try:
        l, t, r, b = [float(v) for v in box]
    except (TypeError, ValueError):
        return None
    if not all(0.0 <= v <= 1.0 for v in (l, t, r, b)) or r - l < 0.03 or b - t < 0.03: return None
    return (l, t, r, b)


def head_anchor(box, x, y, w, h, fallback_top=0.06):
    """Screen point just above the visible model: (centre x, head-top y). Without a usable box: window centre and fallback_top of the height."""
    b = clean_box(box) if box is not None else None
    if b is None: return (x + w // 2, y + int(h * fallback_top))
    return (x + int((b[0] + b[2]) / 2 * w), y + int(b[1] * h))
