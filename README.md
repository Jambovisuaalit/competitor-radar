# competitor-radar

Python 3.12 CLI for weekly competitor website monitoring with Firecrawl and
OpenAI `gpt-4o-mini`. Source pages are stored as Markdown, compared per URL,
and summarized in Finnish. First runs describe the current offering.

## Local setup

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `FIRECRAWL_API_KEY` and `OPENAI_API_KEY` in `.env` or your environment.
Environment values take precedence. Never commit credentials.
The scraper uses the SDK's `V1FirecrawlApp` export, tested with
`firecrawl-py==4.54.0`; install the latest SDK when starting from an old environment.

Edit `competitors.json`: each competitor has `name`, a nonempty `urls` list,
and optional `focus_areas`. URLs are compared separately, including pricing pages.

```sh
python main.py --dry-run
python main.py
python main.py --target https://linear.app/pricing
python -m unittest discover -s tests -v
```

`--dry-run` validates and lists selected targets without keys, network calls or
output writes. `--target` must match a configured URL. Normal execution writes
`reports/report_YYYY-MM-DD.md` (UTC date) and `reports/latest_report.md`.
Multiple runs on the same UTC day replace that day's report. History lives in
`history/`; filenames combine domain, page URL hash, UTC timestamp and unique ID.
Identical pages skip the LLM call. Failed analyses roll back their new snapshot
so the next run retries the comparison. Individual target failures appear in
the report and do not prevent processing the remaining targets.

Exit codes: `0` success, `1` target/report failure, `2` configuration error,
`130` interrupted. Empty or oversized input produces an explicit failure;
documents are never silently truncated. `RADAR_TIMEOUT_SECONDS` controls the
request timeout; `RADAR_MAX_INPUT_CHARS` limits the combined analysis input.
LLM responses are validated with strict Pydantic types; factual accuracy still
requires human review before using findings for business decisions.

## GitHub Actions

Commit this project to a GitHub repository and add repository Actions secrets
`FIRECRAWL_API_KEY` and `OPENAI_API_KEY`. The workflow runs Mondays at 08:00 UTC
and supports **Run workflow**. Scheduled workflows must exist on the default
branch; GitHub can delay scheduled starts.

Each run uploads its Markdown reports and history as artifacts, retained for
90 days. Before scraping, it restores the latest unexpired history artifact
from the same branch. Partial failures still upload reports and surviving
history. No previous artifact means a new baseline. Repository permissions are
read-only; reports are not committed or pushed. An Actions run has not been
triggered by creating the local workflow file.

## API references

- [Firecrawl v1 Python client](https://github.com/firecrawl/firecrawl/blob/main/apps/python-sdk/firecrawl/v1/client.py)
- [OpenAI structured output and JSON mode](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat)
- [GitHub artifact endpoints](https://docs.github.com/en/rest/actions/artifacts)

The JSON mode request works with the requested OpenAI SDK minimum version;
Pydantic validates the returned object before it reaches the reporter.
