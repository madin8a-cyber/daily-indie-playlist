from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SongCandidate:
    song_name: str
    artist: str
    album: str
    genre: str
    apple_music_url: str
    reason: str
    source: str
    bucket: str = "primary"
    track_id: int | None = None
    collection_id: int | None = None
    track_view_url: str = ""
    collection_view_url: str = ""


@dataclass(frozen=True)
class SongQuery:
    song_name: str
    artist: str
    genre: str
    reason: str
    bucket: str = "primary"


class ITunesRateLimitError(RuntimeError):
    """Raised when iTunes keeps returning 429 after all retries."""


class HttpClient:
    RETRY_DELAYS = (2, 5, 10)

    def __init__(self, min_interval: float = 0.8, max_interval: float = 1.5) -> None:
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "daily-indie-playlist/1.0 (https://github.com/)",
                "Accept": "application/json",
            }
        )

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(len(self.RETRY_DELAYS) + 1):
            time.sleep(random.uniform(self.min_interval, self.max_interval))
            response = self.session.get(url, timeout=25, **kwargs)
            if response.status_code == 429 and attempt < len(self.RETRY_DELAYS):
                sleep_seconds = self.RETRY_DELAYS[attempt]
                LOGGER.warning("[iTunes] Rate limited, retry: attempt %s", attempt + 1)
                time.sleep(sleep_seconds)
                continue
            if response.status_code == 429:
                LOGGER.error("[iTunes] Failed after retries: HTTP 429")
                raise ITunesRateLimitError("iTunes Search API returned HTTP 429 after retries")
            if response.status_code in {500, 502, 503, 504} and attempt < len(self.RETRY_DELAYS):
                sleep_seconds = self.RETRY_DELAYS[attempt]
                LOGGER.warning("Retrying %s after HTTP %s in %s seconds", url, response.status_code, sleep_seconds)
                time.sleep(sleep_seconds)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"Failed to fetch {url}")


