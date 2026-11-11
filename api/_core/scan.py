"""Upload safety checks.

1. Executables are refused (400): by extension (lib/blocked-files.json, mirrored here and compared by a test)
   and by magic bytes (PE, ELF, Mach-O, zips that are APKs or JARs).
2. Phishing, seed-phrase and cryptomining pages are flagged: the version is stored, but the site is held for an
   admin's review (see holds.py). Signals are combined so ordinary prototypes with login forms aren't flagged.
   UPLOAD_SCAN=off turns this part off.
"""

import io
import os
import posixpath
import re
import struct
import zipfile

# Keep in sync with lib/blocked-files.json (tests/test_scan.py compares them).
BLOCKED_EXTENSIONS = (
    ".exe",
    ".msi",
    ".msix",
    ".dmg",
    ".pkg",
    ".apk",
    ".aab",
    ".scr",
    ".bat",
    ".cmd",
    ".com",
    ".ps1",
    ".vbs",
    ".jar",
    ".deb",
    ".rpm",
    ".appimage",
    ".iso",
    ".dll",
    ".so",
    ".dylib",
)
BLOCKED_BUNDLES = (".app",)  # folders: Foo.app/Contents/...

_MACHO = {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"}
_FAT = {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}  # also Java .class (0xCAFEBABE): told apart by the arch count


def _is_pe(data: bytes) -> bool:
    if data[:2] != b"MZ" or len(data) < 0x40:
        return False
    (offset,) = struct.unpack_from("<I", data, 0x3C)
    return data[offset : offset + 4] == b"PE\0\0"


def _is_app_archive(data: bytes) -> bool:
    """A zip that is an Android app or a Java archive, whatever its name."""
    if data[:4] != b"PK\x03\x04":
        return False
    try:
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    except (zipfile.BadZipFile, ValueError, OSError):
        return False
    return any(n in ("AndroidManifest.xml", "META-INF/MANIFEST.MF", "classes.dex") for n in names)


def executable_kind(data: bytes) -> str | None:
    """What kind of program these bytes are, or None."""
    head = data[:8]
    if _is_pe(data):
        return "Windows program"
    if head[:4] == b"\x7fELF":
        return "Linux program"
    if head[:4] in _MACHO:
        return "Mac program"
    if head[:4] in _FAT and len(head) == 8 and struct.unpack(">I", head[4:8])[0] < 20:
        return "Mac program"
    if _is_app_archive(data):
        return "Android or Java app"
    return None


def is_blocked(path: str, data: bytes) -> bool:
    lower = path.lower()
    if posixpath.splitext(lower)[1] in BLOCKED_EXTENSIONS:
        return True
    if any(seg.endswith(BLOCKED_BUNDLES) for seg in lower.split("/")[:-1]):
        return True
    return executable_kind(data) is not None


def first_executable(files: dict[str, bytes]) -> str | None:
    """The first executable file's path (in path order), or None."""
    return next((path for path in sorted(files) if is_blocked(path, files[path])), None)


# ---- content scan (phishing, seed phrases, miners) ----

_TEXT_EXT = (".html", ".htm", ".xhtml", ".js", ".mjs", ".cjs", ".svg")
_MAX_FILE = 512 * 1024  # bytes read per file
_MAX_TOTAL = 4 * 1024 * 1024  # bytes read per upload

_PASSWORD = re.compile(r"""type\s*[=:]\s*\\?["']?password\b""", re.I)
_BRANDS = re.compile(
    r"\b(google|gmail|microsoft|outlook|office\s?365|apple\s?id|icloud|paypal|facebook|instagram|netflix|amazon"
    r"|(?:online|internet|mobile)\s+banking|coinbase|metamask|binance|trust\s?wallet|seed\s+phrase"
    r"|recovery\s+phrase)\b",
    re.I,
)
# "Sign in with Google" buttons are everywhere in prototypes: not impersonation.
_SOCIAL_LOGIN = re.compile(
    r"\b(?:sign\s*in|log\s*in|sign\s*up|continue|login|register)\s+(?:with|using|via)\s+"
    r"(?:google|microsoft|apple|facebook|instagram|amazon|paypal)\b",
    re.I,
)
_TAGS = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>|<!--.*?-->|<[^>]+>", re.I | re.S)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title", re.I | re.S)
_SCRIPT = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.I | re.S)
_FORM = re.compile(r"<form\b([^>]*)>(.*?)(?:</form\s*>|$)", re.I | re.S)
_ACTION = re.compile(r"""\baction\s*=\s*["']?\s*((?:https?:)?//[^"'\s>]+)""", re.I)
_LOCAL = re.compile(r"^(?:https?:)?//(?:localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[::1\])(?:[:/]|$)", re.I)
# Places stolen credentials are commonly sent: chat bots, webhooks, form relays.
_EXFIL_CHANNELS = re.compile(
    r"api\.telegram\.org/bot|discord(?:app)?\.com/api/webhooks|hooks\.slack\.com/services|webhook\.site", re.I
)
_FORM_RELAYS = re.compile(r"formspree\.io|getform\.io|formsubmit\.co|usebasin\.com|submit-form\.com", re.I)
# fetch/XHR/beacon/axios/jQuery to an absolute URL, then the next few hundred characters (the request body)
_REQUEST = re.compile(
    r"""(?:fetch|sendBeacon|\.open|axios(?:\.post)?|\$\.(?:post|ajax))\s*\(\s*(?:["']POST["']\s*,\s*)?"""
    r"""["'`]((?:https?:)?//[^"'`\s]+)["'`](.{0,400})""",
    re.I | re.S,
)
_CREDS = re.compile(r"pass(?:word|wd)?\b|pwd\b", re.I)

