"""Window-mode helpers (Windows, documented Win32 only). No hooks, no injection, nothing that touches other processes:
  - SHQueryUserNotificationState: the shell tells us whether a D3D exclusive fullscreen app / presentation is running
  - SetWindowPos(HWND_TOPMOST): re-assert our own window on top (borderless-windowed games can push other topmost windows down)
  - SystemParametersInfo(SPI_GETWORKAREA): desktop-pet placement above the taskbar
An exclusive-fullscreen game owns the display: no ordinary window (ours included) can be drawn over it, so we hide instead of pretending.
"""
import sys

QUNS = {1: 'not_present', 2: 'busy', 3: 'exclusive', 4: 'presentation', 5: 'ok', 6: 'quiet', 7: 'app'}
HWND_TOPMOST, SWP = -1, 0x0001 | 0x0002 | 0x0010 | 0x0040      # NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW


def notification_state():
    """-> 'exclusive' | 'presentation' | 'busy' | 'ok' | ... | None when unavailable."""
    if sys.platform != 'win32': return None
    try:
        import ctypes
        v = ctypes.c_int()
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(v)) != 0: return None
        return QUNS.get(v.value)
    except Exception:
        return None


def should_hide(state, policy):
    """policy: 'exclusive' (hide only for D3D exclusive fullscreen / presentation), 'any' (also any fullscreen app = 'busy'), 'never'."""
    if policy == 'never' or state is None: return False
    if policy == 'any': return state in ('exclusive', 'presentation', 'busy')
    return state in ('exclusive', 'presentation')


def keep_on_top(hwnd):
    if sys.platform != 'win32' or not hwnd: return False
    try:
        import ctypes
        return bool(ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP))
    except Exception:
        return False


def work_area():
    """(left, top, right, bottom) of the primary monitor without the taskbar, or None."""
    if sys.platform != 'win32': return None
    try:
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0): return None
        return r.left, r.top, r.right, r.bottom
    except Exception:
        return None


def pet_position(area, w, h, x):
    """Desktop pet sits on the taskbar edge: bottom of the work area, x clamped inside it."""
    l, t, r, b = area
    return min(max(int(x), l), max(l, r - w)), b - h


def patrol_step(x, target, dt, speed=40.0):
    """One step of the idle wander toward target x (px/s). -> new x"""
    d = target - x
    step = speed * dt
    return target if abs(d) <= step else x + (step if d > 0 else -step)