class ITunesSearchClient:
    def __init__(
        self,
        country: str = "US",
        http: HttpClient | None = None,
        daily_request_limit: int = 50,
        cache_path: Path | str = Path("data/itunes_cache.json"),
    ) -> None:
        self.country = country
        self.http = http or HttpClient()
        self.daily_request_limit = daily_request_limit
        self.request_count = 0
        self._search_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.cache_path = Path(cache_path)
        self._song_cache = self._load_song_cache()

    def find_song(self, query: SongQuery) -> SongCandidate | None:
        cached = self._song_cache_get(query.artist, query.song_name)
        if cached:
            LOGGER.info("[iTunes] Cache hit:\n%s - %s", query.artist, query.song_name)
            return self._to_candidate(cached, query)
        terms = [
            f"{query.artist} {query.song_name}",
            f"{query.song_name} {query.artist}",
            query.artist,
        ]
        for term in terms:
            result = self._search(term, limit=10)
            match = self._best_match(result, query)
            if match:
                self._song_cache_set(query.artist, query.song_name, match)
                return self._to_candidate(match, query)
        LOGGER.info("No iTunes match for %s - %s", query.artist, query.song_name)
        return None

    def search_artist_tracks(self, artist: str, genre: str, reason: str, limit: int = 5) -> list[SongCandidate]:
        payload = self._search(artist, limit=limit)
        songs: list[SongCandidate] = []
        for item in payload:
            songs.append(
                self._to_candidate(
                    item,
                    SongQuery(
                        song_name=str(item.get("trackName", "")),
                        artist=str(item.get("artistName", artist)),
                        genre=genre,
                        reason=reason,
                        bucket=bucket_from_genre(genre),
                    ),
                )
            )
        return [song for song in songs if song.song_name and song.artist and song.apple_music_url]

    def search_term_tracks(self, term: str, genre: str, reason: str, limit: int = 10) -> list[SongCandidate]:
        return [
            self._to_candidate(
                item,
                SongQuery(
                    song_name=str(item.get("trackName", "")),
                    artist=str(item.get("artistName", "")),
                    genre=genre,
                    reason=reason,
                    bucket=bucket_from_genre(genre),
                ),
            )
            for item in self._search(term, limit=limit)
        ]

    def apple_music_search_url(self, artist: str, song_name: str) -> str:
        return f"https://music.apple.com/search?term={quote_plus(f'{artist} {song_name}')}"

    def apple_music_song_url(self, song_name: str, track_id: int) -> str:
        country = self.country.lower()
        return f"https://music.apple.com/{country}/song/{slugify(song_name)}/{track_id}"

    def _search(self, term: str, limit: int) -> list[dict[str, Any]]:
        cache_key = (normalize(term), limit)
        if cache_key in self._search_cache:
            LOGGER.info("[iTunes] Cache hit:\n%s", term)
            return self._search_cache[cache_key]
        if self.request_count >= self.daily_request_limit:
            LOGGER.warning(
                "[iTunes] daily request limit reached (%s); skipping new search: %s",
                self.daily_request_limit,
                term,
            )
            return []
        LOGGER.info("[iTunes] Searching:\n%s", term)
        url = "https://itunes.apple.com/search"
        params = {
            "term": term,
            "media": "music",
            "entity": "song",
            "country": self.country,
            "limit": limit,
        }
        self.request_count += 1
        try:
            payload = self.http.get_json(url, params=params)
        except ITunesRateLimitError:
            LOGGER.error("[iTunes] Failed after retries: %s", term)
            self._search_cache[cache_key] = []
            return []
        results = payload.get("results", [])
        songs = [item for item in results if isinstance(item, dict)]
        self._search_cache[cache_key] = songs
        return songs

    def _song_cache_get(self, artist: str, song_name: str) -> dict[str, Any] | None:
        if not song_name:
            return None
        cached = self._song_cache.get(song_cache_key(artist, song_name))
        if not isinstance(cached, dict):
            return None
        return cached

    def _song_cache_set(self, artist: str, song_name: str, item: dict[str, Any]) -> None:
        if not artist or not song_name:
            return
        cache_item = {
            "trackId": optional_int(item.get("trackId")),
            "collectionId": optional_int(item.get("collectionId")),
            "trackName": str(item.get("trackName") or song_name).strip(),
            "artistName": str(item.get("artistName") or artist).strip(),
            "collectionName": str(item.get("collectionName") or "Unknown Album").strip(),
            "trackViewUrl": str(item.get("trackViewUrl", "")).strip(),
            "collectionViewUrl": str(item.get("collectionViewUrl", "")).strip(),
        }
        self._song_cache[song_cache_key(artist, song_name)] = cache_item
        self._save_song_cache()

    def _load_song_cache(self) -> dict[str, dict[str, Any]]:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.cache_path.exists():
            self.cache_path.write_text("{}\n", encoding="utf-8")
            return {}
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("Invalid iTunes cache JSON at %s; starting with empty cache", self.cache_path)
            return {}
        if not isinstance(payload, dict):
            LOGGER.warning("Invalid iTunes cache shape at %s; starting with empty cache", self.cache_path)
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _save_song_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._song_cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _best_match(self, results: list[dict[str, Any]], query: SongQuery) -> dict[str, Any] | None:
        artist_key = normalize(query.artist)
        song_key = normalize(query.song_name)
        for item in results:
            if normalize(str(item.get("artistName", ""))) == artist_key and normalize(str(item.get("trackName", ""))) == song_key:
                return item
        for item in results:
            item_artist = normalize(str(item.get("artistName", "")))
            item_song = normalize(str(item.get("trackName", "")))
            if artist_key in item_artist or item_artist in artist_key:
                if song_key in item_song or item_song in song_key:
                    return item
        return results[0] if results else None

    def _to_candidate(self, item: dict[str, Any], query: SongQuery) -> SongCandidate:
        song_name = str(item.get("trackName") or query.song_name).strip()
        artist = str(item.get("artistName") or query.artist).strip()
        track_id = optional_int(item.get("trackId"))
        collection_id = optional_int(item.get("collectionId"))
        track_view_url = str(item.get("trackViewUrl", "")).strip()
        collection_view_url = str(item.get("collectionViewUrl", "")).strip()
        url = self._apple_music_url(
            song_name=song_name,
            artist=artist,
            track_id=track_id,
            track_view_url=track_view_url,
        )
        return SongCandidate(
            song_name=song_name,
            artist=artist,
            album=str(item.get("collectionName") or "Unknown Album").strip(),
            genre=query.genre,
            apple_music_url=url,
            reason=query.reason,
            source="iTunes Search API",
            bucket=query.bucket,
            track_id=track_id,
            collection_id=collection_id,
            track_view_url=track_view_url,
            collection_view_url=collection_view_url,
        )

    def _apple_music_url(
        self,
        song_name: str,
        artist: str,
        track_id: int | None,
        track_view_url: str,
    ) -> str:
        if track_id is not None:
            return self.apple_music_song_url(song_name, track_id)
        if track_view_url:
            return track_view_url
        return self.apple_music_search_url(artist, song_name)


