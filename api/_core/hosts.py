"""Which origin a request is for: the app (PUBLIC_BASE_URL) or user content (USERCONTENT_BASE_DOMAIN).

User HTML never runs on the app's origin. It is served from subdomains of the content domain:
  <site id>.<content domain>     one shared site (/ = live version, /@<n>/ = pinned version n)
  p-<local id>.<content domain>  one local preview (files handed over by /app, served by our service worker)
Site ids are 10 characters [a-z0-9]; local ids are 12 hex characters. Ids have no hyphen, so "p-" can't clash.
"""

import os
import re
from urllib.parse import urlsplit

DEV_APP_ORIGIN = "http://localhost:3000"
DEV_CONTENT_DOMAIN = "usercontent.localhost:8001"

# a shared site's host: its current link name, or its random id when it has none (both are DNS-safe labels)
SITE_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
PREVIEW_LABEL = re.compile(r"^p-([0-9a-f]{12})$")


class ConfigError(Exception):
    pass


def _production() -> bool:
    return os.environ.get("VERCEL_ENV") == "production"


def content_only() -> bool:
    """CONTENT_ONLY=1 on the deployment that serves user content: no app routes on any host."""
    return os.environ.get("CONTENT_ONLY", "").lower() in ("1", "true", "yes")


def app_origin() -> str:
    """The app's origin (no trailing slash). The preview boot page only talks to this origin."""
    base = os.environ.get("PUBLIC_BASE_URL")
    if not base:
        if _production():
            raise ConfigError("PUBLIC_BASE_URL must be set in production")
        return DEV_APP_ORIGIN
    return base.rstrip("/")


def content_domain() -> str:
    """USERCONTENT_BASE_DOMAIN, lower-cased, with a port when it isn't the default one (dev).
    It must not be the app's own host or a parent of it, else the app could serve user content."""
    domain = (os.environ.get("USERCONTENT_BASE_DOMAIN") or "").strip().lower().strip(".")
    if not domain:
        if _production():
            raise ConfigError("USERCONTENT_BASE_DOMAIN must be set in production")
        domain = DEV_CONTENT_DOMAIN
    app_host = (urlsplit(os.environ.get("PUBLIC_BASE_URL") or "").netloc or "").lower()
    if app_host and (app_host == domain or app_host.endswith("." + domain)):
        raise ConfigError("USERCONTENT_BASE_DOMAIN must not contain the app's host")
    return domain


def content_scheme(domain: str | None = None) -> str:
    """https, except for *.localhost in development (a secure context in browsers even over http)."""
    hostname = (domain or content_domain()).split(":", 1)[0]
    return "http" if hostname == "localhost" or hostname.endswith(".localhost") else "https"


def content_label(host: str | None) -> str | None:
    """For a Host header under the content domain: the subdomain label ("" for the bare domain). Else None.
    Only the Host header counts (a browser can't forge it), never X-Forwarded-Host."""
    if not host:
        return None
    try:
        domain = content_domain()
    except ConfigError:
        return None
    host = host.strip().lower().rstrip(".")
    if host == domain:
        return ""
    if host.endswith("." + domain):
        label = host[: -len(domain) - 1]
        return label if "." not in label else None
    return None


def site_label(site_id: str, slug: str | None) -> str:
    """A site's one canonical host label: its current link name, else its id. A name that looks like a preview
    host (p-…) or a punycode label (--) can't be a host, so such a site keeps its id."""
    if slug and not slug.startswith("p-") and "--" not in slug and SITE_LABEL.match(slug):
        return slug
    return site_id


def site_origin(label: str) -> str:
    domain = content_domain()
    return f"{content_scheme(domain)}://{label}.{domain}"


def preview_origin(local_id: str) -> str:
    domain = content_domain()
    return f"{content_scheme(domain)}://p-{local_id}.{domain}"
