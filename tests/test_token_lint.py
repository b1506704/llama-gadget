"""Token-adoption gate: GUI modules may not contain raw colors, font sizes, or spacing literals.

Run alone with: QT_QPA_PLATFORM=offscreen python -m unittest tests.test_token_lint -v
(or `python -m llama_gadget.theme.lint`).
"""
import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from llama_gadget.theme import lint

PACKAGE = Path(lint.__file__).resolve().parent.parent

VIOLATIONS = {
    'hex6': 'lbl.setStyleSheet("color:#8b949e;")',
    'hex3': "css = 'background: #fff'",
    'hex8': "css = 'background: #ff00ff80'",
    'rgb': "css = 'color: rgb(10, 20, 30)'",
    'rgba': "css = 'background: rgba(0,0,0,0.5)'",
    'qcolor_name': 'flat.fill(QColor("white"))',
    'qcolor_ints': 'brush = QColor(255, 0, 0)',
    'qcolor_global': 'pen = QColor(Qt.GlobalColor.red)',
    'global_color': 'pm.fill(Qt.GlobalColor.black)',
    'named_qss': "css = 'QLabel { color: white; }'",
    'color_constant': 'COLORS = {"online": token}',
    'color_constant_suffix': 'STATUS_COLOR: str = value',
    'font_size_px': "css = 'font-size:11px;'",
    'font_size_pt': "css = 'font-size: 9pt'",
    'font_shorthand': "css = 'font: bold 12px sans'",
    'font_call': 'font.setPointSize(11)',
    'padding': "css = 'padding: 2px 8px'",
    'padding_scale_value': "css = 'padding: 8px'",
    'margin_side': "css = 'margin-top: 6px'",
    'radius': "css = 'border-radius: 4px'",
    'radius_fstring': "css = f'border-radius: 3px; color: {c.accent}'",
    'margins_call': 'lay.setContentsMargins(0, 4, 0, 0)',
    'spacing_call': 'lay.setSpacing(6)',
    'add_spacing': 'row.addSpacing(10)',
}

ALLOWED = (
    "lbl.setStyleSheet(f'color: {c.text_muted}; font-size: {t.size}px;')",
    "css = f'padding: {s.xxs}px {s.sm}px; border-radius: {r.md:g}px'",
    "css = f'padding: {s.xxs // 2}px {s.xs}px'",
    "css = f'border: 1px solid {c.border}'",
    'lay.setContentsMargins(0, 0, 0, 0)',
    'lay.setContentsMargins(0, gap, 0, gap)',
    'lay.setContentsMargins(s.xxs, s.xxs, s.xxs, s.xxs)',
    'p.setBrush(QColor(color))',
    'pm.fill(Qt.GlobalColor.transparent)',
    "label = QLabel('<b>Theme</b>')",
    "text = '### Built-in Tool: `name`'",
    "html = '&#1234; entity'",
    'x = 1  # was #8b949e with font-size:11px and padding: 2px',
    'self.status_colorize = make()',
    'tone = STATUS_TONES[state]',
    "css = 'QLabel { color: palette(text); }'",
)


class ScannerRuleTests(unittest.TestCase):
    def test_each_forbidden_literal_is_flagged_with_location(self):
        for name, line in VIOLATIONS.items():
            with self.subTest(name):
                source = 'import x\n\n' + line + '\n'
                found = lint.scan_source(source, 'llama_gadget/demo.py')
                self.assertTrue(found, f'{name} not flagged: {line}')
                for diag in found:
                    self.assertRegex(diag, r'^llama_gadget/demo\.py:3: forbidden raw style literal \(.+\) '
                                           r'— use theme\.ThemeTokens instead$')

    def test_token_backed_code_is_allowed(self):
        for line in ALLOWED:
            with self.subTest(line):
                self.assertEqual(lint.scan_source(line + '\n'), [])

    def test_diagnostic_format_and_snippet(self):
        found = lint.scan_source('a = 1\nlbl.setStyleSheet("color:#8b949e; font-size:11px;")\n', 'gui.py')
        self.assertEqual(found, [
            'gui.py:2: forbidden raw style literal (#8b949e) — use theme.ThemeTokens instead',
            'gui.py:2: forbidden raw style literal (font-size:11px) — use theme.ThemeTokens instead',
        ])

    def test_multiline_strings_report_the_offending_line(self):
        source = 'css = (\n    "background: #238636; "\n    "padding: 2px 8px;")\n'
        found = lint.scan_source(source, 'w.py')
        self.assertEqual([d.split(':')[1] for d in found], ['2', '3'])

    def test_untokenizable_source_is_still_scanned(self):
        self.assertTrue(lint.scan_source('def broken(:\n    c = "#ffffff"\n'))

    def test_cli_exit_codes(self):
        with TemporaryDirectory() as temp:
            bad, good = Path(temp) / 'bad.py', Path(temp) / 'good.py'
            bad.write_text('c = "#ffffff"\n')
            good.write_text('c = tokens.colors.accent\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(lint.main([str(bad)]), 1)
                self.assertEqual(lint.main([str(good)]), 0)
            self.assertIn(f'{bad}:1: forbidden raw style literal (#ffffff)', out.getvalue())


class GuiModulesTests(unittest.TestCase):
    def test_scope_covers_gui_modules_and_excludes_theme(self):
        names = {p.relative_to(PACKAGE).as_posix() for p in lint.gui_modules()}
        self.assertTrue({'app.py', 'chat_ui.py', 'tool_ui.py'} <= names)
        self.assertFalse(any(n.startswith('theme/') for n in names))
        self.assertNotIn('images.py', names)
        self.assertEqual(set(lint.EXEMPT), {'images.py'})

    def test_gui_modules_have_zero_violations(self):
        found = lint.scan_paths(lint.gui_modules())
        self.assertEqual(found, [], '\n' + '\n'.join(found))


if __name__ == '__main__':
    unittest.main()
