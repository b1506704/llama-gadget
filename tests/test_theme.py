import dataclasses
import re
import unittest
from PyQt6.QtWidgets import QApplication, QPushButton
from PyQt6.QtGui import QColor, QFont, QPalette
from llama_gadget import theme
from llama_gadget.theme import (DARK_TOKENS, LIGHT_TOKENS, CornerRadii, SemanticColors,
                                SpacingScale, ThemeAdapter, ThemeTokens, TypeToken,
                                TypographyScale)
from llama_gadget.theme.adapter import luminance

APP = QApplication.instance() or QApplication([])
PALETTES = {'dark': DARK_TOKENS, 'light': LIGHT_TOKENS}
COLOR_ROLES = [f.name for f in dataclasses.fields(SemanticColors)]


class TokenSchemaTests(unittest.TestCase):
    def test_exports_and_fields(self):
        for name in ('ThemeTokens', 'SemanticColors', 'SpacingScale', 'TypographyScale',
                     'TypeToken', 'CornerRadii', 'DARK_TOKENS', 'LIGHT_TOKENS', 'ThemeAdapter'):
            self.assertIn(name, theme.__all__)
        self.assertEqual([f.name for f in dataclasses.fields(ThemeTokens)],
                         ['colors', 'spacing', 'typography', 'radii'])
        self.assertEqual(COLOR_ROLES, [
            'background', 'surface_primary', 'surface_elevated', 'surface_overlay',
            'text_primary', 'text_secondary', 'text_muted', 'accent', 'border', 'focus_ring',
            'status_success', 'status_warning', 'status_error', 'status_info'])
        self.assertEqual([f.name for f in dataclasses.fields(TypographyScale)],
                         ['heading', 'body', 'monospace', 'caption'])
        self.assertEqual([f.name for f in dataclasses.fields(CornerRadii)],
                         ['none', 'sm', 'md', 'lg', 'pill'])
        for f in ('family', 'size', 'line_height'):
            self.assertIn(f, [x.name for x in dataclasses.fields(TypeToken)])

    def test_spacing_scale_is_4px_8px(self):
        s = theme.DEFAULT_SPACING
        self.assertEqual(dataclasses.astuple(s), (4, 8, 12, 16, 24, 32))
        self.assertTrue(all(v % 4 == 0 for v in dataclasses.astuple(s)))
        with self.assertRaises(ValueError):
            SpacingScale(4, 8, 10, 16, 24, 32)

    def test_radii_order_and_typography(self):
        r = theme.DEFAULT_RADII
        self.assertTrue(r.none < r.sm < r.md < r.lg < r.pill)
        for role in ('heading', 'body', 'monospace', 'caption'):
            token = getattr(theme.DEFAULT_TYPOGRAPHY, role)
            self.assertIsInstance(token, TypeToken)
            self.assertTrue(token.family)
            self.assertGreater(token.line_height, token.size)
        t = theme.DEFAULT_TYPOGRAPHY
        self.assertGreater(t.heading.size, t.body.size)
        self.assertGreater(t.body.size, t.caption.size)
        self.assertIn('monospace', t.monospace.family)

    def test_frozen_value_types(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            DARK_TOKENS.colors.background = '#000000'
        copy = dataclasses.replace(DARK_TOKENS)
        self.assertEqual(copy, DARK_TOKENS)
        self.assertEqual(hash(copy), hash(DARK_TOKENS))
        self.assertIn('ThemeTokens(', repr(DARK_TOKENS))

    def test_colors_must_be_opaque_hex(self):
        good = dataclasses.asdict(DARK_TOKENS.colors)
        for bad in ('#fff', '#12345678', 'red', '#GGGGGG', None):
            with self.assertRaises(ValueError):
                SemanticColors(**{**good, 'accent': bad})


class PaletteTests(unittest.TestCase):
    def test_shared_scales(self):
        self.assertIs(DARK_TOKENS.spacing, LIGHT_TOKENS.spacing)
        self.assertIs(DARK_TOKENS.typography, LIGHT_TOKENS.typography)
        self.assertIs(DARK_TOKENS.radii, LIGHT_TOKENS.radii)
        self.assertIs(DARK_TOKENS.spacing, theme.DEFAULT_SPACING)

    def test_polarity(self):
        d, l = DARK_TOKENS.colors, LIGHT_TOKENS.colors
        self.assertLess(luminance(d.background), 0.1)
        self.assertGreater(luminance(d.text_primary), 0.6)
        self.assertGreater(luminance(l.background), 0.8)
        self.assertLess(luminance(l.text_primary), 0.1)

    def test_palettes_are_distinct_and_complete(self):
        for role in COLOR_ROLES:
            self.assertNotEqual(getattr(DARK_TOKENS.colors, role), getattr(LIGHT_TOKENS.colors, role), role)
        for tokens in PALETTES.values():
            c = tokens.colors
            statuses = {c.status_success, c.status_warning, c.status_error, c.status_info}
            self.assertEqual(len(statuses), 4)
            self.assertEqual(len({c.text_primary, c.text_secondary, c.text_muted}), 3)
            self.assertEqual(len({c.background, c.surface_primary, c.surface_overlay}), 3)

    def test_dark_matches_original_look(self):
        c = DARK_TOKENS.colors
        self.assertEqual((c.background, c.surface_primary, c.border, c.text_primary),
                         ('#14161a', '#1b1e24', '#2a2e35', '#e6e8eb'))
        self.assertEqual((c.status_success, c.status_warning, c.status_error),
                         ('#3fb950', '#d29922', '#f85149'))


class AdapterTests(unittest.TestCase):
    def test_palette_round_trip(self):
        R = QPalette.ColorRole
        for name, tokens in PALETTES.items():
            with self.subTest(name):
                c, pal = tokens.colors, ThemeAdapter(tokens).palette()
                for role, expected in ((R.Window, c.background), (R.WindowText, c.text_primary),
                                       (R.Base, c.surface_primary), (R.Text, c.text_primary),
                                       (R.Button, c.surface_elevated), (R.Accent, c.accent),
                                       (R.Highlight, c.surface_overlay), (R.Mid, c.border),
                                       (R.PlaceholderText, c.text_muted), (R.Link, c.status_info),
                                       (R.BrightText, c.status_error)):
                    self.assertEqual(pal.color(role).name(), expected, role)
                self.assertEqual(pal.color(QPalette.ColorGroup.Disabled, R.Text).name(), c.text_muted)

    def test_fonts(self):
        a = ThemeAdapter(DARK_TOKENS)
        heading, mono = a.font('heading'), a.font('monospace')
        self.assertEqual(heading.pixelSize(), DARK_TOKENS.typography.heading.size)
        self.assertEqual(heading.weight(), QFont.Weight.DemiBold)
        self.assertEqual(mono.styleHint(), QFont.StyleHint.Monospace)
        self.assertIn('monospace', mono.families())
        with self.assertRaises(ValueError):
            a.font('display')

    def test_widget_helpers_use_only_tokens(self):
        for name, tokens in PALETTES.items():
            with self.subTest(name):
                a, c, r = ThemeAdapter(tokens), tokens.colors, tokens.radii
                self.assertEqual(a.is_dark, name == 'dark')
                for tier, color in (('background', c.background), ('primary', c.surface_primary),
                                    ('elevated', c.surface_elevated), ('overlay', c.surface_overlay)):
                    css = a.surface_style(tier)
                    self.assertIn(f'background: {color};', css)
                    self.assertIn(f'1px solid {c.border}', css)
                    self.assertIn(f'border-radius: {r.md:g}px', css)
                self.assertTrue(a.surface_style('overlay', 'QFrame#card').startswith('QFrame#card {'))
                for role in ('primary', 'secondary', 'muted'):
                    self.assertIn(f'color: {getattr(c, "text_" + role)};', a.text_style(role, 'caption'))
                self.assertIn(f'font-size: {tokens.typography.caption.size}px', a.text_style('muted', 'caption'))
                for status in ('success', 'warning', 'error', 'info'):
                    color = getattr(c, 'status_' + status)
                    self.assertIn(f'color: {color};', a.status_style(status))
                    self.assertIn(f'background: {color};', a.status_badge_style(status))
                control = a.control_style()
                self.assertIn(f'QPushButton:focus {{ border: 1px solid {c.focus_ring}; }}', control)
                self.assertIn(f'color: {c.text_muted}', control)
                self.assertIn(f'background: {c.accent};', a.accent_control_style())
                with self.assertRaises(ValueError):
                    a.status_color('fatal')
                with self.assertRaises(ValueError):
                    a.surface('floating')

    def test_app_stylesheet_is_token_driven_and_parses(self):
        hexes = re.compile(r'#[0-9a-fA-F]{6}\b')
        for name, tokens in PALETTES.items():
            with self.subTest(name):
                a = ThemeAdapter(tokens)
                qss = a.stylesheet()
                derived = {a.hover(tokens.colors.surface_elevated), a.hover(tokens.colors.accent),
                           a.on_color(tokens.colors.accent)}
                allowed = set(dataclasses.asdict(tokens.colors).values()) | derived
                self.assertTrue(set(hexes.findall(qss)) <= allowed)
                for color in (tokens.colors.background, tokens.colors.focus_ring,
                              tokens.colors.surface_overlay, tokens.colors.border):
                    self.assertIn(color, qss)
                self.assertEqual(qss.count('{'), qss.count('}'))
                self.assertIn(f'QLineEdit:focus, QListWidget:focus {{ border: 1px solid {tokens.colors.focus_ring}; }}', qss)
                button = QPushButton('x')
                self.addCleanup(button.deleteLater)
                button.setStyleSheet(qss)
                button.ensurePolished()

    def test_hover_and_on_color_follow_polarity(self):
        dark, light = ThemeAdapter(DARK_TOKENS), ThemeAdapter(LIGHT_TOKENS)
        base = DARK_TOKENS.colors.surface_elevated
        self.assertGreater(QColor(dark.hover(base)).lightness(), QColor(base).lightness())
        base = LIGHT_TOKENS.colors.surface_elevated
        self.assertLess(QColor(light.hover(base)).lightness(), QColor(base).lightness())
        self.assertEqual(light.on_color(LIGHT_TOKENS.colors.accent), LIGHT_TOKENS.colors.background)
        self.assertEqual(dark.on_color(DARK_TOKENS.colors.accent), DARK_TOKENS.colors.text_primary)


if __name__ == '__main__':
    unittest.main()
