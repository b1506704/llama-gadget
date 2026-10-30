"""Token-adoption lint: GUI modules must take colors, font sizes, and spacing from ThemeTokens.

Run `python -m llama_gadget.theme.lint [paths...]`; exits 1 and prints
`path:line: forbidden raw style literal (<snippet>) — use theme.ThemeTokens instead`
for every violation. With no paths it scans every `llama_gadget` module outside `theme/`
(the only package allowed to hold raw style values) except the documented exemptions.
The unittest suite (tests/test_token_lint.py) runs the same scan as the enforcement gate.
"""
import io
from pathlib import Path
import re
import sys
import tokenize

PACKAGE = Path(__file__).resolve().parent.parent
THEME_DIR = Path(__file__).resolve().parent
DEFAULT_PATHS = (PACKAGE / 'layout', PACKAGE / 'theme' / 'adapter.py')

# Package-relative modules that legitimately use raw colors for non-GUI work, with the reason on record.
EXEMPT = {
    'images.py': 'flattens image alpha onto white before JPEG-encoding model input; '
                 'image processing, not GUI styling, and must not follow the theme',
}
MESSAGE = 'forbidden raw style literal ({snippet}) — use theme.ThemeTokens instead'

_NAMED = ('white|black|red|green|blue|yellow|orange|purple|pink|brown|gray|grey|silver|'
          'navy|teal|maroon|olive|lime|aqua|cyan|magenta|fuchsia|gold|crimson|darkgray|'
          'darkgrey|lightgray|lightgrey')
_SPACING_PROPS = r'(?:padding|margin|spacing|gap|border(?:-(?:top|bottom)-(?:left|right))?-radius)'
RULES = (
    ('hex color', re.compile(r'(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b')),
    ('rgb()/hsl() color', re.compile(r'\b(?:rgba?|hsla?)\s*\(')),
    ('QColor literal', re.compile(r'\bQColor\s*\(\s*(?:[\'"\d]|Qt\.)[^)]*\)?')),
    ('Qt named color', re.compile(r'\b(?:Qt\.GlobalColor\.(?!transparent\b)\w+|QColorConstants\.\w+)')),
    ('named QSS color', re.compile(
        r'\b(?:color|background(?:-color)?|border(?:-\w+)?|selection-[\w-]+)\s*:\s*[^;{}\'"]*?'
        rf'\b(?:{_NAMED})\b')),
    ('local color constant', re.compile(r'^\s*[A-Z0-9_]*COLOU?RS?[A-Z0-9_]*\s*(?::[^=]*)?=(?!=)')),
    ('font-size literal', re.compile(r'font(?:-size)?\s*:\s*[^;{}\'"]*?\b\d+(?:\.\d+)?\s*(?:px|pt)\b')),
    ('font-size call', re.compile(r'\.set(?:Point|Pixel)Size(?:F)?\s*\(\s*\d')),
    ('spacing/radius literal', re.compile(
        rf'\b{_SPACING_PROPS}(?:-(?:top|right|bottom|left))?\s*:\s*[^;{{}}\'"]*?'
        r'(?<![\w.{])(?:[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]\d*)px')),
    ('layout spacing literal', re.compile(
        r'\.(?:setContentsMargins|setSpacing|addSpacing|setHorizontalSpacing|setVerticalSpacing)'
        r'\s*\([^)]*?(?<![\w.])[1-9]\d*(?![\w.])[^)]*\)')),
)


def _strip_comments(source):
    """Source lines with Python comments blanked; falls back to raw lines if untokenizable."""
    lines = source.splitlines()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return lines


def _snippet(text, limit=60):
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def scan_source(source, path='<source>'):
    """Diagnostics (`path:line: message`) for every forbidden literal in `source`."""
    found = []
    for number, line in enumerate(_strip_comments(source), 1):
        for _name, pattern in RULES:
            for match in pattern.finditer(line):
                found.append(f'{path}:{number}: ' + MESSAGE.format(snippet=_snippet(match.group())))
    return found


def gui_modules(package=PACKAGE):
    """Every package module outside theme/ that is not explicitly exempt."""
    return sorted(p for p in package.rglob('*.py')
                  if THEME_DIR not in p.resolve().parents and '__pycache__' not in p.parts
                  and p.relative_to(package).as_posix() not in EXEMPT)


def scan_paths(paths):
    found = []
    for path in paths:
        path = Path(path)
        files = sorted(path.rglob('*.py')) if path.is_dir() else [path]
        for f in files:
            found.extend(scan_source(f.read_text(encoding='utf-8'), f))
    return found


def lint(paths=DEFAULT_PATHS):
    return scan_paths(paths)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    paths = argv or gui_modules()
    found = scan_paths(paths)
    for line in found:
        print(line)
    if found:
        print(f'{len(found)} token-adoption violation(s)', file=sys.stderr)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
