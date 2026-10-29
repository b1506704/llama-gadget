"""Flag raw color and pixel literals in modules that must style through ThemeTokens.

Usage: python -m llama_gadget.theme.lint [paths...]
"""
from pathlib import Path
import re
import sys

PACKAGE = Path(__file__).resolve().parent.parent
DEFAULT_PATHS = (PACKAGE / 'layout', PACKAGE / 'theme' / 'adapter.py')
RULES = (
    ('hex color', re.compile(r'#[0-9a-fA-F]{3,8}\b')),
    ('rgb color', re.compile(r'\brgba?\s*\(')),
    ('pixel literal', re.compile(r'\b\d+(\.\d+)?px\b')),
)


def lint(paths=DEFAULT_PATHS):
    violations = []
    for root in map(Path, paths):
        files = sorted(root.rglob('*.py')) if root.is_dir() else [root]
        for path in files:
            for number, text in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                violations += [(path, number, name) for name, pattern in RULES if pattern.search(text)]
    return violations


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    violations = lint(argv or DEFAULT_PATHS)
    for path, number, name in violations:
        print(f'{path}:{number}: raw {name}; use ThemeTokens')
    print(f'{len(violations)} violation(s)')
    return 1 if violations else 0


if __name__ == '__main__':
    sys.exit(main())
