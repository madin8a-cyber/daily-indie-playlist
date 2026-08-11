from __future__ import annotations

import unittest
from unittest.mock import patch

from src.music_api import HttpClient, ITunesSearchClient, SongQuery


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self.payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.calls = 0

    def get(self, url: str, timeout: int, **kwargs) -> FakeResponse:
        self.calls += 1
        return self.responses.pop(0)


class FakeHttp:
    def __init__(self) -> None:
        self.calls = 0

    def get_json(self, url: str, **kwargs) -> dict:
        self.calls += 1
        return {
            "results": [
                {
                    "trackId": self.calls,
                    "trackName": kwargs["params"]["term"],
                    "artistName": "Artist",
                    "collectionName": "Album",
                }
            ]
        }


class MusicApiTests(unittest.TestCase):
    def test_builds_direct_apple_music_song_url_from_track_id(self) -> None:
        client = ITunesSearchClient(country="US")
        candidate = client._to_candidate(
            {
                "trackId": 123456789,
                "collectionId": 987654321,
                "trackName": "Archie, Marry Me",
                "artistName": "Alvvays",
                "collectionName": "Alvvays",
                "trackViewUrl": "https://music.apple.com/us/album/archie-marry-me/987654321?i=123456789",
                "collectionViewUrl": "https://music.apple.com/us/album/alvvays/987654321",
            },
            SongQuery(
                song_name="Archie, Marry Me",
                artist="Alvvays",
                genre="Indie Pop",
                reason="Test",
            ),
        )

        self.assertEqual(
            candidate.apple_music_url,
            "https://music.apple.com/us/song/archie-marry-me/123456789",
        )
        self.assertEqual(candidate.track_id, 123456789)
        self.assertEqual(candidate.collection_id, 987654321)
        self.assertEqual(
            candidate.track_view_url,
            "https://music.apple.com/us/album/archie-marry-me/987654321?i=123456789",
        )
        self.assertEqual(
            candidate.collection_view_url,
            "https://music.apple.com/us/album/alvvays/987654321",
        )

    def test_falls_back_to_search_url_without_track_id(self) -> None:
        client = ITunesSearchClient(country="US")
        candidate = client._to_candidate(
            {
                "trackName": "Unknown Song",
                "artistName": "Unknown Artist",
                "collectionName": "Unknown Album",
            },
            SongQuery(
                song_name="Unknown Song",
                artist="Unknown Artist",
                genre="Indie Rock",
                reason="Test",
            ),
        )

        self.assertEqual(
            candidate.apple_music_url,
            "https://music.apple.com/search?term=Unknown+Artist+Unknown+Song",
        )

    def test_retries_429_with_expected_backoff(self) -> None:
        http = HttpClient()
        http.session = FakeSession(
            [
                FakeResponse(429),
                FakeResponse(429),
                FakeResponse(200, {"ok": True}),
            ]
        )

        with patch("src.music_api.random.uniform", return_value=0.5), patch("src.music_api.time.sleep") as sleep:
            payload = http.get_json("https://itunes.apple.com/search")

        self.assertEqual(payload, {"ok": True})
        sleep.assert_any_call(0.5)
        sleep.assert_any_call(2)
        sleep.assert_any_call(5)
        self.assertEqual(http.session.calls, 3)

    def test_daily_request_limit_reuses_cache_and_skips_new_terms(self) -> None:
        fake_http = FakeHttp()
        client = ITunesSearchClient(country="US", http=fake_http, daily_request_limit=1)

        first = client._search("Alvvays", limit=1)
        cached = client._search("Alvvays", limit=1)
        skipped = client._search("Beach House", limit=1)

        self.assertEqual(len(first), 1)
        self.assertEqual(cached, first)
        self.assertEqual(skipped, [])
        self.assertEqual(fake_http.calls, 1)


if __name__ == "__main__":
    unittest.main()
