# Tests: tHE FRONTEND MODULE GRAPH holds together
from __future__ import annotations

import re
import unittest

from tests import _bootstrap

import config

JS_DIR = config.FRONTEND_DIR / "js"

GLOBALS = set("""
if for while switch catch return typeof new delete void await yield function do
else try throw case in of instanceof async import super this
Object Array String Number Boolean Symbol BigInt Math JSON Date Promise Proxy
Function
Reflect Set Map WeakSet WeakMap RegExp Error TypeError RangeError Intl
parseInt parseFloat isNaN isFinite encodeURIComponent decodeURIComponent
encodeURI decodeURI structuredClone queueMicrotask
fetch Headers Request Response AbortController FormData Blob File FileReader URL
URLSearchParams TextEncoder TextDecoder EventSource WebSocket
Uint8Array Int8Array Uint16Array Uint32Array Float32Array Float64Array
ArrayBuffer DataView btoa atob
setTimeout clearTimeout setInterval clearInterval
requestAnimationFrame cancelAnimationFrame
document window navigator location history console alert confirm prompt
Notification Image Audio Event CustomEvent MutationObserver ResizeObserver
IntersectionObserver DOMParser XMLSerializer crypto performance getComputedStyle
matchMedia scrollTo localStorage sessionStorage
""".split())

_REGEX_OK_AFTER = set("(,=:[!&|?{};+-*%~^<>") | {"return", "typeof", "case", "in", "of"}

def _regex_position(out: list[str]) -> bool:
    tail = "".join(out[-12:]).rstrip()
    if not tail:
        return True
    if tail[-1] in _REGEX_OK_AFTER:
        return True
    word = re.search(r'([A-Za-z]+)$', tail)
    return bool(word and word.group(1) in _REGEX_OK_AFTER)

def strip_noise(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] not in "/*" and _regex_position(out):
            i += 1
            in_class = False
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                elif src[i] == "/" and not in_class:
                    break
                elif src[i] == "\n":
                    break
                i += 1
            i += 1
            while i < n and src[i] in "gimsuyd":
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
        elif c in "\"'":
            quote, i = c, i + 1
            while i < n and src[i] != quote:
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif c == "`":
            i += 1
            while i < n and src[i] != "`":
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    depth, i = 1, i + 2
                    start = i
                    while i < n and depth:
                        if src[i] == "{":
                            depth += 1
                        elif src[i] == "}":
                            depth -= 1
                        if depth:
                            i += 1
                    out.append(" " + strip_noise(src[start:i]) + " ")
                    i += 1
                    continue
                i += 1
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)

def exported_names(code: str) -> set[str]:
    names = set()
    names |= set(re.findall(r'\bexport\s+(?:async\s+)?(?:function|class)\s+([\w$]+)', code))
    names |= set(re.findall(r'\bexport\s+(?:const|let|var)\s+([\w$]+)', code))
    for block in re.findall(r'\bexport\s*\{([^}]*)\}', code):
        for part in block.split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    return names

def bound_names(code: str) -> set[str]:
    names = set()
    names |= set(re.findall(r'\b(?:async\s+)?function\s*\*?\s*([\w$]+)', code))
    names |= set(re.findall(r'\b(?:const|let|var)\s+([\w$]+)', code))
    names |= set(re.findall(r'\bclass\s+([\w$]+)', code))
    for block in re.findall(r'import\s*\{([^}]+)\}\s*from', code, re.S):
        for part in block.split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    names |= set(re.findall(r'import\s+\*\s+as\s+([\w$]+)', code))
    names |= set(re.findall(r'import\s+([\w$]+)\s*(?:,|from)', code))

    for block in re.findall(r'\{([^{}]*)\}\s*(?:=|\)|=>)', code):
        for part in block.split(","):
            part = part.split("=")[0].split(":")[-1].strip()
            if re.fullmatch(r'[\w$]+', part):
                names.add(part)

    for block in re.findall(r'\(([^()]*)\)\s*(?:=>|\{)', code):
        for part in block.split(","):
            part = part.split("=")[0].strip().lstrip("...").strip()
            if re.fullmatch(r'[\w$]+', part):
                names.add(part)
    names |= set(re.findall(r'\bcatch\s*\(\s*([\w$]+)', code))
    names |= set(re.findall(r'([\w$]+)\s*=>', code))
    return names

class FrontendModuleGraphTest(unittest.TestCase):
    def setUp(self):
        root = config.FRONTEND_DIR.parent
        files = sorted(JS_DIR.glob("*.js"))
        files += sorted((root / "backend" / "extensions").glob("*/web/*.js"))
        self.root = root
        self.sources = {p.relative_to(root).as_posix(): p.read_text() for p in files}
        self.assertGreater(len(self.sources), 10, "frontend/js looks empty")

    def _resolve(self, importer: str, target: str) -> str:
        mod = target if target.endswith(".js") else target + ".js"
        if mod.startswith("/js/"):
            return "frontend/js/" + mod[len("/js/"):]
        return (importer.rsplit("/", 1)[0] + "/" + mod[2:]) if mod.startswith("./") else mod

    def test_every_named_import_is_really_exported(self):
        problems = []
        for name, raw in self.sources.items():
            code = strip_noise(raw)
            for block, target in re.findall(
                    r'import\s*\{([^}]+)\}\s*from\s*["\']((?:\./|/js/)[^"\']+)["\']', raw, re.S):
                mod = self._resolve(name, target)
                if mod not in self.sources:
                    problems.append(f"{name}: imports from missing module {mod}")
                    continue
                have = exported_names(strip_noise(self.sources[mod]))
                for want in (n.strip().split(" as ")[0].strip()
                             for n in block.split(",") if n.strip()):
                    if want not in have:
                        problems.append(f"{name}: {mod} does not export {want!r}")
            del code
        self.assertEqual(problems, [], "broken imports:\n" + "\n".join(problems))

    def test_every_called_function_is_bound(self):
        problems = []
        for name, raw in self.sources.items():
            code = strip_noise(raw)
            bound = bound_names(code) | GLOBALS
            called = set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', code))
            for fn in sorted(called - bound):
                problems.append(f"{name}: calls {fn}() - not declared, imported "
                                f"or a known global")
        self.assertEqual(problems, [],
                         "unbound calls (a missing import breaks the page at runtime, silently):\n" + "\n".join(problems))

if __name__ == "__main__":
    unittest.main()
