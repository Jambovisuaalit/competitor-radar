"""Fetch Markdown with Firecrawl's v1 client and keep atomic local snapshots.

Requires Python 3.10+ and a firecrawl-py release exporting V1FirecrawlApp.
Call get_previous_snapshot(url) before scraping for the latest stored version;
scrape_page also returns that version in previous_markdown. A bare domain
selects its HTTPS root page; pass a full URL to select a pricing page.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from firecrawl import V1FirecrawlApp
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError
from rich.console import Console

HISTORY_DIR = Path(__file__).resolve().parent.parent / "history"
API_ERROR_MESSAGE = (
    "Firecrawl API error or quota exceeded. Top up or get a key with 10% off: "
    "https://firecrawl.link/jami-harju"
)
MISSING_KEY_MESSAGE = (
    "Virhe: FIRECRAWL_API_KEY puuttuu! Hanki avain "
    "(10% alennus ensimmäisestä ostosta): https://firecrawl.link/jami-harju"
)
console = Console(stderr=True)


class ScrapeDocument(BaseModel):
    """Validate only the SDK response fields needed by this application."""

    model_config = ConfigDict(extra="ignore")
    success: bool = True
    markdown: StrictStr
    error: Any = None
    metadata: dict[str, Any] | None = None


class ScrapeResult(BaseModel):
    """Stable application result, including safe, machine-readable errors."""

    success: bool
    url: str
    markdown: str | None = None
    previous_markdown: str | None = None
    snapshot_path: str | None = None
    error: Literal["invalid_url", "api_error", "invalid_response", "history_error"] | None = None


def _page_identity(value: str) -> tuple[str, str]:
    """Return canonical URL and a filesystem-safe, page-specific prefix."""
    parts = urlsplit(value if "://" in value else f"https://{value}")
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise ValueError("Expected an HTTP(S) URL without credentials")
    host = parts.hostname.encode("idna").decode("ascii").lower()
    authority = f"[{host}]" if ":" in host else host
    port = parts.port
    if port is not None and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
        authority += f":{port}"
    canonical = urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
    domain = re.sub(r"[^a-z0-9.-]", "_", host)[:100]
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return canonical, f"{domain}-{digest}"


def _read_previous(prefix: str, directory: Path) -> str | None:
    """Read newest committed snapshot; report unreadable history to caller."""
    if not directory.exists():
        return None
    snapshots = sorted(directory.glob(f"{prefix}_*.md"), reverse=True)
    if not snapshots:
        return None
    return snapshots[0].read_text(encoding="utf-8")


def get_previous_snapshot(domain: str) -> str | None:
    """Return the latest saved version, or None if absent or unreadable.

    Despite the argument name, full URLs are supported and recommended.
    Invalid identifiers raise ValueError. Filesystem errors are printed safely.
    """
    _, prefix = _page_identity(domain)
    try:
        return _read_previous(prefix, HISTORY_DIR)
    except (OSError, UnicodeError):
        console.print("Historiaa ei voitu lukea.", style="red")
        return None


def _save_snapshot(prefix: str, markdown: str, directory: Path) -> Path:
    """Commit UTF-8 Markdown atomically, with unique UTC filenames."""
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = directory / f"{prefix}_{timestamp}-{uuid4().hex}.md"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(markdown)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        return destination
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class CompetitorScraper:
    """Synchronous scraper; failed requests never overwrite local history."""

    def __init__(self, api_key: str, *, timeout_ms: int = 30_000) -> None:
        if not api_key.strip():
            console.print(MISSING_KEY_MESSAGE, style="red", markup=False)
            raise ValueError(MISSING_KEY_MESSAGE)
        if timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        self._client = V1FirecrawlApp(api_key=api_key)
        self._timeout_ms = timeout_ms

    def scrape_page(self, url: str) -> dict[str, Any]:
        """Fetch, validate and save Markdown, returning a structured result.

        success means both fetch and persistence succeeded. On persistence
        failure, fetched Markdown remains available but success is False.
        SDK exceptions (including HTTP errors and timeouts) are contained;
        their messages are not printed because they may contain credentials.
        """
        try:
            canonical, prefix = _page_identity(url)
        except (ValueError, UnicodeError):
            return ScrapeResult(success=False, url=url, error="invalid_url").model_dump()

        try:
            response = self._client.scrape_url(
                canonical, formats=["markdown"], timeout=self._timeout_ms, max_age=0
            )
        except Exception:
            # The v1 SDK can raise generic Exception as well as HTTP exceptions.
            console.print(API_ERROR_MESSAGE, style="red", markup=False)
            return ScrapeResult(success=False, url=url, error="api_error").model_dump()

        payload = response.model_dump() if isinstance(response, BaseModel) else response
        try:
            document = ScrapeDocument.model_validate(payload)
            status = (document.metadata or {}).get("statusCode")
            if not document.success or document.error or (isinstance(status, int) and status >= 400):
                raise ValueError("Unsuccessful scrape")
        except (ValidationError, ValueError):
            console.print(API_ERROR_MESSAGE, style="red", markup=False)
            return ScrapeResult(success=False, url=url, error="invalid_response").model_dump()

        result = ScrapeResult(success=True, url=url, markdown=document.markdown)
        try:
            result.previous_markdown = _read_previous(prefix, HISTORY_DIR)
            result.snapshot_path = str(_save_snapshot(prefix, document.markdown, HISTORY_DIR))
        except (OSError, UnicodeError):
            console.print("Markdown-historian lukeminen tai tallentaminen epäonnistui.", style="red")
            result.success = False
            result.error = "history_error"
        return result.model_dump()
