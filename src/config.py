"""Validated configuration; environment values override the local .env file."""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

ROOT = Path(__file__).resolve().parent.parent


class Competitor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    urls: list[HttpUrl] = Field(min_length=1)
    focus_areas: list[str] = Field(default_factory=list)


class RadarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    competitors: list[Competitor] = Field(min_length=1)


class Settings(BaseModel):
    firecrawl_api_key: SecretStr
    gemini_api_key: SecretStr
    gemini_model: str = Field(default="gemini-flash-latest", min_length=1)
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_input_chars: int = Field(default=60000, ge=1000, le=100000)


def load_config(path: Path = ROOT / "competitors.json") -> RadarConfig:
    return RadarConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env", override=False)
    firecrawl_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not firecrawl_key:
        raise ValueError(
            "Virhe: FIRECRAWL_API_KEY puuttuu! Hanki avain (10% alennus ensimmäisestä ostosta): "
            "https://firecrawl.link/jami-harju"
        )
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not gemini_key:
        raise ValueError("Virhe: GEMINI_API_KEY puuttuu! Aseta se ympäristöön tai .env-tiedostoon.")
    return Settings(
        firecrawl_api_key=SecretStr(firecrawl_key),
        gemini_api_key=SecretStr(gemini_key),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip(),
        timeout_seconds=os.getenv("RADAR_TIMEOUT_SECONDS", "60"),
        max_input_chars=os.getenv("RADAR_MAX_INPUT_CHARS", "60000"),
    )
