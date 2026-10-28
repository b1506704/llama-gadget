import unittest
from dataclasses import replace

from llama_gadget.theme import (DARK_TOKENS, LIGHT_TOKENS, SURFACES, TEXT_MIN, TEXT_ROLES, UI_MIN, UI_ROLES,
                                audit_theme_contrast, contrast_pairings, contrast_ratio, relative_luminance,
                                stylesheet)


class ContrastMathTests(unittest.TestCase):
    def test_reference_luminance(self):
        self.assertAlmostEqual(relative_luminance('#000000'), 0.0)
        self.assertAlmostEqual(relative_luminance('#ffffff'), 1.0)
        self.assertAlmostEqual(relative_luminance('#fff'), 1.0)
        # #777777: 0x77/255 = .4667 -> ((.4667 + .055) / 1.055) ** 2.4 = .1845
        self.assertAlmostEqual(relative_luminance('#777777'), 0.1845, places=4)
        # Below the 0.04045 threshold the linear segment applies: 0x0a/255 / 12.92
        self.assertAlmostEqual(relative_luminance('#0a0a0a'), (10 / 255) / 12.92, places=6)
        with self.assertRaises(ValueError):
            relative_luminance('#12345')

    def test_contrast_ratio_bounds_and_symmetry(self):
        self.assertAlmostEqual(contrast_ratio('#000000', '#ffffff'), 21.0, delta=1e-4)
        self.assertAlmostEqual(contrast_ratio('#ffffff', '#000000'), 21.0, delta=1e-4)
        self.assertAlmostEqual(contrast_ratio('#3fb950', '#3fb950'), 1.0)
        self.assertAlmostEqual(contrast_ratio('#58a6ff', '#14161a'), contrast_ratio('#14161a', '#58a6ff'))

    def test_pairings_cover_every_role_and_surface(self):
        names = {name: required for name, _, _, required in contrast_pairings(DARK_TOKENS)}
        for fg in TEXT_ROLES:
            for bg in SURFACES:
                self.assertEqual(names[f'{fg}/{bg}'], TEXT_MIN)
        for fg in UI_ROLES:
            for bg in SURFACES:
                self.assertEqual(names[f'{fg}/{bg}'], UI_MIN)
        self.assertEqual(names['on_accent/accent'], TEXT_MIN)

    def test_audit_flags_sub_threshold_pairings(self):
        bad = replace(DARK_TOKENS, text_muted='#4a4f57', focus_ring='#30363d')
        violations = {v.pairing: v for v in audit_theme_contrast(bad)}
        self.assertIn('text_muted/background', violations)
        self.assertIn('focus_ring/surface_overlay', violations)
        v = violations['text_muted/background']
        self.assertEqual(v.required, TEXT_MIN)
        self.assertLess(v.ratio, TEXT_MIN)
        self.assertIn('text_muted/background', str(v))
        self.assertIn(':1', str(v))


class PaletteGateTests(unittest.TestCase):
    def assert_compliant(self, tokens):
        violations = audit_theme_contrast(tokens)
        self.assertEqual(violations, [], '\n' + '\n'.join(map(str, violations)))

    def test_dark_palette_meets_wcag_aa(self):
        self.assert_compliant(DARK_TOKENS)

    def test_light_palette_meets_wcag_aa(self):
        self.assert_compliant(LIGHT_TOKENS)

    def test_stylesheet_uses_tokens_only(self):
        for tokens in (DARK_TOKENS, LIGHT_TOKENS):
            css = stylesheet(tokens)
            self.assertIn(tokens.focus_ring, css)
            self.assertIn(tokens.text_muted, css)
            other = LIGHT_TOKENS if tokens is DARK_TOKENS else DARK_TOKENS
            self.assertNotIn(other.text_primary, css)


if __name__ == '__main__':
    unittest.main()
