# Changelog

## 0.1.0 — 2026-10-26

- Monitor configured competitor websites and pricing pages with Firecrawl.
- Compare page-specific Markdown snapshots stored locally.
- Analyze initial offerings and subsequent changes with Google Gemini.
- Validate model responses with strict Pydantic types.
- Print Rich reports and write dated and latest Markdown reports.
- Schedule weekly GitHub Actions runs with history and report artifacts.
- Support `--dry-run` and selection of a configured URL with `--target`.
- Provide automated offline tests and a downloadable source ZIP with SHA-256 checksum.

This initial release is distributed through GitHub Releases. It requires
Python 3.12, installation of `requirements.txt`, and the user's own Firecrawl
and Gemini API keys. API usage may incur provider charges.
