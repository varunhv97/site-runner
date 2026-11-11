import json
import re

from test_api import client, create, make_zip, page  # noqa: F401  (client is a fixture)


def config(html: str) -> dict:
    return json.loads(re.search(r"const C = (.*);\n", html).group(1))


def test_html_gets_wrapper_with_strict_csp(client):
    sid = create(client, {"index.html": "<p>secret-marker</p>", "app.js": "x=1"})["site"]["id"]
    r = client.get(f"/s/{sid}/")
    csp = r.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'<script nonce="{nonce}">' in r.text
    assert "unsafe-inline" not in csp and "sandbox" not in csp  # our code, on this app's origin
    assert "secret-marker" not in r.text  # the site's content never enters the wrapper
    assert config(r.text) == {
        "site": sid,
        "base": f"/s/{sid}/",
        "frameBase": f"/r/{sid}/",
        "frame": f"/r/{sid}/",
        "limit": 512 * 1024,
    }
    assert client.get(f"/s/{sid}/").headers["content-security-policy"] != csp  # a fresh nonce each time

    # other files are served as before
    js = client.get(f"/s/{sid}/app.js")
    assert js.text == "x=1" and js.headers["content-security-policy"].startswith("sandbox ")


def test_frame_stays_sandboxed_and_escapes_the_page(client):
    evil = "</script><script>parent.alert(1)</script><!-- __BASE__ __LIMIT__"
    sid = create(client, {"index.html": evil})["site"]["id"]
    r = client.get(f"/r/{sid}/")
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox ") and "allow-same-origin" not in csp
    assert r.text.count("</script") == 1  # only our own closing tag
    assert page(client, f"/s/{sid}/") == evil  # placeholders inside the page are left alone


def test_storage_key_is_the_site_id_not_the_link_name(client):
    r = client.post(
        "/api/sites",
        files={"file": ("s.zip", make_zip({"index.html": "v1"}))},
        data={"name": "x", "slug": "my-notes"},
    )
    sid = r.json()["site"]["id"]
    c = config(client.get("/s/my-notes@1/").text)
    assert c["site"] == sid
    assert c["frame"] == "/r/my-notes@1/" and c["base"] == "/s/my-notes@1/"


def test_frame_paths_resolve_like_share_paths(client):
    sid = create(client, {"index.html": "home", "docs/index.html": "docs"})["site"]["id"]
    r = client.get(f"/r/{sid}", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == f"/r/{sid}/"
    r = client.get(f"/r/{sid}/docs", follow_redirects=False)
    assert r.headers["location"] == f"/r/{sid}/docs/"
    assert config(client.get(f"/s/{sid}/docs/").text)["frame"] == f"/r/{sid}/docs/"
    assert client.get(f"/r/{sid}/nope.html").status_code == 404
    assert client.get(f"/s/{sid}/nope.html").status_code == 404
