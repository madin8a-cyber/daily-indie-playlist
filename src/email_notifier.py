from __future__ import annotations

import datetime as dt
import html
import logging
import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

LOGGER = logging.getLogger(__name__)

TRACK_RE = re.compile(r"^\d+\.\s+(?P<artist>.+?)\s+-\s+(?P<song>.+?)\s*$")
APPLE_MUSIC_RE = re.compile(r"^\s*Apple Music:\s*(?P<url>\S+)\s*$")


@dataclass(frozen=True)
class EmailTrack:
    artist: str
    song: str
    apple_music_url: str


def main() -> int:
    configure_logging()
    playlist_date = os.environ.get("PLAYLIST_DATE") or dt.date.today().isoformat()
    output_dir = Path(os.environ.get("PLAYLIST_OUTPUT_DIR", "output"))
    playlist_path = output_dir / f"{playlist_date}.md"
    if not playlist_path.exists():
        LOGGER.warning("Playlist output not found; skipping email: %s", playlist_path)
        return 0

    username = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")
    mail_to = os.environ.get("MAIL_TO")
    if not username or not password or not mail_to:
        LOGGER.warning("Mail secrets are incomplete; skipping email")
        return 0

    title, tracks = parse_playlist_markdown(playlist_path)
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    github_ref = os.environ.get("GITHUB_REF_NAME", "main")
    output_url = build_output_url(repo, github_ref, playlist_path)

    message = build_email_message(
        mail_from=username,
        mail_to=mail_to,
        subject=f"🎧 {title}",
        title=title,
        tracks=tracks,
        output_url=output_url,
    )
    send_email(username=username, password=password, message=message)
    LOGGER.info("Sent Daily Indie Mix email to %s", mail_to)
    return 0


def parse_playlist_markdown(path: Path) -> tuple[str, list[EmailTrack]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = lines[0].lstrip("#").strip() if lines else path.stem
    tracks: list[EmailTrack] = []
    current_artist = ""
    current_song = ""
    for line in lines:
        track_match = TRACK_RE.match(line)
        if track_match:
            current_artist = track_match.group("artist").strip()
            current_song = track_match.group("song").strip()
            continue
        url_match = APPLE_MUSIC_RE.match(line)
        if url_match and current_artist and current_song:
            tracks.append(
                EmailTrack(
                    artist=current_artist,
                    song=current_song,
                    apple_music_url=url_match.group("url").strip(),
                )
            )
            current_artist = ""
            current_song = ""
    return title, tracks


def build_output_url(repo: str, ref_name: str, playlist_path: Path) -> str:
    if not repo:
        return str(playlist_path)
    path = quote(playlist_path.as_posix())
    return f"https://github.com/{repo}/blob/{ref_name}/{path}"


def build_email_message(
    mail_from: str,
    mail_to: str,
    subject: str,
    title: str,
    tracks: list[EmailTrack],
    output_url: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = mail_from
    message["To"] = mail_to
    message["Subject"] = subject
    text_body = build_text_body(title, tracks, output_url)
    html_body = build_html_body(title, tracks, output_url)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


def build_text_body(title: str, tracks: list[EmailTrack], output_url: str) -> str:
    lines = [title, "", "Tracklist:"]
    for index, track in enumerate(tracks, start=1):
        lines.append(f"{index}. {track.artist} - {track.song}")
        lines.append(f"   Apple Music: {track.apple_music_url}")
    lines.extend(["", f"Output file: {output_url}"])
    return "\n".join(lines) + "\n"


def build_html_body(title: str, tracks: list[EmailTrack], output_url: str) -> str:
    items = "\n".join(
        "<li>"
        f"{html.escape(track.artist)} - {html.escape(track.song)}"
        f"<br><a href=\"{html.escape(track.apple_music_url)}\">Apple Music</a>"
        "</li>"
        for track in tracks
    )
    return (
        "<html><body>"
        f"<h1>{html.escape(title)}</h1>"
        f"<ol>{items}</ol>"
        f"<p>Output file: <a href=\"{html.escape(output_url)}\">{html.escape(output_url)}</a></p>"
        "</body></html>"
    )


def send_email(username: str, password: str, message: EmailMessage) -> None:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


if __name__ == "__main__":
    raise SystemExit(main())
