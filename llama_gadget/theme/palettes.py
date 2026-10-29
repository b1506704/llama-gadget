"""Built-in Dark and Light token catalogs sharing one spacing/typography/radii source."""
from .tokens import (DEFAULT_RADII, DEFAULT_SPACING, DEFAULT_TYPOGRAPHY, SemanticColors,
                     ThemeTokens)

# Reproduces the gadget's original dark look (app.STYLE, chat_ui, tool_ui).
DARK_TOKENS = ThemeTokens(
    colors=SemanticColors(
        background='#14161a', surface_primary='#1b1e24', surface_elevated='#2a2e35',
        surface_overlay='#2a3140', text_primary='#e6e8eb', text_secondary='#d0d4da',
        text_muted='#8b949e', accent='#238636', border='#2a2e35', focus_ring='#388bfd',
        status_success='#3fb950', status_warning='#d29922', status_error='#f85149',
        status_info='#58a6ff'),
    spacing=DEFAULT_SPACING, typography=DEFAULT_TYPOGRAPHY, radii=DEFAULT_RADII)

LIGHT_TOKENS = ThemeTokens(
    colors=SemanticColors(
        background='#f6f8fa', surface_primary='#ffffff', surface_elevated='#eaeef2',
        surface_overlay='#dde4ee', text_primary='#1f2328', text_secondary='#424a53',
        text_muted='#656d76', accent='#1f883d', border='#d0d7de', focus_ring='#0969da',
        status_success='#1a7f37', status_warning='#9a6700', status_error='#cf222e',
        status_info='#0550ae'),
    spacing=DEFAULT_SPACING, typography=DEFAULT_TYPOGRAPHY, radii=DEFAULT_RADII)
