"""Offline integration tests: no credentials or network access required."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rich.console import Console

import main
from src.analyzer import AnalysisError, CompetitorInsight, RadarAnalyzer
from src.config import RadarConfig, Settings
from src.reporter import ReportEntry, render_report, save_report
from src.scraper import CompetitorScraper, get_previous_snapshot


def insight() -> CompetitorInsight:
    return CompetitorInsight(company_name="Example", has_critical_changes=False,
                             pricing_changes=["10 EUR/kk"], feature_updates=["API"], summary="Yrityksille")


class PipelineTests(unittest.TestCase):
    def test_analyzer_validates_response_and_sets_first_run_flag(self) -> None:
        payload = insight().model_dump()
        payload["has_critical_changes"] = True
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(payload)))
        ])
        with patch("src.analyzer.OpenAI", return_value=client):
            analyzer = RadarAnalyzer("test")
            self.assertFalse(analyzer.analyze_diff("10 EUR/kk", None, []).has_critical_changes)
            self.assertTrue(analyzer.analyze_diff("10 EUR/kk", "5 EUR/kk", []).has_critical_changes)
            client.chat.completions.create.return_value.choices[0].message.content = '{"bad": true}'
            with self.assertRaises(AnalysisError):
                analyzer.analyze_diff("text", None, [])
            with self.assertRaises(AnalysisError):
                analyzer.analyze_diff("", None, [])
            with self.assertRaises(AnalysisError):
                analyzer.analyze_diff("x" * 60001, None, [])

    def test_snapshots_isolate_pages_and_contain_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("src.scraper.HISTORY_DIR", Path(tmp)), patch("src.scraper.V1FirecrawlApp") as factory:
            factory.return_value.scrape_url.return_value = {"markdown": "old"}
            scraper = CompetitorScraper("test")
            self.assertTrue(scraper.scrape_page("https://example.com/pricing")["success"])
            factory.return_value.scrape_url.return_value = {"markdown": "new"}
            result = scraper.scrape_page("https://example.com/pricing")
            self.assertEqual(result["previous_markdown"], "old")
            self.assertEqual(get_previous_snapshot("https://example.com/pricing"), "new")
            self.assertIsNone(get_previous_snapshot("example.com"))
            factory.return_value.scrape_url.side_effect = TimeoutError()
            self.assertFalse(scraper.scrape_page("https://example.com/pricing")["success"])
            self.assertEqual(len(list(Path(tmp).glob("*.md"))), 2)

    def test_report_writes_date_latest_and_literal_rich_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entries = [ReportEntry(company_name="[red]Example", url="https://example.com", insight=insight()),
                       ReportEntry(company_name="Failed", url="https://example.org", error="Timeout")]
            path = save_report(entries, directory=Path(tmp))
            self.assertTrue(path.name.startswith("report_"))
            self.assertEqual(path.read_text(), (Path(tmp) / "latest_report.md").read_text())
            self.assertIn("Timeout", path.read_text())
            stream = io.StringIO()
            render_report(entries, console=Console(file=stream, width=180, color_system=None))
            self.assertIn("[red]Example", stream.getvalue())

    def test_cli_full_pipeline_and_failed_analysis_baseline_rollback(self) -> None:
        config = RadarConfig.model_validate({"competitors": [{
            "name": "Example", "urls": ["https://example.com/pricing"], "focus_areas": []
        }]})
        settings = Settings(firecrawl_api_key="test", openai_api_key="test")
        with tempfile.TemporaryDirectory() as tmp, patch("main.load_config", return_value=config), patch("main.load_settings", return_value=settings), patch("src.scraper.HISTORY_DIR", Path(tmp) / "history"), patch("src.scraper.V1FirecrawlApp") as firecrawl, patch("main.RadarAnalyzer") as analyzer, patch("main.save_report", side_effect=lambda entries: save_report(entries, directory=Path(tmp) / "reports")):
            firecrawl.return_value.scrape_url.return_value = {"markdown": "old"}
            analyzer.return_value.analyze_diff.return_value = insight()
            self.assertEqual(main.main([]), 0)
            self.assertEqual(main.main([]), 0)
            analyzer.return_value.analyze_diff.assert_called_once()
            firecrawl.return_value.scrape_url.return_value = {"markdown": "new"}
            analyzer.return_value.analyze_diff.side_effect = AnalysisError("Rate limit")
            self.assertEqual(main.main([]), 1)
            self.assertEqual(get_previous_snapshot("https://example.com/pricing"), "old")
            self.assertIn("Rate limit", (Path(tmp) / "reports/latest_report.md").read_text())
            self.assertEqual(main.main(["--dry-run", "--target", "https://missing.com"]), 2)


if __name__ == "__main__":
    unittest.main()
