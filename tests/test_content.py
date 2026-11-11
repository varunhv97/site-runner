"""The content origin: shared sites on <id>.sites.spinsite.app, and the app host never serving user HTML."""

import asyncio
import json
import pathlib

import pytest

from _core import hosts
from _core.paths import resolve_path
from test_api import client, create, make_zip  # noqa: F401  (client is a fixture)

DOMAIN = "sites.spinsite.app"
RULES = json.loads((pathlib.Path(__file__).parent / "path_rules.json").read_text())


def on(sid: str) -> dict:
    return {"Host": f"{sid}.{DOMAIN}"}


def site(client, files=None, **data):
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip(files or {"index.html": "v1"}))}, data=data)
    assert r.status_code == 200, r.text
    return r.json()


def publish(client, body, files):
    r = client.post(
        f"/api/sites/{body['site']['id']}/versions",
        files={"file": ("s.zip", make_zip(files))},
        headers={"Authorization": f"Bearer {body['edit_token']}"},
    )
    assert r.status_code == 200, r.text


# ---- path rules (the preview service worker runs the same cases: tests/path_rules.test.mjs) ----


@pytest.mark.parametrize("rules", RULES["sites"], ids=lambda r: r["entry"])
def test_path_rules(rules):
    files = set(rules["files"])

    async def exists(p):
        return p in files

    for case in rules["cases"]:
        got = asyncio.run(resolve_path(case["path"], rules["entry"], exists))
        if "file" in case:
            want = ("file", case["file"])
        elif "redirect" in case:
            want = ("redirect", case["redirect"], case["status"])
        else:
            want = ("missing",)
        assert got == want, case


def test_server_follows_the_path_rules(client):
    rules = RULES["sites"][0]
    body = site(client, {p: f"<p>{p}</p>" for p in rules["files"]})
    for case in rules["cases"]:
        r = client.get("/" + case["path"], headers=on(body["site"]["id"]), follow_redirects=False)
        if "file" in case:
            assert r.status_code == 200 and r.text == f"<p>{case['file']}</p>", case
        elif "redirect" in case:
            assert r.status_code == case["status"], case
            assert r.headers["location"] == "/" + case["redirect"].replace(" ", "%20"), case
        else:
            assert r.status_code == 404, case


# ---- the app host never serves user HTML ----


def test_share_links_redirect_to_the_site_host(client):
    body = site(client, {"index.html": "home", "docs/a.html": "a", "app.js": "x=1"}, slug="trail-map")
    sid = body["site"]["id"]
    assert body["url"].endswith("/s/trail-map/")  # share links stay on the app's domain
    host = f"https://trail-map.{DOMAIN}"  # a named site lives on its name
    cases = {
        "/s/trail-map/": f"{host}/",
        "/s/trail-map": f"{host}/",
        f"/s/{sid}/docs/a.html?x=1&y=2": f"{host}/docs/a.html?x=1&y=2",  # by id too: still its one host
        "/s/trail-map@1/docs/a.html": f"{host}/@1/docs/a.html",
        "/s/trail-map@1": f"{host}/@1/",
        "/s/trail-map/app.js": f"{host}/app.js",  # not just HTML: everything moved
        "/r/trail-map@1/docs/a.html": f"{host}/@1/docs/a.html",  # the old frame links
        "/s/trail-map/a%20b.html": f"{host}/a%20b.html",
    }
    for url, location in cases.items():
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 308 and r.headers["location"] == location, url
        assert "set-cookie" not in r.headers
    # after a rename, the old link name forwards straight to the new name's host
    auth = {"Authorization": f"Bearer {body['edit_token']}"}
    assert client.post(f"/api/sites/{sid}/slug", json={"slug": "ridge-loop"}, headers=auth).status_code == 200
    r = client.get("/s/trail-map@1/?q=1", follow_redirects=False)
    assert r.headers["location"] == f"https://ridge-loop.{DOMAIN}/@1/?q=1"


def test_unknown_sites_and_versions_404_on_the_app_host(client):
    sid = site(client)["site"]["id"]
    for url in ("/s/nosuchsite/", f"/s/{sid}@2/", f"/s/{sid}@x/", f"/r/{sid}@0/", "/s/a@b@c/"):
        assert client.get(url, follow_redirects=False).status_code == 404, url


