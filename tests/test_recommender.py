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
            )
            for index in range(limit)
        ]


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


if __name__ == "__main__":
    unittest.main()
