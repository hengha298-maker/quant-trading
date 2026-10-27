"""Platform-independent structural JSON limit before decoding untrusted input."""
MAX_DEPTH = 64


def check_nesting(text):
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == '\\':
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in '[{':
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError('JSON_NESTING_LIMIT_EXCEEDED')
        elif character in ']}':
            depth -= 1
            if depth < 0:
                raise ValueError('JSON_STRUCTURE_INVALID')
    # The strict JSON decoder handles mismatched delimiters, unterminated
    # strings and trailing content. Brackets inside strings do not count.