def test_the_api_does_not_serve_previews_or_content_on_the_app_host(client):
    sid = site(client)["site"]["id"]
    for path in ("/preview/abc/1/", "/preview/abc/1/index.html", "/__sr/boot", "/__sr/sw.js", "/", "/index.html"):
        assert client.get(path, follow_redirects=False).status_code == 404, path
    assert client.get("/@1/", headers={"Host": "spinsite.app"}).status_code == 404
    assert client.get("/", headers={"Host": f"{sid}.evil.example"}).status_code == 404


# ---- the content host ----


def test_content_hosts_have_no_app_routes(client):
    sid = site(client)["site"]["id"]
    for path in ("/api/auth/me", "/api/sites", f"/api/sites/{sid}", "/api/health", "/s/x/", f"/s/{sid}/", "/r/x/"):
        r = client.get(path, headers=on(sid), follow_redirects=False)
        assert r.status_code == 404, path
    r = client.post("/api/sites", headers=on(sid), files={"file": ("s.zip", make_zip({"index.html": "x"}))})
    assert r.status_code in (404, 405)
    assert client.get("/api/auth/me", headers={"Host": DOMAIN}).status_code == 404  # the bare domain too


def test_content_responses_never_carry_cookies(client, monkeypatch):
    sid = site(client, {"index.html": "x", "a.css": "p{}", "d/index.html": "d"})["site"]["id"]
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")  # sign-in on: the app would set cookies on its own routes
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    cookie = {"Cookie": "sr_session=abc; __Host-sr_session=def; other=1"}
    for path in ("/", "/a.css", "/d", "/d/", "/missing", "/@1/", "/@1", "/@9/", "/api/auth/login", "/api/auth/me"):
        r = client.get(path, headers=on(sid) | cookie, follow_redirects=False)
        assert "set-cookie" not in r.headers, path
        assert r.headers["content-security-policy"].startswith("sandbox "), path
        assert r.headers["origin-agent-cluster"] == "?1", path
        assert r.headers["x-content-type-options"] == "nosniff", path


def test_sandbox_keeps_the_sites_own_origin(client):
    sid = site(client, {"index.html": "<p>hi</p>", "a.js": "x=1"})["site"]["id"]
    r = client.get("/", headers=on(sid))
    csp = r.headers["content-security-policy"]
    flags = set(csp.split()[1:])
    assert csp.split()[0] == "sandbox"
    assert {"allow-same-origin", "allow-scripts", "allow-forms", "allow-popups", "allow-modals"} <= flags
    assert {"allow-popups-to-escape-sandbox", "allow-downloads", "allow-top-navigation-by-user-activation"} <= flags
    assert "allow-top-navigation" not in flags
    assert "access-control-allow-origin" not in r.headers  # HTML isn't readable cross-origin
    assert client.get("/a.js", headers=on(sid)).headers["access-control-allow-origin"] == "*"


def test_service_workers_are_refused_on_site_hosts(client):
    sid = site(client, {"index.html": "x", "sw.js": "self.onfetch = () => {}"})["site"]["id"]
    assert client.get("/sw.js", headers=on(sid)).status_code == 200
    r = client.get("/sw.js", headers=on(sid) | {"Service-Worker": "script"})
    assert r.status_code == 403
    assert client.get("/@1/sw.js", headers=on(sid) | {"Service-Worker": "script"}).status_code == 403


