#!/usr/bin/env python3
"""Download all transcripts of a Devoxx YouTube playlist.

By default the tool looks up the "Devoxx Belgium 2026" playlist on the
@DevoxxForever channel, lists every video in it and saves the transcript of
each video to the output directory. Videos that already have a transcript on
disk are skipped, so the tool can be re-run to pick up newly uploaded talks.

Examples:
    python devoxx_transcripts.py
    python devoxx_transcripts.py --playlist "https://www.youtube.com/playlist?list=PL..."
    python devoxx_transcripts.py --format srt --lang en nl fr --delay 2
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.formatters import (
    JSONFormatter,
    SRTFormatter,
    TextFormatter,
    WebVTTFormatter,
)

DEFAULT_CHANNEL = "https://www.youtube.com/@DevoxxForever"
DEFAULT_MATCH = r"devoxx\s+belgium.*2026|2026.*devoxx\s+belgium"

FORMATTERS = {
    "txt": TextFormatter(),
    "json": JSONFormatter(),
    "srt": SRTFormatter(),
    "vtt": WebVTTFormatter(),
}


@dataclass
class Video:
    id: str
    title: str
    index: int

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def slugify(text: str, max_len: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)[:max_len].strip("-") or "untitled"


def ydl(cookies: str | None) -> yt_dlp.YoutubeDL:
    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True}
    if cookies:
        opts["cookiefile"] = cookies
    return yt_dlp.YoutubeDL(opts)


def find_playlist(channel: str, pattern: str, cookies: str | None) -> str:
    """Return the URL of the first playlist on `channel` whose title matches `pattern`."""
    log(f"Zoeken naar playlist /{pattern}/ op {channel} ...")
    info = ydl(cookies).extract_info(f"{channel.rstrip('/')}/playlists", download=False)
    regex = re.compile(pattern, re.IGNORECASE)
    entries = info.get("entries") or []
    for entry in entries:
        if regex.search(entry.get("title") or ""):
            log(f"Gevonden: {entry['title']}")
            return entry.get("url") or f"https://www.youtube.com/playlist?list={entry['id']}"
    titles = "\n  ".join(e.get("title") or "?" for e in entries[:30])
    raise SystemExit(
        f"Geen playlist gevonden die matcht met /{pattern}/.\n"
        f"Beschikbare playlists (eerste 30):\n  {titles}\n"
        "Geef de playlist expliciet mee met --playlist <url>."
    )


def list_videos(playlist: str, cookies: str | None) -> tuple[str, list[Video]]:
    info = ydl(cookies).extract_info(playlist, download=False)
    videos = [
        Video(id=e["id"], title=e.get("title") or e["id"], index=i)
        for i, e in enumerate(info.get("entries") or [], start=1)
        if e and e.get("id")
    ]
    return info.get("title") or playlist, videos


def fetch_transcript(api: YouTubeTranscriptApi, video_id: str, languages: list[str]):
    """Prefer a manual transcript in `languages`, then auto-generated, then anything."""
    transcripts = api.list(video_id)
    for finder in (transcripts.find_manually_created_transcript, transcripts.find_generated_transcript):
        try:
            return finder(languages).fetch()
        except NoTranscriptFound:
            pass
    # Fall back to whatever exists (e.g. a talk in a language not asked for).
    for transcript in transcripts:
        return transcript.fetch()
    raise NoTranscriptFound(video_id, languages, transcripts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--playlist", help="Playlist URL of ID. Zonder deze optie wordt de playlist op het kanaal gezocht.")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL, help=f"Kanaal om in te zoeken (default: {DEFAULT_CHANNEL})")
    parser.add_argument("--match", default=DEFAULT_MATCH, help="Regex op de playlisttitel (default: Devoxx Belgium 2026)")
    parser.add_argument("-o", "--out", default="transcripts", help="Output directory (default: transcripts)")
    parser.add_argument("--format", choices=FORMATTERS, default="txt", help="Bestandsformaat (default: txt)")
    parser.add_argument("--lang", nargs="+", default=["en"], help="Voorkeurtalen in volgorde (default: en)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconden pauze tussen video's (default: 1)")
    parser.add_argument("--limit", type=int, help="Enkel de eerste N video's verwerken")
    parser.add_argument("--force", action="store_true", help="Bestaande transcripts opnieuw downloaden")
    parser.add_argument("--cookies", help="cookies.txt (Netscape-formaat) voor de playlist-lookup")
    args = parser.parse_args()

    playlist = args.playlist
    if playlist and not playlist.startswith("http"):
        playlist = f"https://www.youtube.com/playlist?list={playlist}"
    if not playlist:
        playlist = find_playlist(args.channel, args.match, args.cookies)

    title, videos = list_videos(playlist, args.cookies)
    if args.limit:
        videos = videos[: args.limit]
    log(f"Playlist: {title} — {len(videos)} video's")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    api = YouTubeTranscriptApi()
    formatter = FORMATTERS[args.format]
    rows = []
    counts = {"ok": 0, "skipped": 0, "missing": 0, "error": 0}

    for n, video in enumerate(videos, start=1):
        path = out / f"{video.index:03d}-{slugify(video.title)}-{video.id}.{args.format}"
        row = {"index": video.index, "video_id": video.id, "title": video.title, "url": video.url, "file": path.name}
        prefix = f"[{n}/{len(videos)}] {video.title}"

        if path.exists() and not args.force:
            counts["skipped"] += 1
            rows.append({**row, "status": "skipped", "language": ""})
            log(f"{prefix} — bestaat al")
            continue

        try:
            fetched = fetch_transcript(api, video.id, args.lang)
        except (IpBlocked, RequestBlocked) as exc:
            log(f"{prefix} — geblokkeerd door YouTube, stop. Probeer later opnieuw of verhoog --delay.\n{exc}")
            counts["error"] += 1
            break
        except CouldNotRetrieveTranscript as exc:
            status = "missing" if isinstance(exc, (NoTranscriptFound, TranscriptsDisabled)) else "error"
            counts[status] += 1
            rows.append({**row, "status": status, "language": "", "file": ""})
            log(f"{prefix} — geen transcript ({type(exc).__name__})")
            continue

        body = formatter.format_transcript(fetched)
        if args.format == "txt":
            header = f"# {video.title}\n# {video.url}\n# language: {fetched.language_code}"
            header += " (auto-generated)\n\n" if fetched.is_generated else "\n\n"
            body = header + body
        path.write_text(body + "\n", encoding="utf-8")
        counts["ok"] += 1
        rows.append({**row, "status": "ok", "language": fetched.language_code})
        log(f"{prefix} — ok ({fetched.language_code}{', auto' if fetched.is_generated else ''})")
        if n < len(videos):
            time.sleep(args.delay)

    with (out / "index.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["index", "video_id", "title", "url", "file", "status", "language"])
        writer.writeheader()
        writer.writerows(rows)
    (out / "playlist.json").write_text(
        json.dumps({"title": title, "url": playlist, "videos": [v.__dict__ for v in videos]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    log(f"Klaar: {counts['ok']} gedownload, {counts['skipped']} overgeslagen, "
        f"{counts['missing']} zonder transcript, {counts['error']} fouten. Output in {out}/")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
