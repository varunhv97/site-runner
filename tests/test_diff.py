"""`spinsite diff`: two versions, or a version and a local folder, against the API app."""

import json

from spinsite import diff as diff_mod
from test_auth import client  # noqa: F401  (client is a fixture)
from test_cli import site_dir, spin  # noqa: F401  (spin is a fixture)
from test_pull import ok, two  # noqa: F401  (two is a fixture)


def deploy(invoke, tmp_path, files, name, *args):
    return json.loads(ok(invoke("deploy", str(site_dir(tmp_path, files, name)), *args, "--json")).output)


def test_diff_between_versions(two, tmp_path):
    invoke, as_alice, _ = two
    as_alice()
    v1 = {"index.html": "a\nb\nc\n", "css/site.css": "p{}", "img/x.png": "\x00\x01", "same.txt": "same"}
    deploy(invoke, tmp_path, v1, "v1", "--slug", "diffs")
    v2 = {"index.html": "a\nB\nc\n", "css/site.css": "p{}", "img/x.png": "\x00\x02", "same.txt": "same", "new.js": "1"}
    deploy(invoke, tmp_path, v2, "v2", "--site", "diffs")

    body = json.loads(ok(invoke("diff", "diffs", "--from", "1", "--json")).output)  # --to defaults to live (2)
    assert (body["from"], body["to"], body["added"], body["removed"]) == (1, 2, ["new.js"], [])
    assert body["unchanged"] == 2 and not body["truncated"]
    png, html = body["changed"]  # sorted by path
    assert html["path"] == "index.html" and (html["added_lines"], html["removed_lines"]) == (1, 1)
    assert "--- v1/index.html" in html["diff"] and "-b\n+B\n" in html["diff"]
    assert png == {"path": "img/x.png", "old_size": 2, "new_size": 2, "kind": "binary"}

    text = ok(invoke("diff", "diffs", "--from", "1")).output
    assert "added    new.js" in text and "changed  index.html (+1 -1)" in text and "+B" in text
    assert "+B" not in ok(invoke("diff", "diffs", "--from", "1", "--stat")).output
    only = json.loads(ok(invoke("diff", "diffs", "--from", "1", "--path", "img", "--json")).output)
    assert [f["path"] for f in only["changed"]] == ["img/x.png"] and only["added"] == []
    assert "no changes" in ok(invoke("diff", "diffs", "--from", "2", "--to", "2")).output


def test_diff_with_a_pulled_folder(two, tmp_path):
    invoke, as_alice, as_bob = two
    as_alice()
    deploy(invoke, tmp_path, {"index.html": "hello\n", "old.html": "x"}, "v1", "--slug", "team")
    ok(invoke("invite", "team", "bob@example.com"))

    as_bob()  # a member can diff too
    ok(invoke("pull", "team", "work"))
    (tmp_path / "work/index.html").write_text("hello there\n")
    (tmp_path / "work/old.html").unlink()
    (tmp_path / "work/.env").write_text("SECRET=1")  # hidden: never deployed, so never in a diff
    body = json.loads(ok(invoke("diff", "--folder", "work", "--json")).output)
    assert (body["from"], body["to"], body["added"], body["removed"]) == (1, "local", [], ["old.html"])
    assert "+hello there" in body["changed"][0]["diff"]

    r = invoke("diff")
    assert r.exit_code == 1 and "--folder" in r.output
    r = invoke("diff", "nope")
    assert r.exit_code == 1


def test_long_diffs_are_cut(two, tmp_path, monkeypatch):
    invoke, as_alice, _ = two
    as_alice()
    monkeypatch.setattr(diff_mod, "MAX_DIFF_CHARS", 60)
    deploy(invoke, tmp_path, {"index.html": "x\n", "a.txt": "1\n", "b.txt": "1\n"}, "v1", "--slug", "long")
    deploy(invoke, tmp_path, {"index.html": "y\n", "a.txt": "2\n", "b.txt": "2\n"}, "v2", "--site", "long")
    body = json.loads(ok(invoke("diff", "long", "--from", "1", "--json")).output)
    assert body["truncated"] and len(body["changed"]) == 3
    assert all("added_lines" in f for f in body["changed"]) and not all("diff" in f for f in body["changed"])
