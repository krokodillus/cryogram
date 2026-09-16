# Tests: the theme token blocks in frontend/css/styles.css are the ONE place colour may live: this test parses both blocks (light :root and [data-theme="dark"]) and enforces two invariants that make black-on-black / white-on-white impossible to ship in either theme:
from __future__ import annotations

import re
import unittest

from tests import _bootstrap

import config

CSS = (config.FRONTEND_DIR / "css" / "styles.css").read_text()

TOKEN_BLOCK_RE = re.compile(
    r'(?::root|\[data-theme="dark"\])\s*\{(.*?)\}', re.S)
DECL_RE = re.compile(r'(--[\w-]+)\s*:\s*([^;]+);')
HEX_RE = re.compile(r'#[0-9A-Fa-f]{3,8}\b')

def _blocks():
    found = TOKEN_BLOCK_RE.findall(CSS)
    assert len(found) >= 2, "expected a :root and a [data-theme=dark] token block"
    light = dict(DECL_RE.findall(found[0]))
    dark = dict(DECL_RE.findall(found[1]))
    return light, dark

def _hex_to_rgb(value):
    value = value.strip()
    m = HEX_RE.search(value)
    if not m:
        return None
    h = m.group(0).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def _lum(rgb):
    def chan(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

PAIRS = [

    ("--ink", "--bg", 4.5), ("--ink", "--surface", 4.5), ("--ink", "--panel", 4.5),
    ("--ink", "--hover", 4.5),
    ("--muted", "--bg", 4.5), ("--muted", "--surface", 4.5), ("--muted", "--panel", 4.5),
    ("--faint", "--panel", 3.0),

    ("--accent", "--panel", 3.0), ("--accent", "--bg", 3.0),
    ("--accent-ink", "--accent", 4.5),
    ("--primary-ink", "--primary", 4.5),
    ("--primary-ink", "--primary-hover", 4.5),
    ("--bad-solid-ink", "--bad-solid", 3.0),
    ("--bad-solid-ink", "--bad-solid-hover", 3.0),

    ("--ok-ink", "--ok-bg", 3.0), ("--ok-ink", "--ok-surface", 3.0),
    ("--ok-ink", "--surface", 3.0),
    ("--warn-ink", "--warn-bg", 3.0), ("--warn-ink", "--warn-surface", 3.0),
    ("--bad-ink", "--bad-bg", 3.0), ("--bad-ink", "--bad-surface", 3.0),
    ("--info-ink", "--info-bg", 3.0),
    ("--ai-ink", "--ai-bg", 3.0),

    ("--badge-ink", "--ok", 3.0), ("--badge-ink", "--warn", 3.0),
    ("--badge-ink", "--bad", 3.0), ("--badge-ink", "--info", 3.0),
    ("--badge-ink", "--ai", 3.0), ("--badge-ink", "--neutral", 3.0),

    ("--tag-ink", "--tag-bg", 4.5), ("--tag-muted", "--tag-bg", 3.0),
    ("--bubble-user-ink", "--bubble-user-bg", 4.5),

    ("--code", "--panel", 3.0), ("--ai", "--panel", 3.0),
    ("--input", "--panel", 3.0), ("--conn", "--panel", 3.0),

    ("--accent", "--accent-bg", 3.0),

    ("--line", "--bg", 1.15), ("--edge", "--bg", 1.3),
    ("--line", "--surface", 1.15),
]

class ThemeContrastTest(unittest.TestCase):
    def test_no_raw_colours_outside_token_blocks(self):
        stripped = TOKEN_BLOCK_RE.sub("", CSS)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
        offenders = [m.group(0) for m in HEX_RE.finditer(stripped)]
        offenders += re.findall(r"rgba?\([^)]*\)", stripped)
        self.assertEqual(offenders, [],
                         f"raw colours outside the token blocks: {offenders}")

    def test_both_themes_declare_the_same_tokens(self):
        light, dark = _blocks()
        colour_tokens = {k for k, v in light.items() if _hex_to_rgb(v)}
        dark_colour_tokens = {k for k, v in dark.items() if _hex_to_rgb(v)}
        missing = colour_tokens - dark_colour_tokens - {"--radius-badge", "--mono", "--font-sans"}
        self.assertEqual(missing, set(),
                         f"colour tokens missing a dark value: {sorted(missing)}")

    def test_contrast_pairs_hold_in_both_themes(self):
        light, dark = _blocks()

        merged_dark = dict(light)
        merged_dark.update(dark)
        failures = []
        for name, tokens in (("light", light), ("dark", merged_dark)):
            for fg, bg, minimum in PAIRS:
                self.assertIn(fg, tokens, f"{name}: {fg} not declared")
                self.assertIn(bg, tokens, f"{name}: {bg} not declared")
                frgb, brgb = _hex_to_rgb(tokens[fg]), _hex_to_rgb(tokens[bg])
                self.assertIsNotNone(frgb, f"{name}: {fg} is not a hex colour")
                self.assertIsNotNone(brgb, f"{name}: {bg} is not a hex colour")
                ratio = contrast(frgb, brgb)
                if ratio < minimum:
                    failures.append(
                        f"{name}: {fg} on {bg} = {ratio:.2f}, needs {minimum}")
        self.assertEqual(failures, [], "contrast failures:\n" + "\n".join(failures))

    def test_pair_tokens_exist_for_every_tint_family(self):
        light, _ = _blocks()
        for fam in ("ok", "warn", "bad", "info"):
            for part in ("", "-bg", "-ink", "-line"):
                self.assertIn(f"--{fam}{part}", light,
                              f"family {fam} is missing --{fam}{part}")

if __name__ == "__main__":
    unittest.main()
