from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from src.history import HistoryEntry, SongHistory
from src.music_api import SongCandidate, SongQuery
from src.recommender import Recommender


class FakeITunesClient:
    def __init__(self) -> None:
        self.counter = 0

    def find_song(self, query: SongQuery) -> SongCandidate:
        self.counter += 1
        title = query.song_name or f"Track {self.counter}"
        artist = query.artist or f"Artist {self.counter}"
        return SongCandidate(
            song_name=title,
            artist=artist,
            album=f"Album {self.counter}",
            genre=query.genre,
            apple_music_url=f"https://music.apple.com/search?term={artist}+{title}",
            reason=query.reason,
            source="fake",
            bucket=query.bucket,
        )

    def search_artist_tracks(self, artist: str, genre: str, reason: str, limit: int = 5) -> list[SongCandidate]:
        return [
            SongCandidate(
                song_name=f"{artist} Deep Cut {index}",
                artist=artist,
                album=f"{artist} Album",
                genre=genre,
                apple_music_url=f"https://music.apple.com/search?term={artist}",
                reason=reason,
                source="fake",
                bucket="primary",
            )
            for index in range(limit)
        ]


class CandidateRecommender(Recommender):
    def __init__(self, candidates: list[SongQuery], history: SongHistory) -> None:
        super().__init__(itunes=FakeITunesClient(), history=history, openai_api_key="test-key")
        self.candidates = candidates

    def _candidate_queries(self, playlist_date: str) -> list[SongQuery]:
        return self.candidates


class RecommenderTests(unittest.TestCase):
    def test_generates_twenty_tracks_without_openai(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            recommender = Recommender(itunes=FakeITunesClient(), history=history)

            songs = recommender.generate(playlist_date="2026-08-12", seed=20260812, total=20)

        self.assertEqual(len(songs), 20)
        self.assertEqual(len({(song.artist, song.song_name) for song in songs}), 20)

    def test_skips_history_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            history.add_many(
                [
                    HistoryEntry(
                        song_name="Track 1",
                        artist="Alvvays",
                        album="Old Album",
                        date_added=(dt.date.today() - dt.timedelta(days=1)).isoformat(),
                    )
                ]
            )
            recommender = Recommender(itunes=FakeITunesClient(), history=history)

            songs = recommender.generate(playlist_date="2026-08-12", seed=20260812, total=20)

        self.assertNotIn(("Alvvays", "Track 1"), {(song.artist, song.song_name) for song in songs})

    def test_ai_candidate_filter_enforces_ratio_and_artist_uniqueness(self) -> None:
        candidates = make_candidates(70, "primary", "Primary Artist")
        candidates.extend(make_candidates(20, "recent", "Recent Artist"))
        candidates.extend(make_candidates(20, "classic", "Classic Artist"))
        candidates.insert(
            0,
            SongQuery(
                song_name="Duplicate Artist Song",
                artist="Primary Artist 1",
                genre="Indie Rock",
                reason="Should be skipped because the artist repeats.",
                bucket="primary",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            recommender = CandidateRecommender(candidates, history)

            songs = recommender.generate(playlist_date="2026-08-12", seed=20260812, total=20)

        self.assertEqual(len(songs), 20)
        self.assertEqual(sum(1 for song in songs if song.bucket == "primary"), 12)
        self.assertEqual(sum(1 for song in songs if song.bucket == "recent"), 4)
        self.assertEqual(sum(1 for song in songs if song.bucket == "classic"), 4)
        self.assertEqual(len({song.artist for song in songs}), 20)

    def test_ai_candidate_filter_skips_recent_thirty_day_duplicate(self) -> None:
        candidates = [
            SongQuery(
                song_name="Recent Duplicate",
                artist="Recent Duplicate Artist",
                genre="Indie Pop",
                reason="Should be skipped due to recent history.",
                bucket="primary",
            )
        ]
        candidates.extend(make_candidates(70, "primary", "Primary Artist"))
        candidates.extend(make_candidates(20, "recent", "Recent Artist"))
        candidates.extend(make_candidates(20, "classic", "Classic Artist"))

        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            history.add_many(
                [
                    HistoryEntry(
                        song_name="Recent Duplicate",
                        artist="Recent Duplicate Artist",
                        album="Recent Album",
                        date_added="2026-08-01",
                    )
                ]
            )
            recommender = CandidateRecommender(candidates, history)

            songs = recommender.generate(playlist_date="2026-08-12", seed=20260812, total=20)

        self.assertNotIn(
            ("Recent Duplicate Artist", "Recent Duplicate"),
            {(song.artist, song.song_name) for song in songs},
        )


def make_candidates(count: int, bucket: str, artist_prefix: str) -> list[SongQuery]:
    return [
        SongQuery(
            song_name=f"{bucket.title()} Song {index}",
            artist=f"{artist_prefix} {index}",
            genre=bucket,
            reason=f"{bucket} candidate",
            bucket=bucket,
        )
        for index in range(count)
    ]


if __name__ == "__main__":
    unittest.main()
