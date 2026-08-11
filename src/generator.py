from __future__ import annotations

import datetime as dt
import logging
import os
import random
from pathlib import Path

from src.history import HistoryEntry, SongHistory
from src.music_api import ITunesSearchClient, LastFmClient, MusicBrainzClient, SongCandidate
from src.recommender import Recommender

LOGGER = logging.getLogger(__name__)


def main() -> int:
    configure_logging()
    load_dotenv()

    playlist_date = os.environ.get("PLAYLIST_DATE") or dt.date.today().isoformat()
    playlist_title = f"{playlist_date} Daily Indie Mix"
    country = os.environ.get("PLAYLIST_COUNTRY", "US")
    output_dir = Path(os.environ.get("PLAYLIST_OUTPUT_DIR", "output"))
    history_path = Path(os.environ.get("SONGS_HISTORY_PATH", "data/songs_history.json"))

    LOGGER.info("Generating playlist: %s", playlist_title)
    history = SongHistory(history_path)
    itunes = ITunesSearchClient(country=country)
    lastfm_key = os.environ.get("LASTFM_API_KEY")
    lastfm = LastFmClient(lastfm_key) if lastfm_key else None
    musicbrainz = MusicBrainzClient()

    recommender = Recommender(
        itunes=itunes,
        history=history,
        lastfm=lastfm,
        musicbrainz=musicbrainz,
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
    )
    seed = int(playlist_date.replace("-", ""))
    random.seed(seed)
    songs = recommender.generate(playlist_date=playlist_date, seed=seed, total=20)

    output_path = write_playlist_markdown(output_dir, playlist_date, playlist_title, songs)
    history.add_many(
        [
            HistoryEntry(
                song_name=song.song_name,
                artist=song.artist,
                album=song.album,
                date_added=playlist_date,
            )
            for song in songs
        ]
    )
    history.save()

    LOGGER.info("Generated %s tracks", len(songs))
    LOGGER.info("Wrote playlist markdown to %s", output_path)
    return 0


def write_playlist_markdown(output_dir: Path, playlist_date: str, title: str, songs: list[SongCandidate]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{playlist_date}.md"
    lines = [f"# {title}", "", "## Tracklist", ""]
    for index, song in enumerate(songs, start=1):
        lines.extend(
            [
                f"{index}. {song.artist} - {song.song_name}",
                f"   Album: {song.album}",
                f"   Genre: {song.genre}",
                f"   Apple Music: {song.apple_music_url}",
                f"   Reason: {song.reason}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    raise SystemExit(main())
