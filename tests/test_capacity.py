import _core.capacity as capacity
import _core.mailer as mailer
from test_api import client, make_zip  # noqa: F401  (client is a fixture)


def upload(client, files):
    return client.post("/api/sites", files={"file": ("s.zip", make_zip(files))}, data={"name": "x"})


def test_uploads_past_the_site_wide_cap_are_refused(client, monkeypatch):
    monkeypatch.setenv("GLOBAL_STORAGE_LIMIT_MB", str(3000 / capacity.MB))  # 3,000 bytes for everyone
    assert upload(client, {"index.html": "a" * 1500}).status_code == 200
    r = upload(client, {"index.html": "b" * 1600})  # 1,500 + 1,600 > 3,000
    assert r.status_code == 507 and "storage is full" in r.json()["error"]
    assert upload(client, {"index.html": "a" * 1500}).status_code == 200  # already stored: costs nothing new


def test_one_alert_a_day_past_80_percent(client, monkeypatch):
    sent = []

    async def fake_send(to, subject, text, reply_to=None):
        sent.append(subject)
        return True

    monkeypatch.setattr(mailer, "send", fake_send)
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("REPORT_EMAIL", "abuse@spinsite.app")
    monkeypatch.setenv("GLOBAL_STORAGE_LIMIT_MB", str(1000 / capacity.MB))
    assert upload(client, {"index.html": "a" * 700}).status_code == 200  # 70%: no alert
    assert sent == []
    assert upload(client, {"index.html": "b" * 150}).status_code == 200  # 85%: alert
    assert upload(client, {"index.html": "c" * 50}).status_code == 200  # 90%: same day, no second alert
    assert len(sent) == 1 and "Storage at 85%" in sent[0]
