from __future__ import annotations

import logging
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SongQuery:
    song_name: str
    artist: str
    genre: str
    reason: str


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "daily-indie-playlist/1.0 (https://github.com/)",
                "Accept": "application/json",
            }
        )

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(4):
            response = self.session.get(url, timeout=25, **kwargs)
            if response.status_code in {429, 500, 502, 503, 504} and attempt < 3:
                sleep_seconds = 2**attempt
                LOGGER.warning("Retrying %s after HTTP %s", url, response.status_code)
                time.sleep(sleep_seconds)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"Failed to fetch {url}")


class ITunesSearchClient:
    def __init__(self, country: str = "US", http: HttpClient | None = None) -> None:
        self.country = country
        self.http = http or HttpClient()

    def find_song(self, query: SongQuery) -> SongCandidate | None:
        terms = [
            f"{query.artist} {query.song_name}",
            f"{query.song_name} {query.artist}",
            query.artist,
        ]
        for term in terms:
            result = self._search(term, limit=10)
            match = self._best_match(result, query)
            if match:
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
                ),
            )
            for item in self._search(term, limit=limit)
        ]

    def apple_music_search_url(self, artist: str, song_name: str) -> str:
        return f"https://music.apple.com/search?term={quote_plus(f'{artist} {song_name}')}"

    def _search(self, term: str, limit: int) -> list[dict[str, Any]]:
        url = "https://itunes.apple.com/search"
        params = {
            "term": term,
            "media": "music",
            "entity": "song",
            "country": self.country,
            "limit": limit,
        }
        payload = self.http.get_json(url, params=params)
        results = payload.get("results", [])
        return [item for item in results if isinstance(item, dict)]

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
        url = str(item.get("trackViewUrl", "")).strip()
        if not url:
            url = self.apple_music_search_url(query.artist, query.song_name)
        return SongCandidate(
            song_name=str(item.get("trackName") or query.song_name).strip(),
            artist=str(item.get("artistName") or query.artist).strip(),
            album=str(item.get("collectionName") or "Unknown Album").strip(),
            genre=query.genre,
            apple_music_url=url,
            reason=query.reason,
            source="iTunes Search API",
        )


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
                )
            )
        return [query for query in queries if query.song_name and query.artist]


def normalize(value: str) -> str:
    return " ".join(value.lower().strip().split())


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
