"""Published script imports for Windows Python, which ignores PYTHONPATH."""
HANDS_ENTRY = '..\\tree\\pyhands'


def with_hands(text):
    if HANDS_ENTRY not in text.splitlines():
        text = text.rstrip('\r\n') + '\n' + HANDS_ENTRY + '\n'
    return text
