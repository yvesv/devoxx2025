#!/usr/bin/env python3
"""Download all transcripts of a Devoxx YouTube playlist.

By default the tool looks up the "Devoxx Belgium 2026" playlist on the
@DevoxxForever channel, lists every video in it and saves the transcript of
each video to the output directory: always as structured JSON (metadata plus
segments with timestamps), and also in the chosen --format. Videos that already
have a transcript on disk are skipped, so the tool can be re-run to pick up newly uploaded talks.

Examples:
    python devoxx_transcripts.py
    python devoxx_transcripts.py --playlist "https://www.youtube.com/playlist?list=PL..."
    python devoxx_transcripts.py --format srt --lang en nl fr --delay 2
    python devoxx_transcripts.py --webshare-user USER --webshare-pass PASS
    python devoxx_transcripts.py --proxy http://user:pass@host:port
    python devoxx_transcripts.py --tor          # gratis, via een lokale Tor (brew install tor)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import secrets
import socket
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
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
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig
from youtube_transcript_api.formatters import (
    SRTFormatter,
    TextFormatter,
    WebVTTFormatter,
)

DEFAULT_CHANNEL = "https://www.youtube.com/@DevoxxForever"
DEFAULT_MATCH = r"devoxx\s+belgium.*2026|2026.*devoxx\s+belgium"

FORMATTERS = {
    "txt": TextFormatter(),
    "json": None,  # structured JSON, always written (see structured_json)
    "srt": SRTFormatter(),
    "vtt": WebVTTFormatter(),
}


@dataclass
class Video:
    id: str
    title: str
    position: int  # current position in the playlist; can shift when videos are added
    duration: float | None = None
    number: int = 0  # stable file number, see assign_numbers()

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
        Video(id=e["id"], title=e.get("title") or e["id"], position=i, duration=e.get("duration"))
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


NUMBERS_FILE = "numbers.json"
FILE_RE = re.compile(r"^(\d+)-.*-([A-Za-z0-9_-]{11})\.[a-z]+$")


def assign_numbers(out: Path, videos: list[Video]) -> None:
    """Give every video a number that never changes once assigned.

    Numbers live in numbers.json. On the first run they are taken over from the
    file names already on disk. New videos get the next free number, in playlist
    order; numbers of videos that leave the playlist are never reused.
    """
    registry_path = out / NUMBERS_FILE
    if registry_path.exists():
        registry = {k: int(v) for k, v in json.loads(registry_path.read_text(encoding="utf-8")).items()}
    else:
        registry = {}
        for file in sorted(out.iterdir()):
            match = FILE_RE.match(file.name)
            if match:
                number, video_id = int(match[1]), match[2]
                registry[video_id] = min(number, registry.get(video_id, number))
        # Two videos with the same number on disk: the first keeps it, the other gets a new one below.
        seen = set()
        for video_id, number in sorted(registry.items(), key=lambda item: (item[1], item[0])):
            if number in seen:
                del registry[video_id]
            seen.add(number)

    next_number = max(registry.values(), default=0) + 1
    for video in videos:
        if video.id not in registry:
            registry[video.id] = next_number
            next_number += 1
        video.number = registry[video.id]

    ordered = dict(sorted(registry.items(), key=lambda item: item[1]))
    registry_path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def structured_json(video: Video, playlist_title: str, playlist_url: str, fetched) -> str:
    segments = [
        {
            "start": round(snippet.start, 2),
            "end": round(snippet.start + snippet.duration, 2),
            "duration": round(snippet.duration, 2),
            "timestamp": hms(snippet.start),
            "url": f"https://youtu.be/{video.id}?t={int(snippet.start)}",
            "text": snippet.text,
        }
        for snippet in fetched.snippets
    ]
    data = {
        "video_id": video.id,
        "title": video.title,
        "url": video.url,
        "number": video.number,
        "playlist": {"title": playlist_title, "url": playlist_url, "position": video.position},
        "video_duration": video.duration,
        "transcript": {
            "language": fetched.language,
            "language_code": fetched.language_code,
            "is_generated": fetched.is_generated,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "segment_count": len(segments),
            "full_text": " ".join(s["text"] for s in segments),
        },
        "segments": segments,
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def tor_url(port: int) -> str:
    # Tor isolates circuits per SOCKS credentials (IsolateSOCKSAuth), so fresh
    # random credentials give a new circuit and usually a new exit IP.
    return f"socks5h://{secrets.token_hex(8)}:x@127.0.0.1:{port}"


def check_tor(port: int) -> None:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=3).close()
    except OSError:
        raise SystemExit(
            f"Tor draait niet (niets bereikbaar op 127.0.0.1:{port}).\n"
            "Start Tor in een apart terminalvenster met: tor\n"
            "Wacht tot je 'Bootstrapped 100% (done)' ziet en start dit script dan opnieuw."
        )


class Fetcher:
    """Fetches transcripts and works around IP blocks: a new Tor circuit, or a cool-down."""

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.api = YouTubeTranscriptApi(proxy_config=self.proxy_config())

    def proxy_config(self):
        args = self.args
        if args.tor:
            url = tor_url(args.tor_port)
            return GenericProxyConfig(http_url=url, https_url=url)
        if args.webshare_user and args.webshare_pass:
            return WebshareProxyConfig(proxy_username=args.webshare_user, proxy_password=args.webshare_pass)
        if args.proxy:
            return GenericProxyConfig(http_url=args.proxy, https_url=args.proxy)
        return None

    def describe(self) -> str:
        args = self.args
        if args.tor:
            return f"Tor op 127.0.0.1:{args.tor_port}, nieuw circuit bij elke blokkade"
        if args.webshare_user and args.webshare_pass:
            return "Webshare rotating residential"
        if args.proxy:
            return re.sub(r"//[^@]*@", "//***@", args.proxy)
        return "geen (eigen IP)"

    def fetch(self, video_id: str, prefix: str):
        if self.args.tor:
            return self._fetch_rotating_tor(video_id, prefix)
        return self._fetch_with_cooldown(video_id, prefix)

    def _fetch_rotating_tor(self, video_id: str, prefix: str):
        rotations = self.args.tor_rotations
        for attempt in range(rotations + 1):
            try:
                return fetch_transcript(self.api, video_id, self.args.lang)
            except (IpBlocked, RequestBlocked):
                if attempt == rotations:
                    raise
                log(f"{prefix} — Tor exit geblokkeerd, nieuw circuit ({attempt + 1}/{rotations}) ...")
                self.api = YouTubeTranscriptApi(proxy_config=self.proxy_config())
                time.sleep(2)

    def _fetch_with_cooldown(self, video_id: str, prefix: str):
        """Retry after an IP block, doubling the wait each time."""
        wait = self.args.cooldown
        for attempt in range(self.args.retries + 1):
            try:
                return fetch_transcript(self.api, video_id, self.args.lang)
            except (IpBlocked, RequestBlocked):
                if attempt == self.args.retries:
                    raise
                log(f"{prefix} — geblokkeerd door YouTube, wacht {wait:.0f}s (poging {attempt + 1}/{self.args.retries}) ...")
                time.sleep(wait)
                wait *= 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--playlist", help="Playlist URL of ID. Zonder deze optie wordt de playlist op het kanaal gezocht.")
    parser.add_argument("--channel", default=DEFAULT_CHANNEL, help=f"Kanaal om in te zoeken (default: {DEFAULT_CHANNEL})")
    parser.add_argument("--match", default=DEFAULT_MATCH, help="Regex op de playlisttitel (default: Devoxx Belgium 2026)")
    parser.add_argument("-o", "--out", default="transcripts", help="Output directory (default: transcripts)")
    parser.add_argument("--format", choices=FORMATTERS, default="txt",
                        help="Extra leesbaar formaat naast de JSON, die altijd wordt bewaard (default: txt)")
    parser.add_argument("--lang", nargs="+", default=["en"], help="Voorkeurtalen in volgorde (default: en)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconden pauze tussen video's (default: 1)")
    parser.add_argument("--limit", type=int, help="Enkel de eerste N video's verwerken")
    parser.add_argument("--force", action="store_true", help="Bestaande transcripts opnieuw downloaden")
    parser.add_argument("--cookies", help="cookies.txt (Netscape-formaat) voor de playlist-lookup")
    parser.add_argument("--webshare-user", default=os.environ.get("WEBSHARE_PROXY_USERNAME"),
                        help="Webshare 'Residential' proxy username (of env WEBSHARE_PROXY_USERNAME)")
    parser.add_argument("--webshare-pass", default=os.environ.get("WEBSHARE_PROXY_PASSWORD"),
                        help="Webshare 'Residential' proxy password (of env WEBSHARE_PROXY_PASSWORD)")
    parser.add_argument("--proxy", default=os.environ.get("TRANSCRIPT_PROXY"),
                        help="Andere HTTP(S)-proxy, bv. http://user:pass@host:port (of env TRANSCRIPT_PROXY)")
    parser.add_argument("--tor", action="store_true",
                        help="Via een lokale Tor-proxy, met een nieuw circuit (ander IP) bij elke blokkade")
    parser.add_argument("--tor-port", type=int, default=9050, help="SOCKS-poort van Tor (default: 9050)")
    parser.add_argument("--tor-rotations", type=int, default=20,
                        help="Max. aantal nieuwe Tor-circuits per video (default: 20)")
    parser.add_argument("--cooldown", type=float, default=300,
                        help="Seconden wachten na een IP-blokkade voor een nieuwe poging (default: 300)")
    parser.add_argument("--retries", type=int, default=3,
                        help="Aantal pogingen na een IP-blokkade, met verdubbelende wachttijd (default: 3, 0 = meteen stoppen)")
    args = parser.parse_args()
    if args.tor:
        check_tor(args.tor_port)

    playlist = args.playlist
    if playlist and not playlist.startswith("http"):
        playlist = f"https://www.youtube.com/playlist?list={playlist}"
    if not playlist:
        playlist = find_playlist(args.channel, args.match, args.cookies)

    title, videos = list_videos(playlist, args.cookies)
    log(f"Playlist: {title} — {len(videos)} video's")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Number the whole playlist, also with --limit, so numbers follow playlist order.
    assign_numbers(out, videos)
    if args.limit:
        videos = videos[: args.limit]
    fetcher = Fetcher(args)
    log(f"Proxy: {fetcher.describe()}")
    extensions = list(dict.fromkeys(["json", args.format]))
    rows = []
    counts = {"ok": 0, "skipped": 0, "missing": 0, "error": 0}

    for n, video in enumerate(videos, start=1):
        stem = f"{video.number:03d}-{slugify(video.title)}-{video.id}"
        paths = {ext: out / f"{stem}.{ext}" for ext in extensions}
        row = {"number": video.number, "position": video.position, "video_id": video.id, "title": video.title, "url": video.url,
               "file": paths["json"].name}
        prefix = f"[{n}/{len(videos)}] {video.title}"

        # Match on video ID, not on the full name: a title can change, and files from
        # older versions of this script may carry a different number.
        existing = {ext: sorted(out.glob(f"*-{video.id}.{ext}")) for ext in extensions}
        if all(existing.values()) and not args.force:
            for ext, files in existing.items():
                if files[0] != paths[ext]:
                    files[0].rename(paths[ext])
                    log(f"{prefix} — hernoemd: {files[0].name} -> {paths[ext].name}")
                for duplicate in files[1:]:
                    duplicate.unlink()
                    log(f"{prefix} — dubbel bestand verwijderd: {duplicate.name}")
            counts["skipped"] += 1
            rows.append({**row, "status": "skipped", "language": ""})
            log(f"{prefix} — bestaat al")
            continue

        try:
            fetched = fetcher.fetch(video.id, prefix)
        except (IpBlocked, RequestBlocked):
            log(f"{prefix} — nog steeds geblokkeerd door YouTube, stop. "
                "Gebruik een proxy (--tor, --webshare-user/--webshare-pass of --proxy) of probeer later opnieuw.")
            counts["error"] += 1
            break
        except CouldNotRetrieveTranscript as exc:
            status = "missing" if isinstance(exc, (NoTranscriptFound, TranscriptsDisabled)) else "error"
            counts[status] += 1
            rows.append({**row, "status": status, "language": "", "file": ""})
            log(f"{prefix} — geen transcript ({type(exc).__name__})")
            continue

        for ext, path in paths.items():
            if ext == "json":
                body = structured_json(video, title, playlist, fetched)
            else:
                body = FORMATTERS[ext].format_transcript(fetched)
                if ext == "txt":
                    header = f"# {video.title}\n# {video.url}\n# language: {fetched.language_code}"
                    header += " (auto-generated)\n\n" if fetched.is_generated else "\n\n"
                    body = header + body
            path.write_text(body + "\n", encoding="utf-8")
            for stale in existing[ext]:
                if stale != path:
                    stale.unlink()
        counts["ok"] += 1
        rows.append({**row, "status": "ok", "language": fetched.language_code})
        log(f"{prefix} — ok ({fetched.language_code}{', auto' if fetched.is_generated else ''})")
        if n < len(videos):
            time.sleep(args.delay)

    with (out / "index.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["number", "position", "video_id", "title", "url", "file", "status", "language"])
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
