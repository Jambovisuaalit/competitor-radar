"""CLI entrypoint: python main.py [--dry-run] [--target URL]."""
from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console

from src.analyzer import AnalysisError, CompetitorInsight, RadarAnalyzer
from src.config import load_config, load_settings
from src.reporter import ReportEntry, render_report, save_report
from src.scraper import CompetitorScraper, _page_identity

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Competitor Radar — verkkosivujen kilpailijaseuranta")
    parser.add_argument("--dry-run", action="store_true", help="Validoi kohteet ilman API-kutsuja tai tiedostojen kirjoitusta")
    parser.add_argument("--target", help="Aja vain tämä competitors.json-tiedostossa oleva URL")
    args = parser.parse_args(argv)
    try:
        config = load_config()
        selected = _page_identity(args.target)[0] if args.target else None
        targets = [
            (company, str(url))
            for company in config.competitors for url in company.urls
            if selected is None or _page_identity(str(url))[0] == selected
        ]
        if not targets:
            console.print("Virhe: --target ei vastaa yhtään konfiguroitua URL-osoitetta.", style="red")
            return 2
        if args.dry_run:
            for company, url in targets:
                console.print(f"[dry-run] {company.name}: {url}", markup=False)
            return 0
        settings = load_settings()
    except ValidationError:
        console.print("Virhe: konfiguraation rakenne tai ympäristöasetukset ovat virheellisiä.", style="red")
        return 2
    except (OSError, UnicodeError):
        console.print("Virhe: competitors.json-tiedostoa ei voitu lukea.", style="red")
        return 2
    except ValueError as exc:
        # Only locally generated configuration errors are exposed.
        console.print(str(exc), style="red", markup=False)
        return 2

    scraper = CompetitorScraper(settings.firecrawl_api_key.get_secret_value(), timeout_ms=int(settings.timeout_seconds * 1000))
    analyzer = RadarAnalyzer(
        settings.gemini_api_key.get_secret_value(), model=settings.gemini_model,
        timeout_seconds=settings.timeout_seconds,
        max_input_chars=settings.max_input_chars,
    )
    entries: list[ReportEntry] = []
    try:
        for company, url in targets:
            console.print(f"Haetaan: {company.name} — {url}", markup=False)
            result = scraper.scrape_page(url)
            entry = ReportEntry(company_name=company.name, url=url)
            if not result["success"]:
                entry.error = f"Sivun haku tai historiatallennus epäonnistui ({result['error']})."
            else:
                try:
                    if result["markdown"] == result["previous_markdown"]:
                        entry.insight = CompetitorInsight(
                            company_name=company.name, has_critical_changes=False,
                            pricing_changes=[], feature_updates=[], summary="Sivun sisältö ei ole muuttunut.",
                        )
                    else:
                        entry.insight = analyzer.analyze_diff(
                            result["markdown"], result["previous_markdown"], company.focus_areas,
                        )
                except AnalysisError as exc:
                    entry.error = str(exc)
                    # Failed analysis must not advance the comparison baseline.
                    try:
                        Path(result["snapshot_path"]).unlink(missing_ok=True)
                    except OSError:
                        entry.error += " Epäonnistuneen analyysin tilannekuvaa ei voitu poistaa."
            entries.append(entry)
    finally:
        analyzer.close()
    render_report(entries, console=console)
    try:
        path = save_report(entries)
    except OSError:
        console.print("Virhe: raportin tallentaminen epäonnistui.", style="red")
        return 1
    console.print(f"Raportti: {path}", markup=False)
    return 1 if any(entry.error for entry in entries) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
