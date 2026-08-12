from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.email_notifier import build_output_url, main, parse_playlist_markdown, write_last_run


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

    def test_write_last_run_records_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "data" / "last_run.json"

            write_last_run(path, "2026-08-12", 20, "sent")

            payload = path.read_text(encoding="utf-8")

        self.assertIn('"playlist_date": "2026-08-12"', payload)
        self.assertIn('"songs_count": 20', payload)
        self.assertIn('"email_status": "sent"', payload)

    def test_main_writes_failed_email_status_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "2026-08-12.md").write_text(
                """# 2026-08-12 Daily Indie Mix

## Tracklist

1. Alvvays - Dreams Tonite
   Album: Antisocialites
   Genre: Indie Pop
   Apple Music: https://music.apple.com/us/song/dreams-tonite/123
   Reason: Test reason.
""",
                encoding="utf-8",
            )
            last_run_path = root / "data" / "last_run.json"
            env = {
                "PLAYLIST_DATE": "2026-08-12",
                "PLAYLIST_OUTPUT_DIR": str(output_dir),
                "LAST_RUN_PATH": str(last_run_path),
                "MAIL_USERNAME": "sender@example.com",
                "MAIL_PASSWORD": "password",
                "MAIL_TO": "phone@example.com",
            }

            with patch.dict("os.environ", env, clear=False), patch(
                "src.email_notifier.send_email", side_effect=RuntimeError("SMTP down")
            ):
                exit_code = main()

            payload = last_run_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn('"songs_count": 1', payload)
        self.assertIn('"email_status": "failed: SMTP down"', payload)


if __name__ == "__main__":
    unittest.main()
