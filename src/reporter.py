"""Render plain-text Rich tables and atomically write Markdown reports."""
from __future__ import annotations

import html
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .analyzer import CompetitorInsight
from .config import ROOT


class ReportEntry(BaseModel):
    company_name: str
    url: str
    insight: CompetitorInsight | None = None
    error: str | None = None


def _escape(value: str) -> str:
    value = html.escape(value, quote=False)
    for character in ("\\", "`", "*", "_", "{", "}", "[", "]", "#", "|", "!"):
        value = value.replace(character, "\\" + character)
    return value.replace("\r", " ").replace("\n", " ")


def _atomic_write(path: Path, content: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def render_report(entries: list[ReportEntry], *, console: Console | None = None) -> None:
    output = console or Console()
    table = Table(title="Competitor Radar", show_lines=True)
    for title in ("Kilpailija / sivu", "Tila", "Hinnoittelu", "Ominaisuudet", "Yhteenveto"):
        table.add_column(title, overflow="fold")
    for entry in entries:
        insight = entry.insight
        table.add_row(*[Text(value) for value in (
            f"{entry.company_name}\n{entry.url}",
            "Virhe" if entry.error else ("Kriittinen muutos" if insight and insight.has_critical_changes else "OK"),
            "\n".join(insight.pricing_changes) if insight else "—",
            "\n".join(insight.feature_updates) if insight else "—",
            entry.error or (insight.summary if insight else "Ei analyysiä"),
        )])
    output.print(table)


def save_report(entries: list[ReportEntry], *, directory: Path = ROOT / "reports") -> Path:
    now = datetime.now(timezone.utc)
    lines = ["# Competitor Radar", "", f"Ajo: {now.isoformat()} (UTC)", ""]
    for entry in entries:
        lines.extend([f"## {_escape(entry.company_name)}", "", f"Sivu: {_escape(entry.url)}", ""])
        if entry.error:
            lines.extend([f"**Virhe:** {_escape(entry.error)}", ""])
            continue
        insight = entry.insight
        if insight is None:
            lines.extend(["Ei analyysiä.", ""])
            continue
        lines.extend([f"Kriittisiä muutoksia: {'kyllä' if insight.has_critical_changes else 'ei'}", ""])
        for heading, items in (("Hinnoittelu", insight.pricing_changes), ("Ominaisuudet", insight.feature_updates)):
            lines.extend([f"### {heading}", ""])
            lines.extend([f"- {_escape(item)}" for item in items] or ["- Ei havaintoja."])
            lines.append("")
        lines.extend(["### Yhteenveto", "", _escape(insight.summary), ""])
    content = "\n".join(lines)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"report_{now:%Y-%m-%d}.md"
    _atomic_write(destination, content)
    _atomic_write(directory / "latest_report.md", content)
    return destination
