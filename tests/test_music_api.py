from __future__ import annotations

import unittest

from src.music_api import ITunesSearchClient, SongQuery


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


if __name__ == "__main__":
    unittest.main()
