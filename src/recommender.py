from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Any

import requests

from src.ai_curator import AICurator
from src.history import SongHistory
from src.history import normalize as normalize_history
from src.music_api import ITunesSearchClient, LastFmClient, MusicBrainzClient, SongCandidate, SongQuery

LOGGER = logging.getLogger(__name__)

OPENAI_PROMPT = (
    "Create a high quality indie playlist focused on indie pop, indie rock and alternative music. "
    "Avoid repeating previous songs. Balance new releases and classics."
)

PRIMARY_ARTISTS = [
    "Alvvays",
    "Beach House",
    "The Strokes",
    "Vampire Weekend",
    "Tame Impala",
    "Mitski",
    "Phoebe Bridgers",
    "Japanese Breakfast",
    "Soccer Mommy",
    "Snail Mail",
    "Big Thief",
    "The National",
    "Arcade Fire",
    "Interpol",
    "Yeah Yeah Yeahs",
    "Fleet Foxes",
    "Broken Social Scene",
    "The xx",
    "Clairo",
    "Wet Leg",
    "Wolf Alice",
    "Fontaines D.C.",
    "The Beths",
    "Men I Trust",
    "Real Estate",
]

NEW_RELEASE_ARTISTS = [
    "boygenius",
    "beabadoobee",
    "Samia",
    "Bar Italia",
    "Wednesday",
    "MJ Lenderman",
    "Horsegirl",
    "The Last Dinner Party",
    "Blondshell",
    "Caroline Polachek",
]

CLASSIC_ARTISTS = [
    "Radiohead",
    "R.E.M.",
    "Pixies",
    "The Cure",
    "The Smiths",
    "Sonic Youth",
    "Pavement",
    "Yo La Tengo",
    "Modest Mouse",
    "Neutral Milk Hotel",
    "Belle and Sebastian",
    "Elliott Smith",
]


@dataclass(frozen=True)
class PlaylistPlan:
    primary: list[SongQuery]
    new_releases: list[SongQuery]
    classics: list[SongQuery]