def test_pinned_and_live_on_one_origin(client):
    body = site(client, {"index.html": "v1", "a.css": "one"})
    sid = body["site"]["id"]
    publish(client, body, {"index.html": "v2", "a.css": "two"})
    assert client.get("/", headers=on(sid)).text == "v2"
    assert client.get("/@1/", headers=on(sid)).text == "v1"
    assert client.get("/@1/a.css", headers=on(sid)).text == "one"
    assert client.get("/@2/a.css", headers=on(sid)).text == "two"
    r = client.get("/@1", headers=on(sid), params={"q": "1"}, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/@1/?q=1"
    assert client.get("/@3/", headers=on(sid)).status_code == 404
    assert client.get("/@0/", headers=on(sid)).status_code == 404
    assert client.get("/@99999999999/", headers=on(sid)).status_code == 404  # too long for a version: a path
    # caching: pinned files may be cached, the start page and live files revalidate
    assert client.get("/@1/a.css", headers=on(sid)).headers["cache-control"] == "public, max-age=3600"
    assert client.get("/@1/", headers=on(sid)).headers["cache-control"] == "no-cache"
    assert client.get("/a.css", headers=on(sid)).headers["cache-control"] == "no-cache"


def test_etags(client):
    sid = site(client, {"index.html": "<p>hi</p>", "a.css": "p{}"})["site"]["id"]
    for path in ("/a.css", "/", "/@1/a.css"):
        first = client.get(path, headers=on(sid))
        tag = first.headers["etag"]
        again = client.get(path, headers=on(sid) | {"If-None-Match": tag})
        assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag
        assert again.headers["content-security-policy"].startswith("sandbox ")
        assert client.get(path, headers=on(sid) | {"If-None-Match": '"other"'}).status_code == 200
    assert client.get("/", headers=on(sid)).headers["etag"] != client.get("/a.css", headers=on(sid)).headers["etag"]


def test_unknown_and_deleted_sites_404(client):
    body = site(client)
    sid = body["site"]["id"]
    assert client.get("/", headers=on("abcdefghij")).status_code == 404
    assert client.get("/", headers=on("not-a-site")).status_code == 404
    assert client.get("/", headers={"Host": f"a.b.{DOMAIN}"}).status_code == 404
    client.delete(f"/api/sites/{sid}", headers={"Authorization": f"Bearer {body['edit_token']}"})
    assert client.get("/", headers=on(sid)).status_code == 404


def test_each_site_has_one_host_its_name_else_its_id(client):
    # one host per site, so visitors' saved data is never split between two origins
    body = site(client, {"index.html": "home", "a b.html": "x"}, slug="helloworld")  # a name shaped like an id
    sid = body["site"]["id"]
    assert client.get("/", headers=on("helloworld")).text == "home"
    r = client.get("/a%20b.html?x=1", headers=on(sid), follow_redirects=False)  # the id goes to the name
    assert r.status_code == 308 and r.headers["location"] == f"https://helloworld.{DOMAIN}/a%20b.html?x=1"
    r = client.get("/@1/", headers=on(sid), follow_redirects=False)
    assert r.headers["location"] == f"https://helloworld.{DOMAIN}/@1/"

    auth = {"Authorization": f"Bearer {body['edit_token']}"}
    assert client.post(f"/api/sites/{sid}/slug", json={"slug": "hello-again"}, headers=auth).status_code == 200
    r = client.get("/", headers=on("helloworld"), follow_redirects=False)  # an earlier name forwards
    assert r.status_code == 308 and r.headers["location"] == f"https://hello-again.{DOMAIN}/"
    assert client.get("/", headers=on("hello-again")).text == "home"

    unnamed = site(client)["site"]["id"]  # no name: its id is its host
    assert client.get("/", headers=on(unnamed)).text == "v1"
    assert client.get("/", headers=on("no-such-site")).status_code == 404


def test_names_that_look_like_preview_hosts_are_refused(client):
    r = client.post("/api/sites", files={"file": ("s.zip", make_zip({"index.html": "x"}))}, data={"slug": "p-thing"})
    assert r.status_code == 400 and "p-" in r.json()["error"]
    assert hosts.site_label("abc1234567", "p-legacy") == "abc1234567"  # an older p- name keeps the id host
    assert hosts.site_label("abc1234567", "ok-name") == "ok-name"


def test_hosts_match_case_insensitively_and_with_a_port(client, monkeypatch):
    sid = site(client)["site"]["id"]
    assert client.get("/", headers={"Host": f"{sid}.SITES.spinsite.app"}).status_code == 200
    monkeypatch.setenv("USERCONTENT_BASE_DOMAIN", "usercontent.localhost:8811")
    assert client.get("/", headers={"Host": f"{sid}.usercontent.localhost:8811"}).text == "v1"
    assert client.get("/", headers={"Host": f"{sid}.usercontent.localhost:9999"}).status_code == 404  # app host
    r = client.get(f"/s/{sid}/", follow_redirects=False)
    assert r.headers["location"] == f"http://{sid}.usercontent.localhost:8811/"  # *.localhost: http in dev


def test_pinned_name_folders_are_rejected(client):
    for files in ({"@3/index.html": "x", "index.html": "y"}, {"site/@12/a.html": "x", "site/index.html": "y"}):
        r = client.post("/api/sites", files={"file": ("s.zip", make_zip(files))})
        assert r.status_code == 400 and "@" in r.json()["error"], files
    site(client, {"index.html": "x", "@home/a.html": "ok", "docs/@3/a.html": "ok"})  # only top-level @<digits>


def test_content_only_deployment_has_no_app_routes(client, monkeypatch):
    sid = site(client)["site"]["id"]
    monkeypatch.setenv("CONTENT_ONLY", "1")
    assert client.get("/api/health").status_code == 404
    assert client.get(f"/s/{sid}/", follow_redirects=False).status_code == 404
    assert client.get("/", headers=on(sid)).text == "v1"


def test_content_domain_config(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://spinsite.app")
    monkeypatch.setenv("USERCONTENT_BASE_DOMAIN", "spinsite.app")  # the app itself: refused
    with pytest.raises(hosts.ConfigError):
        hosts.content_domain()
    assert hosts.content_label("abc.spinsite.app") is None
    monkeypatch.setenv("USERCONTENT_BASE_DOMAIN", "sites.spinsite.app")
    assert hosts.site_origin("abcdefghij") == "https://abcdefghij.sites.spinsite.app"
    assert hosts.content_label("spinsite.app") is None
    monkeypatch.delenv("USERCONTENT_BASE_DOMAIN")
    monkeypatch.setenv("VERCEL_ENV", "production")
    with pytest.raises(hosts.ConfigError):
        hosts.content_domain()


def test_config_endpoint(client):
    assert client.get("/api/config").json() == {"content_scheme": "https", "content_domain": DOMAIN}


# ---- local previews: p-<local id> hosts serve only our boot page and service worker ----

PREVIEW = {"Host": f"p-0123456789ab.{DOMAIN}"}


def test_preview_boot_page(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://spinsite.app")
    r = client.get("/__sr/boot", headers=PREVIEW | {"Cookie": "__Host-sr_session=x"})
    assert r.status_code == 200 and "set-cookie" not in r.headers
    csp = r.headers["content-security-policy"]
    nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
    assert csp.startswith("sandbox ") and "allow-same-origin" in csp
    assert f"script-src 'nonce-{nonce}'" in csp and "unsafe-inline" not in csp
    assert "worker-src 'self'" in csp and "frame-ancestors https://spinsite.app" in csp
    assert r.text.count(f'<script nonce="{nonce}">') == 1
    assert 'const SR_APP = "https://spinsite.app";' in r.text  # talks to the app's exact origin only
    assert r.headers["origin-agent-cluster"] == "?1" and r.headers["cache-control"] == "no-store"
    assert client.get("/__sr/boot", headers=PREVIEW).headers["content-security-policy"] != csp  # fresh nonce


def test_preview_service_worker(client):
    r = client.get("/__sr/sw.js", headers=PREVIEW | {"Service-Worker": "script"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["service-worker-allowed"] == "/"
    assert "function srResolvePath" in r.text and "__SANDBOX_CSP__" not in r.text
    assert "allow-same-origin" in r.text.split("const SR_SANDBOX = ", 1)[1].split(";\n", 1)[0]


def test_previews_files_never_come_from_the_server(client):
    sid = site(client)["site"]["id"]
    for path in ("/", "/index.html", "/@1/", "/__sr/other"):
        r = client.get(path, headers=PREVIEW)
        assert r.status_code == 404 and "set-cookie" not in r.headers, path
        assert r.headers["content-security-policy"].startswith("sandbox "), path
    # only our worker may register on a preview host
    assert client.get("/evil-sw.js", headers=PREVIEW | {"Service-Worker": "script"}).status_code == 403
    # previews have no app routes either, and a site id isn't a preview id
    assert client.get("/api/auth/me", headers=PREVIEW).status_code == 404
    assert client.get("/__sr/boot", headers={"Host": f"p-{sid}.{DOMAIN}"}).status_code == 404
    assert (
        client.get("/__sr/boot", headers={"Host": f"p-0123456789AB.{DOMAIN}"}).status_code == 200
    )  # Host is case-insensitive
    assert client.get("/__sr/boot", headers=on(sid)).status_code == 404  # a site host has no boot page
