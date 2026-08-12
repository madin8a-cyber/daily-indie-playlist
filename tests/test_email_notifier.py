from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.email_notifier import build_output_url, parse_playlist_markdown


class EmailNotifierTests(unittest.TestCase):
    def test_parse_playlist_markdown_extracts_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            playlist_path = Path(temp_dir) / "2026-08-12.md"
            playlist_path.write_text(
                """# 2026-08-12 Daily Indie Mix

## Tracklist

1. Alvvays - Dreams Tonite
   Album: Antisocialites
   Genre: Indie Pop
   Apple Music: https://music.apple.com/us/song/dreams-tonite/123
   Reason: Test reason.

2. Radiohead - Weird Fishes / Arpeggi
   Album: In Rainbows
   Genre: Classic Alternative
   Apple Music: https://music.apple.com/us/song/weird-fishes-arpeggi/456
   Reason: Test reason.
""",
                encoding="utf-8",
            )

            title, tracks = parse_playlist_markdown(playlist_path)

        self.assertEqual(title, "2026-08-12 Daily Indie Mix")
        self.assertEqual(len(tracks), 2)
        self.assertEqual(tracks[0].artist, "Alvvays")
        self.assertEqual(tracks[0].song, "Dreams Tonite")
        self.assertEqual(tracks[0].apple_music_url, "https://music.apple.com/us/song/dreams-tonite/123")

    def test_build_output_url_points_to_repo_file(self) -> None:
        url = build_output_url(
            repo="madin8a-cyber/daily-indie-playlist",
            ref_name="main",
            playlist_path=Path("output/2026-08-12.md"),
        )

        self.assertEqual(
            url,
            "https://github.com/madin8a-cyber/daily-indie-playlist/blob/main/output/2026-08-12.md",
        )


if __name__ == "__main__":
    unittest.main()