class MusicBrainzClient:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient()

    def search_recordings(self, term: str, limit: int = 10) -> list[SongQuery]:
        url = "https://musicbrainz.org/ws/2/recording/"
        payload = self.http.get_json(url, params={"query": term, "fmt": "json", "limit": limit})
        recordings = payload.get("recordings", [])
        queries: list[SongQuery] = []
        for item in recordings:
            title = str(item.get("title", "")).strip()
            artist_credit = item.get("artist-credit", [])
            artist = ""
            if artist_credit and isinstance(artist_credit[0], dict):
                artist = str(artist_credit[0].get("name", "")).strip()
            if title and artist:
                queries.append(
                    SongQuery(
                        song_name=title,
                        artist=artist,
                        genre="Alternative / Indie",
                        reason="Discovered through MusicBrainz metadata search.",
                        bucket="classic",
                    )
                )
        return queries


class LastFmClient:
    def __init__(self, api_key: str, http: HttpClient | None = None) -> None:
        self.api_key = api_key
        self.http = http or HttpClient()

    def top_tracks_by_tag(self, tag: str, limit: int = 10) -> list[SongQuery]:
        payload = self.http.get_json(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "tag.gettoptracks",
                "tag": tag,
                "api_key": self.api_key,
                "format": "json",
                "limit": limit,
            },
        )
        tracks = payload.get("tracks", {}).get("track", [])
        queries: list[SongQuery] = []
        for item in tracks:
            artist = item.get("artist", {})
            queries.append(
                SongQuery(
                    song_name=str(item.get("name", "")).strip(),
                    artist=str(artist.get("name", "") if isinstance(artist, dict) else artist).strip(),
                    genre=tag.title(),
                    reason=f"High-scrobble Last.fm track tagged {tag}.",
                    bucket="recent",
                )
            )
        return [query for query in queries if query.song_name and query.artist]


def normalize(value: str) -> str:
    return " ".join(value.lower().strip().split())


def song_cache_key(artist: str, song_name: str) -> str:
    return f"{normalize(artist)}|{normalize(song_name)}"


def slugify(value: str) -> str:
    normalized = normalize(value)
    chars: list[str] = []
    previous_dash = False
    for char in normalized:
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-") or "song"


def optional_int(value: Any) -> int | None:
    try:
        if value in {None, ""}:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def bucket_from_genre(genre: str) -> str:
    genre_key = normalize(genre)
    if "classic" in genre_key:
        return "classic"
    if "new" in genre_key or "recent" in genre_key:
        return "recent"
    return "primary"


class AppleMusicPlaylistClient:
    """Future integration point for creating real Apple Music library playlists."""

    def __init__(self, developer_token: str, user_token: str, storefront: str = "us") -> None:
        self.developer_token = developer_token
        self.user_token = user_token
        self.storefront = storefront

    def create_playlist(self, name: str, song_ids: list[str]) -> str:
        raise NotImplementedError(
            "Apple Music playlist creation is reserved for phase two. "
            "Add APPLE_MUSIC_DEVELOPER_TOKEN and APPLE_MUSIC_USER_TOKEN support here."
        )
