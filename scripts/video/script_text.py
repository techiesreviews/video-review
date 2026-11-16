"""Reads a script's markdown as the words to say. The take cutter and the overview page share it, so both read
the same script the same way: links, images and [directions]."""
import re

# A [direction] may run over several lines, but never past a heading: a missing "]" must not swallow later scenes.
DIRECTION = re.compile(r'\[(?:[^\]\n]|\n(?![ \t]*#))*\]')
# A markdown link or image, [text](address "title"). A mark with a backslash in front of it is text everywhere.
LINK_START = re.compile(r'((?<!\\)!)?\[')
# The square brackets of a script, and a heading: no pair of brackets runs past one, as no direction does.
BRACKET = re.compile(r'\\[^\n]|[\[\]]|\n[ \t]*#')
# What counts in an address: its round brackets and the end of its line; the brackets in its "title" are text.
ADDRESS_MARK = re.compile(r'\\[^\n]|(?<=[ \t])(?:"(?:[^"\n\\]|\\.)*"|\'(?:[^\'\n\\]|\\.)*\')|[()\n]')
ANGLE_ADDRESS = re.compile(r'<[^<>\n]*>')
# The mark that opens a list item.
LIST_ITEM = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+')


def bracket_pairs(text):
    """{just after a square bracket that opens: just after the one that closes it}, with the pairs inside a pair
    counted. A bracket that is not closed before the next heading has no entry."""
    pairs, opened = {}, []
    for mark in BRACKET.finditer(text):
        if mark.group() == '[':
            opened.append(mark.end())
        elif mark.group() == ']' and opened:
            pairs[opened.pop()] = mark.end()
        elif mark.group().startswith('\n'):
            opened.clear()
    return pairs


def address_end(text, start):
    """The place just after the round bracket that closes the link address opened just before `start`: on the same
    line, with the pairs of brackets inside the address counted; an address in <angle brackets> is text. None when
    the address is never closed."""
    angle = ANGLE_ADDRESS.match(text, start)
    depth = 1
    for mark in ADDRESS_MARK.finditer(text, angle.end() if angle else start):
        if mark.group() == '\n':
            break
        depth += {'(': 1, ')': -1}.get(mark.group(), 0)
        if not depth:
            return mark.end()
    return None


def without_links(text, images):
    """The script with its images left out (images=True), or with each link read as its text (images=False). A
    link that has a line to itself (also as a list item) is left out: that is a direction with an address. A
    bracket that is never closed, in the text or in the address, makes no link."""
    pairs = bracket_pairs(text)
    parts, done = [], 0
    for match in LINK_START.finditer(text):
        text_end = pairs.get(match.end(), len(text))
        if match.start() < done or bool(match.group(1)) != images or not text.startswith('(', text_end):
            continue
        end = address_end(text, text_end + 1)
        if end is None:
            continue
        line_end = text.find('\n', end)
        before = text[text.rfind('\n', 0, match.start()) + 1:match.start()]
        after = text[end:line_end if line_end >= 0 else len(text)]
        alone = not LIST_ITEM.sub('', before).strip() and not after.strip()
        parts += [text[done:match.start()], ' ' if images else '' if alone else text[match.end():text_end - 1]]
        done = end
    return ''.join(parts) + text[done:]


def without_directions(text):
    """The script without what is not said: images and [directions] are left out, and a link is read as its text,
    or left out when it has a line to itself. A direction never runs past a heading."""
    # Images go first: one that is itself a link, [![image](file)](address), then goes with its link.
    return DIRECTION.sub(' ', without_links(without_links(text, images=True), images=False))
