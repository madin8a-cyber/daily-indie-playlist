from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from src.history import SongHistory
from src.music_api import SongQuery

LOGGER = logging.getLogger(__name__)

CURATOR_PROMPT = (
    "Create a high quality indie playlist focused on indie pop, indie rock and alternative music. "
    "Avoid repeating previous songs. Balance new releases and classics."
)


class AICurator:
    def __init__(self, api_key: str, model: str | None = None) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-5-mini")

    def generate_candidates(self, playlist_date: str, history: SongHistory, count: int = 100) -> list[SongQuery]:
        LOGGER.info("Generating %s AI curator candidates with OpenAI", count)
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert independent music curator. "
                        "Return only real released songs with artist names likely to resolve in Apple Music/iTunes."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"{CURATOR_PROMPT}\n\n"
                        f"Playlist date: {playlist_date}\n"
                        f"Generate exactly {count} candidate songs.\n"
                        "Category mix must be exactly: 60 primary, 20 recent, 20 classic.\n"
                        "primary means indie pop, indie rock, or alternative.\n"
                        "recent means recent acclaimed or high-rated new releases.\n"
                        "classic means classic alternative or indie works.\n"
                        "Do not include the same artist more than twice in the candidate list.\n"
                        "Avoid these previous songs, especially the recent entries:\n"
                        f"{history.recent_summary(limit=200)}"
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "daily_indie_candidates",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "candidates": {
                                "type": "array",
                                "minItems": count,
                                "maxItems": count,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "song_name": {"type": "string"},
                                        "artist": {"type": "string"},
                                        "genre": {"type": "string"},
                                        "reason": {"type": "string"},
                                        "bucket": {"type": "string", "enum": ["primary", "recent", "classic"]},
                                    },
                                    "required": ["song_name", "artist", "genre", "reason", "bucket"],
                                },
                            }
                        },
                        "required": ["candidates"],
                    },
                }
            },
        }
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
            response.raise_for_status()
            raw = json.loads(self._extract_response_text(response.json()))
            return self._parse(raw.get("candidates", []))
        except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"OpenAI curator candidate generation failed: {exc}") from exc

    def _extract_response_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return str(payload["output_text"])
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and "text" in content:
                    return str(content["text"])
        raise ValueError("OpenAI response did not include output text")

    def _parse(self, raw_items: Any) -> list[SongQuery]:
        if not isinstance(raw_items, list):
            raise ValueError("candidates must be a list")
        candidates: list[SongQuery] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            song_name = str(item.get("song_name", "")).strip()
            artist = str(item.get("artist", "")).strip()
            bucket = str(item.get("bucket", "")).strip()
            if not song_name or not artist or bucket not in {"primary", "recent", "classic"}:
                continue
            candidates.append(
                SongQuery(
                    song_name=song_name,
                    artist=artist,
                    genre=str(item.get("genre", "Alternative / Indie")).strip(),
                    reason=str(item.get("reason", "AI curator recommendation.")).strip(),
                    bucket=bucket,
                )
            )
        if len(candidates) < 80:
            raise ValueError(f"Only parsed {len(candidates)} usable candidates")
        return candidates
