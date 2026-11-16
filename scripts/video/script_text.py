"""Reads a script's markdown as the words to say. The take cutter and the overview page share it, so both read
the same script the same way: links, images and [directions], with the brackets inside a direction and a bracket
that is never closed."""
import re

# A markdown link or image, [text](address "title"). A mark with a backslash in front of it is text everywhere.
LINK_START = re.compile(r'((?<!\\)!)?\[')
# The square brackets of a script, and a heading: no pair of brackets runs past one, as no direction does.
BRACKET = re.compile(r'\\[^\n]|[\[\]]|\n[ \t]*#')
# What counts in an address: its round brackets and the end of its line; the brackets in its "title" are text.
ADDRESS_MARK = re.compile(r'\\[^\n]|(?<=[ \t])(?:"(?:[^"\n\\]|\\.)*"|\'(?:[^\'\n\\]|\\.)*\')|[()\n]')
ANGLE_ADDRESS = re.compile(r'<[^<>\n]*>')
# The mark that opens a list item.
LIST_ITEM = re.compile(r'^\s*(?:[-*+]|\d+[.)])\s+')
# Bold or italic marks around a link, and the space beside them: a **[link](address)** alone is still alone.
EMPHASIS = '*_ \t'


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
    link that has a line to itself (also as a list item, and in bold or italic marks) is left out: that is a
    direction with an address. A bracket that is never closed, in the text or in the address, makes no link."""
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
        alone = not LIST_ITEM.sub('', before).strip(EMPHASIS) and not after.strip(EMPHASIS)
        parts += [text[done:match.start()], ' ' if images else '' if alone else text[match.end():text_end - 1]]
        done = end
    return ''.join(parts) + text[done:]


def without_directions(text):
    """The script without what is not said: images and [directions] are left out, and a link is read as its text,
    or left out when it has a line to itself. A direction is a closed pair of brackets, with the pairs inside it; it
    may run over several lines, but never past a heading. A bracket that is never closed hides no later line: a line
    that starts with one is left out on its own, one inside a line is text. A bracket with a backslash in front of
    it is text."""
    # Images go first: one that is itself a link, [![image](file)](address), then goes with its link.
    text = without_links(without_links(text, images=True), images=False)
    parts, done = [], 0
    for start, end in sorted(bracket_pairs(text).items()):
        if start > done:  # a pair inside a pair went with it
            parts += [text[done:start - 1], ' ']
            done = end
    lines = (''.join(parts) + text[done:]).split('\n')
    return '\n'.join('' if line.lstrip().startswith('[') else line for line in lines)