class Recommender:
    def __init__(
        self,
        itunes: ITunesSearchClient,
        history: SongHistory,
        lastfm: LastFmClient | None = None,
        musicbrainz: MusicBrainzClient | None = None,
        openai_api_key: str | None = None,
    ) -> None:
        self.itunes = itunes
        self.history = history
        self.lastfm = lastfm
        self.musicbrainz = musicbrainz
        self.openai_api_key = openai_api_key
        self._song_lookup_cache: dict[str, SongCandidate | None] = {}
        self._artist_tracks_cache: dict[tuple[str, str, int], list[SongCandidate]] = {}

    def generate(self, playlist_date: str, seed: int, total: int = 20) -> list[SongCandidate]:
        random.seed(seed)
        candidates = self._candidate_queries(playlist_date)
        selected_keys: set[str] = set()
        selected_artists: set[str] = set()
        primary = self._resolve_bucket(
            candidates,
            bucket="primary",
            target=12,
            playlist_date=playlist_date,
            selected_keys=selected_keys,
            selected_artists=selected_artists,
        )
        new_releases = self._resolve_bucket(
            candidates,
            bucket="recent",
            target=4,
            playlist_date=playlist_date,
            selected_keys=selected_keys,
            selected_artists=selected_artists,
        )
        classics = self._resolve_bucket(
            candidates,
            bucket="classic",
            target=4,
            playlist_date=playlist_date,
            selected_keys=selected_keys,
            selected_artists=selected_artists,
        )

        selected = primary + new_releases + classics
        if len(selected) < total:
            LOGGER.warning("Only resolved %s tracks from initial plan; using fallback expansion", len(selected))
            selected.extend(self._fallback_fill(total - len(selected), selected, playlist_date))
        if len(selected) < total:
            raise RuntimeError(f"Could only produce {len(selected)} unique tracks; need {total}")
        return selected[:total]

    def _candidate_queries(self, playlist_date: str) -> list[SongQuery]:
        if self.openai_api_key:
            try:
                return AICurator(self.openai_api_key).generate_candidates(playlist_date, self.history, count=100)
            except RuntimeError as exc:
                LOGGER.warning("AI curator failed; falling back to local candidate plan: %s", exc)
        plan = self._fallback_plan()
        return plan.primary + plan.new_releases + plan.classics

    def _resolve_bucket(
        self,
        queries: list[SongQuery],
        bucket: str,
        target: int,
        playlist_date: str,
        selected_keys: set[str] | None = None,
        selected_artists: set[str] | None = None,
    ) -> list[SongCandidate]:
        selected: list[SongCandidate] = []
        selected_keys = selected_keys if selected_keys is not None else set()
        selected_artists = selected_artists if selected_artists is not None else set()
        bucket_queries = [query for query in queries if query.bucket == bucket]
        for query in bucket_queries:
            song = self._find_song_cached(query)
            if not song:
                continue
            if not self._is_allowed(song, playlist_date, selected_keys, selected_artists):
                continue
            selected.append(song)
            selected_keys.add(SongHistory.key(song.artist, song.song_name))
            selected_artists.add(normalize_history(song.artist))
            if len(selected) >= target:
                break
        return selected

    def _fallback_fill(
        self,
        count: int,
        already_selected: list[SongCandidate],
        playlist_date: str,
    ) -> list[SongCandidate]:
        selected_keys = {SongHistory.key(song.artist, song.song_name) for song in already_selected}
        selected_artists = {normalize_history(song.artist) for song in already_selected}
        candidates: list[SongCandidate] = []
        for artist in random.sample(PRIMARY_ARTISTS + NEW_RELEASE_ARTISTS + CLASSIC_ARTISTS, k=20):
            candidates.extend(
                self._search_artist_tracks_cached(
                    artist=artist,
                    genre="Alternative / Indie",
                    reason="Fallback replacement selected from the curated artist pool.",
                    limit=5,
                )
            )
        random.shuffle(candidates)
        results: list[SongCandidate] = []
        for candidate in candidates:
            if not self._is_allowed(candidate, playlist_date, selected_keys, selected_artists):
                continue
            results.append(candidate)
            selected_keys.add(SongHistory.key(candidate.artist, candidate.song_name))
            selected_artists.add(normalize_history(candidate.artist))
            if len(results) == count:
                break
        return results

    def _find_song_cached(self, query: SongQuery) -> SongCandidate | None:
        cache_key = SongHistory.key(query.artist, query.song_name or "*")
        if cache_key in self._song_lookup_cache:
            LOGGER.info("[iTunes] recommender cache hit: %s - %s", query.artist, query.song_name or "*")
            return self._song_lookup_cache[cache_key]
        song = self.itunes.find_song(query)
        self._song_lookup_cache[cache_key] = song
        return song

    def _search_artist_tracks_cached(
        self,
        artist: str,
        genre: str,
        reason: str,
        limit: int,
    ) -> list[SongCandidate]:
        cache_key = (normalize_history(artist), normalize_history(genre), limit)
        if cache_key in self._artist_tracks_cache:
            LOGGER.info("[iTunes] recommender artist cache hit: %s", artist)
            return self._artist_tracks_cache[cache_key]
        songs = self.itunes.search_artist_tracks(artist=artist, genre=genre, reason=reason, limit=limit)
        self._artist_tracks_cache[cache_key] = songs
        return songs

    def _is_allowed(
        self,
        song: SongCandidate,
        playlist_date: str,
        selected_keys: set[str],
        selected_artists: set[str],
    ) -> bool:
        key = SongHistory.key(song.artist, song.song_name)
        artist_key = normalize_history(song.artist)
        if key in selected_keys:
            LOGGER.info("Skipping same-run duplicate track: %s - %s", song.artist, song.song_name)
            return False
        if artist_key in selected_artists:
            LOGGER.info("Skipping same-day duplicate artist: %s", song.artist)
            return False
        if self.history.contains(song.artist, song.song_name):
            LOGGER.info("Skipping historical duplicate track: %s - %s", song.artist, song.song_name)
            return False
        if self.history.contains_recent(song.artist, song.song_name, playlist_date, days=30):
            LOGGER.info("Skipping recent 30-day duplicate track: %s - %s", song.artist, song.song_name)
            return False
        return True

    def _fallback_plan(self) -> PlaylistPlan:
        LOGGER.info("Using fallback recommender plan")
        primary_queries = self._artist_queries(
            random.sample(PRIMARY_ARTISTS, k=12),
            genre="Indie Pop / Indie Rock / Alternative",
            reason="Core indie/alternative recommendation from the curated artist pool.",
            bucket="primary",
        )

        new_queries = self._lastfm_queries(["indie", "alternative"], per_tag=6)
        if len(new_queries) < 8:
            new_queries.extend(
                self._artist_queries(
                    random.sample(NEW_RELEASE_ARTISTS, k=8),
                    genre="New Indie / Alternative",
                    reason="Recent acclaimed indie-adjacent artist selected from the fallback pool.",
                    bucket="recent",
                )
            )

        classic_queries = self._artist_queries(
            random.sample(CLASSIC_ARTISTS, k=8),
            genre="Classic Alternative / Indie",
            reason="Classic alternative or indie catalog pick.",
            bucket="classic",
        )

        if self.musicbrainz:
            try:
                classic_queries.extend(self.musicbrainz.search_recordings("classic alternative rock", limit=5))
            except requests.RequestException as exc:
                LOGGER.warning("MusicBrainz fallback failed: %s", exc)

        return PlaylistPlan(primary=primary_queries, new_releases=new_queries, classics=classic_queries)

    def _artist_queries(self, artists: list[str], genre: str, reason: str, bucket: str) -> list[SongQuery]:
        return [SongQuery(song_name="", artist=artist, genre=genre, reason=reason, bucket=bucket) for artist in artists]

    def _lastfm_queries(self, tags: list[str], per_tag: int) -> list[SongQuery]:
        if not self.lastfm:
            return []
        queries: list[SongQuery] = []
        for tag in tags:
            try:
                queries.extend(self.lastfm.top_tracks_by_tag(tag, limit=per_tag))
            except requests.RequestException as exc:
                LOGGER.warning("Last.fm lookup failed for tag %s: %s", tag, exc)
        random.shuffle(queries)
        return queries

    def _openai_plan(self, playlist_date: str) -> PlaylistPlan | None:
        if not self.openai_api_key:
            return None
        LOGGER.info("Using OpenAI-assisted recommender plan")
        payload = {
            "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
            "input": [
                {
                    "role": "user",
                    "content": (
                        f"{OPENAI_PROMPT}\n\n"
                        f"Playlist date: {playlist_date}\n"
                        "Return JSON only with keys primary, new_releases, classics. "
                        "Each key must be an array of objects with song_name, artist, genre, reason. "
                        "Return at least 18 primary, 8 new_releases, and 8 classics.\n\n"
                        f"Previous songs to avoid:\n{self.history.recent_summary()}"
                    ),
                }
            ],
            "text": {"format": {"type": "json_object"}},
        }
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {self.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=45,
            )
            response.raise_for_status()
            content = self._extract_response_text(response.json())
            raw = json.loads(content)
            return PlaylistPlan(
                primary=self._parse_queries(raw.get("primary", [])),
                new_releases=self._parse_queries(raw.get("new_releases", [])),
                classics=self._parse_queries(raw.get("classics", [])),
            )
        except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("OpenAI planning failed; falling back to curated pools: %s", exc)
            return None

    def _extract_response_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return str(payload["output_text"])
        output = payload.get("output", [])
        for item in output:
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and "text" in content:
                    return str(content["text"])
        raise ValueError("OpenAI response did not include output text")

    def _parse_queries(self, raw_items: Any) -> list[SongQuery]:
        if not isinstance(raw_items, list):
            return []
        queries: list[SongQuery] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            artist = str(item.get("artist", "")).strip()
            if not artist:
                continue
            queries.append(
                SongQuery(
                    song_name=str(item.get("song_name", "")).strip(),
                    artist=artist,
                    genre=str(item.get("genre", "Alternative / Indie")).strip(),
                    reason=str(item.get("reason", "AI-assisted indie playlist recommendation.")).strip(),
                    bucket=str(item.get("bucket", "primary")).strip(),
                )
            )
        return queries
