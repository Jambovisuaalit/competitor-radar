"""Offline integration tests: no credentials or network access required."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from google import genai
from google.genai import errors, types
from rich.console import Console

import main
from src.analyzer import AnalysisError, CompetitorInsight, RadarAnalyzer
from src.config import RadarConfig, Settings, load_settings
from src.reporter import ReportEntry, render_report, save_report
from src.scraper import CompetitorScraper, get_previous_snapshot


def insight() -> CompetitorInsight:
    return CompetitorInsight(company_name="Example", has_critical_changes=False,
                             pricing_changes=["10 EUR/kk"], feature_updates=["API"], summary="Yrityksille")


def gemini_response(text: str, finish_reason: types.FinishReason = types.FinishReason.STOP) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=finish_reason, content=types.Content(parts=[types.Part(text=text)]),
    )])


class PipelineTests(unittest.TestCase):
    def test_real_sdk_serializes_schema_and_parses_response_offline(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
            self.assertEqual(
                body["generationConfig"]["responseJsonSchema"], CompetitorInsight.model_json_schema(),
            )
            self.assertIn("gemini-flash-latest:generateContent", request.url.path)
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [{"text": insight().model_dump_json()}]},
                "finishReason": "STOP",
            }]})

        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            client = genai.Client(api_key="test", http_options=types.HttpOptions(httpx_client=transport))
            with patch("src.analyzer.genai.Client", return_value=client):
                analyzer = RadarAnalyzer("test")
                try:
                    self.assertEqual(analyzer.analyze_diff("10 EUR/kk", None, []), insight())
                finally:
                    analyzer.close()

    def test_analyzer_validates_response_and_sets_first_run_flag(self) -> None:
        payload = insight().model_dump()
        payload["has_critical_changes"] = True
        client = Mock()
        client.models.generate_content.return_value = gemini_response(json.dumps(payload))
        with patch("src.analyzer.genai.Client", return_value=client) as factory:
            analyzer = RadarAnalyzer("test", model="gemini-flash-latest", timeout_seconds=12)
            self.assertFalse(analyzer.analyze_diff("10 EUR/kk", None, []).has_critical_changes)
            self.assertTrue(analyzer.analyze_diff("10 EUR/kk", "5 EUR/kk", []).has_critical_changes)
            self.assertEqual(factory.call_args.kwargs["http_options"].timeout, 12000)
            request = client.models.generate_content.call_args.kwargs
            self.assertEqual(request["model"], "gemini-flash-latest")
            self.assertEqual(request["config"].response_json_schema, CompetitorInsight.model_json_schema())
            self.assertEqual(json.loads(request["contents"])["previous_markdown"], "5 EUR/kk")
            client.models.generate_content.return_value = gemini_response('{"bad": true}')
            with self.assertRaises(AnalysisError):
                analyzer.analyze_diff("text", None, [])
            with self.assertRaises(AnalysisError):
                analyzer.analyze_diff("", None, [])
            with self.assertRaises(AnalysisError):
                analyzer.analyze_diff("x" * 60001, None, [])
            analyzer.close()
            client.close.assert_called_once()

    def test_gemini_rejects_empty_blocked_and_truncated_responses(self) -> None:
        client = Mock()
        with patch("src.analyzer.genai.Client", return_value=client):
            analyzer = RadarAnalyzer("test")
            for response in (
                types.GenerateContentResponse(),
                gemini_response("", types.FinishReason.STOP),
                gemini_response(insight().model_dump_json(), types.FinishReason.MAX_TOKENS),
                gemini_response(insight().model_dump_json(), types.FinishReason.SAFETY),
                gemini_response('{"company_name": 123}'),
            ):
                with self.subTest(response=response):
                    client.models.generate_content.return_value = response
                    with self.assertRaises(AnalysisError):
                        analyzer.analyze_diff("text", None, [])

    def test_gemini_api_and_transport_errors_are_safe(self) -> None:
        client = Mock()
        with patch("src.analyzer.genai.Client", return_value=client):
            analyzer = RadarAnalyzer("test")
            for error in (
                errors.APIError(429, {"error": {"message": "secret-key", "status": "RESOURCE_EXHAUSTED"}}),
                httpx.ReadTimeout("secret-key"),
                httpx.ConnectError("secret-key"),
            ):
                client.models.generate_content.side_effect = error
                with self.assertRaises(AnalysisError) as captured:
                    analyzer.analyze_diff("text", None, [])
                self.assertNotIn("secret-key", str(captured.exception))

    def test_settings_require_gemini_key_and_allow_model_override(self) -> None:
        with patch("src.config.load_dotenv"), patch.dict(os.environ, {
            "FIRECRAWL_API_KEY": "test-firecrawl", "GEMINI_API_KEY": "test-gemini",
            "GEMINI_MODEL": "gemini-flash-latest",
        }, clear=True):
            settings = load_settings()
            self.assertEqual(settings.gemini_api_key.get_secret_value(), "test-gemini")
            self.assertEqual(settings.gemini_model, "gemini-flash-latest")
            del os.environ["GEMINI_API_KEY"]
            with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY puuttuu"):
                load_settings()

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
        settings = Settings(firecrawl_api_key="test", gemini_api_key="test")
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
