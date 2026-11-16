"""Lex's cut preferences, read from web/cut-preferences.json: the one place the cutter (cut-takes.py), the join check
(join-check.py), cut-to-review.py and the review page's preview get the room around speech, the pauses at a join,
the fade and their working values. Change a number there, never here or in a script."""
import json
from pathlib import Path

FILE = Path(__file__).resolve().parents[2] / 'web' / 'cut-preferences.json'
_SAVED = json.loads(FILE.read_text(encoding='utf-8'))
# Lex's choices, each saved with the reason and date beside it in the file.
LEX = {name: entry['value'] for name, entry in _SAVED['lex'].items()}
# The cutter's and the join check's working values, in the order their reports list them.
CUTTER = dict(_SAVED['cutter'])
JOIN_CHECK = dict(_SAVED['joinCheck'])
