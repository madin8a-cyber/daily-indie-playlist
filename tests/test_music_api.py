from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
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
                    "trackId": 1000 + self.calls,
                    "collectionId": 2000 + self.calls,
                    "trackName": kwargs["params"]["term"],
                    "artistName": "Artist",
                    "collectionName": "Album",
                    "trackViewUrl": f"https://music.apple.com/us/album/x?i={1000 + self.calls}",
                    "collectionViewUrl": f"https://music.apple.com/us/album/{2000 + self.calls}",
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

        with patch("src.music_api.random.uniform", return_value=0.8), patch("src.music_api.time.sleep") as sleep:
            payload = http.get_json("https://itunes.apple.com/search")

        self.assertEqual(payload, {"ok": True})
        sleep.assert_any_call(0.8)
        sleep.assert_any_call(2)
        sleep.assert_any_call(5)
        self.assertEqual(http.session.calls, 3)

    def test_daily_request_limit_reuses_cache_and_skips_new_terms(self) -> None:
        fake_http = FakeHttp()
        with tempfile.TemporaryDirectory() as temp_dir:
            client = ITunesSearchClient(
                country="US",
                http=fake_http,
                daily_request_limit=1,
                cache_path=Path(temp_dir) / "itunes_cache.json",
            )

            first = client._search("Alvvays", limit=1)
            cached = client._search("Alvvays", limit=1)
            skipped = client._search("Beach House", limit=1)

        self.assertEqual(len(first), 1)
        self.assertEqual(cached, first)
        self.assertEqual(skipped, [])
        self.assertEqual(fake_http.calls, 1)

    def test_normal_search_returns_track_id_and_writes_cache(self) -> None:
        fake_http = FakeHttp()
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "itunes_cache.json"
            client = ITunesSearchClient(country="US", http=fake_http, cache_path=cache_path)

            candidate = client.find_song(
                SongQuery(
                    song_name="Dreams Tonite",
                    artist="Alvvays",
                    genre="Indie Pop",
                    reason="Test",
                )
            )

            self.assertIsNotNone(candidate)
            assert candidate is not None
            self.assertEqual(candidate.track_id, 1001)
            self.assertIn("alvvays|dreams tonite", cache_path.read_text(encoding="utf-8"))

    def test_song_cache_hit_does_not_call_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "itunes_cache.json"
            cache_path.write_text(
                """
{
  "alvvays|dreams tonite": {
    "trackId": 123,
    "collectionId": 456,
    "trackName": "Dreams Tonite",
    "artistName": "Alvvays",
    "collectionName": "Antisocialites",
    "trackViewUrl": "https://music.apple.com/us/album/antisocialites?i=123",
    "collectionViewUrl": "https://music.apple.com/us/album/antisocialites/456"
  }
}
""".strip()
                + "\n",
                encoding="utf-8",
            )
            fake_http = FakeHttp()
            client = ITunesSearchClient(country="US", http=fake_http, cache_path=cache_path)

            candidate = client.find_song(
                SongQuery(
                    song_name="Dreams Tonite",
                    artist="Alvvays",
                    genre="Indie Pop",
                    reason="Test",
                )
            )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(candidate.track_id, 123)
        self.assertEqual(fake_http.calls, 0)

    def test_429_after_retries_returns_empty_search_results(self) -> None:
        http = HttpClient()
        http.session = FakeSession([FakeResponse(429), FakeResponse(429), FakeResponse(429), FakeResponse(429)])
        with tempfile.TemporaryDirectory() as temp_dir:
            client = ITunesSearchClient(country="US", http=http, cache_path=Path(temp_dir) / "itunes_cache.json")

            with patch("src.music_api.random.uniform", return_value=0.8), patch("src.music_api.time.sleep"):
                result = client._search("Alvvays Dreams Tonite", limit=10)

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
