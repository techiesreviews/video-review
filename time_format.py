"""How the tool writes a time, for the Python scripts and the local app.

web/time-format.js writes the same for the browser and the hosted Worker, and tests/fixtures/time-format-cases.json is
run against both, so a time reads the same in captions, paper edits, the overview page and the review page.
"""
import math


def round_half_up(value):
    """Nearest whole number, halves up, as JavaScript's Math.round does (Python's round() sends halves to even)."""
    whole = math.floor(value)
    return whole + 1 if value - whole >= .5 else whole


def timecode(ms, separator='.'):
    """'01:02:03.456' for exports and agents; separator ',' gives an SRT timestamp, '.' a VTT one."""
    ms = max(0, round_half_up(ms))
    return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02}{separator}{ms % 1000:03}'


def clock(ms, places=0, nearest=False, hours=True, pad_minutes=False):
    """'1:02' or '1:02:03' for people to read.

    places: 0, or 3 to add '.456'. nearest: round to the nearest second, halves to even, instead of cutting off the
    part of a second. hours: False keeps counting minutes past 59 ('75:23'). pad_minutes: two-digit minutes without
    hours.
    """
    if places not in (0, 3):
        raise ValueError('A clock shows 0 or 3 digits of milliseconds.')
    if nearest and places:
        raise ValueError('A clock rounded to the nearest second has no milliseconds to show.')
    ms = max(0, round(ms / 1000)) * 1000 if nearest else max(0, round_half_up(ms))
    h = ms // 3600000 if hours else 0
    m = ms // 60000 - h * 60
    head = f'{h}:{m:02}' if h else f'{m:02}' if pad_minutes else f'{m}'
    return f'{head}:{ms // 1000 % 60:02}' + (f'.{ms % 1000:03}' if places else '')


def frame(ms, fps):
    """The frame shown at a time, halves up, as the browser and the hosted Worker count it."""
    return round_half_up(ms * fps / 1000)
