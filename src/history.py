from __future__ import annotations

import json
import logging
import datetime as dt
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryEntry:
    song_name: str
    artist: str
    album: str
    date_added: str


class SongHistory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]\n", encoding="utf-8")
        self.entries = self._load()
        self._seen_keys = {self.key(entry.artist, entry.song_name) for entry in self.entries}

    @staticmethod
    def key(artist: str, song_name: str) -> str:
        return f"{normalize(artist)}::{normalize(song_name)}"

    def contains(self, artist: str, song_name: str) -> bool:
        return self.key(artist, song_name) in self._seen_keys

    def contains_recent(self, artist: str, song_name: str, playlist_date: str, days: int = 30) -> bool:
        return self.key(artist, song_name) in self.recent_song_keys(playlist_date, days)

    def recent_song_keys(self, playlist_date: str, days: int = 30) -> set[str]:
        end_date = dt.date.fromisoformat(playlist_date)
        start_date = end_date - dt.timedelta(days=days - 1)
        keys: set[str] = set()
        for entry in self.entries:
            added_on = parse_date(entry.date_added)
            if added_on and start_date <= added_on <= end_date:
                keys.add(self.key(entry.artist, entry.song_name))
        return keys

    def add_many(self, songs: list[HistoryEntry]) -> None:
        for song in songs:
            key = self.key(song.artist, song.song_name)
            if key in self._seen_keys:
                continue
            self.entries.append(song)
            self._seen_keys.add(key)

    def save(self) -> None:
        payload = [asdict(entry) for entry in self.entries]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        LOGGER.info("Saved %s history entries to %s", len(payload), self.path)

    def recent_summary(self, limit: int = 120) -> str:
        recent = self.entries[-limit:]
        return "\n".join(f"- {entry.artist} - {entry.song_name} ({entry.album})" for entry in recent)

    def _load(self) -> list[HistoryEntry]:
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid history JSON at {self.path}") from exc
        if not isinstance(raw, list):
            raise ValueError(f"History file must contain a JSON list: {self.path}")
        entries: list[HistoryEntry] = []
        for item in raw:
            if not isinstance(item, dict):
                LOGGER.warning("Skipping malformed history item: %r", item)
                continue
            entries.append(
                HistoryEntry(
                    song_name=str(item.get("song_name", "")).strip(),
                    artist=str(item.get("artist", "")).strip(),
                    album=str(item.get("album", "")).strip(),
                    date_added=str(item.get("date_added", "")).strip(),
                )
            )
        return [entry for entry in entries if entry.song_name and entry.artist]


def normalize(value: str) -> str:
    return " ".join(value.lower().strip().split())


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        LOGGER.warning("Skipping history entry with invalid date: %s", value)
        return None
