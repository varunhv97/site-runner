import pytest

CONTENT_DOMAIN = "sites.spinsite.app"


@pytest.fixture(autouse=True)
def content_domain(monkeypatch):
    """User content is served on subdomains of this domain (USERCONTENT_BASE_DOMAIN)."""
    monkeypatch.setenv("USERCONTENT_BASE_DOMAIN", CONTENT_DOMAIN)
    monkeypatch.delenv("CONTENT_ONLY", raising=False)