_SEED_WORDS = re.compile(
    r"\b(?:seed|recovery|mnemonic|secret|backup)\s+(?:phrase|words)\b|\b(?:12|twelve|24|twenty[\s-]four)[\s-]words?\b",
    re.I,
)
_FIELD = re.compile(r"<(input|textarea)\b([^>]*)>", re.I)
_SEED_FIELD = re.compile(
    r"""(?:name|id|placeholder|aria-label)\s*=\s*["'][^"']*(?:seed|phrase|mnemonic|word\s*#?\d|\bwords\b)""", re.I
)
_TEXT_FIELD = re.compile(r"""^(?!.*\btype\s*=)|\btype\s*=\s*["']?(?:text|password)\b""", re.I | re.S)

_MINERS = re.compile(
    r"coinhive|coin-hive|cryptonight|coinimp|crypto-?loot|webminer|webmine\.pro|minero\.cc|\bminero\.js"
    r"|stratum\+(?:tcp|ssl)|xmrig|deepminer|jsecoin|monerominer",
    re.I,
)


def scan_enabled() -> bool:
    return os.environ.get("UPLOAD_SCAN", "on").strip().lower() not in ("off", "0", "false", "no")


def _texts(files: dict[str, bytes]) -> dict[str, str]:
    out, total = {}, 0
    for path in sorted(files):
        if not path.lower().endswith(_TEXT_EXT) or total >= _MAX_TOTAL:
            continue
        chunk = files[path][: min(_MAX_FILE, _MAX_TOTAL - total)]
        total += len(chunk)
        out[path] = chunk.decode("utf-8", errors="replace")
    return out


def _visible(html: str) -> str:
    title = " ".join(_TITLE.findall(html))
    return f"{title} {_TAGS.sub(' ', html)}"


def _external(url: str) -> bool:
    return not _LOCAL.match(url)


def _host(url: str) -> str:
    return re.sub(r"^(?:https?:)?//", "", url).split("/", 1)[0][:60]


def _phishing(pages: dict[str, str], scripts: dict[str, str]) -> str | None:
    """A password field plus at least two of: a brand, credentials going to another site, a known drop."""
    code = "\n".join(list(scripts.values()) + list(pages.values()))
    if not _PASSWORD.search(code):
        return None
    brand = None
    for html in pages.values():
        m = _BRANDS.search(_SOCIAL_LOGIN.sub(" ", _visible(html)))
        if m:
            brand = re.sub(r"\s+", " ", m.group(1))
            break

    channel = _EXFIL_CHANNELS.search(code)
    relay = _FORM_RELAYS.search(code)
    target = None
    for html in pages.values():  # a form with a password field posting to another site
        for attrs, body in _FORM.findall(html):
            action = _ACTION.search(attrs)
            if action and _external(action.group(1)) and _PASSWORD.search(body):
                target = action.group(1)
                break
    if not target:  # a script sending something password-like to another site
        for m in _REQUEST.finditer(code):
            if _external(m.group(1)) and _CREDS.search(m.group(2)):
                target = m.group(1)
                break

    if channel and (brand or target or relay):
        return f"a login page that sends passwords to {channel.group(0).split('/')[0]}"
    if brand and (target or relay):
        where = _host(target) if target else _host(relay.group(0))
        return f"a sign-in page imitating {brand} that sends passwords to {where}"
    return None


def _seed_phrase(pages: dict[str, str]) -> str | None:
    for html in pages.values():
        if not _SEED_WORDS.search(_visible(html)):
            continue
        fields = _FIELD.findall(html)
        texty = [a for tag, a in fields if tag.lower() == "textarea" or _TEXT_FIELD.search(a)]
        if len(texty) >= 12 or any(_SEED_FIELD.search(a) for _, a in fields):
            return "a page asking for a wallet's seed or recovery phrase"
    return None


def _miner(files: dict[str, bytes], pages: dict[str, str], scripts: dict[str, str]) -> str | None:
    for path in files:
        if _MINERS.search(posixpath.basename(path)):
            return f"cryptocurrency mining code ({path})"
    code = list(scripts.items()) + [
        (p, " ".join(attrs + body for attrs, body in _SCRIPT.findall(html))) for p, html in pages.items()
    ]
    for path, text in code:
        m = _MINERS.search(text)
        if m:
            return f"cryptocurrency mining code ({m.group(0).lower()} in {path})"
    return None


def scan_upload(files: dict[str, bytes]) -> list[str]:
    """Reasons to hold this upload for review (empty when it looks fine or scanning is off)."""
    if not scan_enabled():
        return []
    texts = _texts(files)
    pages = {p: t for p, t in texts.items() if p.lower().endswith((".html", ".htm", ".xhtml", ".svg"))}
    scripts = {p: t for p, t in texts.items() if p not in pages}
    found = [_phishing(pages, scripts), _seed_phrase(pages), _miner(files, pages, scripts)]
    return [f"Looks like {r}" for r in found if r]
