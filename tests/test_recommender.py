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

    def apple_music_search_url(self, artist: str, song_name: str) -> str:
        return f"https://music.apple.com/search?term={artist}+{song_name}"


class PartiallyMatchingITunesClient(FakeITunesClient):
    def __init__(self, matches: int) -> None:
        super().__init__()
        self.matches = matches

    def find_song(self, query: SongQuery) -> SongCandidate | None:
        if self.counter >= self.matches:
            self.counter += 1
            return None
        return super().find_song(query)


class NoMatchingITunesClient(FakeITunesClient):
    def find_song(self, query: SongQuery) -> SongCandidate | None:
        self.counter += 1
        return None

    def search_artist_tracks(self, artist: str, genre: str, reason: str, limit: int = 5) -> list[SongCandidate]:
        return []


class CandidateRecommender(Recommender):
    def __init__(
        self,
        candidates: list[SongQuery],
        history: SongHistory,
        itunes: FakeITunesClient | None = None,
    ) -> None:
        super().__init__(itunes=itunes or FakeITunesClient(), history=history, openai_api_key="test-key")
        self.candidates = candidates

    def _candidate_queries(self, playlist_date: str) -> list[SongQuery]:
        return self.candidates


class NoEmergencyCandidateRecommender(CandidateRecommender):
    def _emergency_queries(self) -> list[SongQuery]:
        return []


class NoFallbackCandidateRecommender(NoEmergencyCandidateRecommender):
    def _broad_fallback_queries(self) -> list[SongQuery]:
        return []


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

    def test_recommender_caches_duplicate_song_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            fake_itunes = FakeITunesClient()
            recommender = Recommender(itunes=fake_itunes, history=history)
            query = SongQuery(
                song_name="Dreams Tonite",
                artist="Alvvays",
                genre="Indie Pop",
                reason="Cache test",
                bucket="primary",
            )

            first = recommender._find_song_cached(query)
            second = recommender._find_song_cached(query)

        self.assertEqual(first, second)
        self.assertEqual(fake_itunes.counter, 1)

    def test_uses_unverified_candidates_when_itunes_matches_are_insufficient(self) -> None:
        candidates = make_candidates(70, "primary", "Primary Artist")
        candidates.extend(make_candidates(20, "recent", "Recent Artist"))
        candidates.extend(make_candidates(20, "classic", "Classic Artist"))
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            recommender = CandidateRecommender(
                candidates,
                history,
                itunes=PartiallyMatchingITunesClient(matches=13),
            )

            songs = recommender.generate(playlist_date="2026-08-23", seed=20260823, total=20)

        self.assertEqual(len(songs), 20)
        self.assertEqual(sum(1 for song in songs if song.bucket == "primary"), 12)
        self.assertEqual(sum(1 for song in songs if song.bucket == "recent"), 4)
        self.assertEqual(sum(1 for song in songs if song.bucket == "classic"), 4)
        self.assertGreaterEqual(sum(1 for song in songs if song.source == "AI curator candidate"), 7)

    def test_relaxed_emergency_fallback_when_history_blocks_candidates(self) -> None:
        candidates = make_candidates(70, "primary", "Primary Artist")
        candidates.extend(make_candidates(20, "recent", "Recent Artist"))
        candidates.extend(make_candidates(20, "classic", "Classic Artist"))
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            history.add_many(
                [
                    HistoryEntry(
                        song_name=query.song_name,
                        artist=query.artist,
                        album="Already Used",
                        date_added="2026-08-20",
                    )
                    for query in candidates
                ]
            )
            recommender = CandidateRecommender(candidates, history)

            songs = recommender.generate(playlist_date="2026-08-24", seed=20260824, total=20)

        self.assertEqual(len(songs), 20)
        self.assertEqual(sum(1 for song in songs if song.bucket == "primary"), 12)
        self.assertEqual(sum(1 for song in songs if song.bucket == "recent"), 4)
        self.assertEqual(sum(1 for song in songs if song.bucket == "classic"), 4)
        self.assertEqual(len({song.artist for song in songs}), 20)

    def test_broad_soft_rock_pop_fallback_can_complete_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            recommender = NoEmergencyCandidateRecommender(
                candidates=[],
                history=history,
                itunes=NoMatchingITunesClient(),
            )

            songs = recommender.generate(playlist_date="2026-08-24", seed=20260824, total=20)

        self.assertEqual(len(songs), 20)
        self.assertTrue(any("Soft Rock" in song.genre or "Pop" in song.genre for song in songs))

    def test_generated_search_fallback_prevents_crash_when_all_pools_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history = SongHistory(Path(temp_dir) / "songs_history.json")
            recommender = NoFallbackCandidateRecommender(
                candidates=[],
                history=history,
                itunes=NoMatchingITunesClient(),
            )

            songs = recommender.generate(playlist_date="2026-08-24", seed=20260824, total=20)

        self.assertEqual(len(songs), 20)
        self.assertEqual(sum(1 for song in songs if song.source == "Generated search fallback"), 20)


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
