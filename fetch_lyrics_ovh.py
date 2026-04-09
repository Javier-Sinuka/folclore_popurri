#!/usr/bin/env python3
"""Complete repertorio_folclore.qd using lyrics.ovh.

Flow:
1. Read song titles from `## ...` headings in the Quarkdown file.
2. Read the Spotify public embed page to recover title -> artist mappings.
3. Query `https://api.lyrics.ovh/v1/{artist}/{title}` first.
4. If that fails, query `https://api.lyrics.ovh/suggest/{term}` and retry with
   a few candidates.
5. Replace `_Espacio para la letra._` under each heading with the fetched lyrics.

Usage:
    python3 fetch_lyrics_ovh.py --dry-run
    python3 fetch_lyrics_ovh.py --write
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_QD_PATH = PROJECT_ROOT / "repertorio_folclore.qd"
DEFAULT_ALIASES_PATH = PROJECT_ROOT / "lyrics_aliases.json"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "lyrics_report.json"
DEFAULT_PLAYLIST_URL = "https://open.spotify.com/embed/playlist/1WCHhiYkdFXvQ2Hfu2O94U"
LYRICS_BASE = "https://api.lyrics.ovh/v1"
SUGGEST_BASE = "https://api.lyrics.ovh/suggest"
PLACEHOLDER = "_Espacio para la letra._"
HTTP_TIMEOUT_SECONDS = 10


@dataclass
class TrackInfo:
    title: str
    artist: str


def load_aliases(path: Path) -> dict[str, list[TrackInfo]]:
    if not path.exists():
        return {}

    raw = json.loads(path.read_text())
    aliases: dict[str, list[TrackInfo]] = {}
    for song_title, items in raw.items():
        normalized_items = items if isinstance(items, list) else [items]
        alias_tracks: list[TrackInfo] = []
        for item in normalized_items:
            artist = item.get("artist")
            title = item.get("title")
            if artist and title:
                alias_tracks.append(TrackInfo(title=title, artist=artist))
        if alias_tracks:
            aliases[song_title] = alias_tracks
    return aliases


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def extract_titles_from_qd(text: str) -> list[str]:
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


def parse_spotify_embed(playlist_url: str) -> dict[str, str]:
    html = fetch_text(playlist_url)
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
    if not match:
        raise RuntimeError("No se pudo encontrar __NEXT_DATA__ en el embed de Spotify.")

    data = json.loads(match.group(1))
    track_list = data["props"]["pageProps"]["state"]["data"]["entity"]["trackList"]
    mapping: dict[str, str] = {}
    for item in track_list:
        mapping.setdefault(item["title"], item["subtitle"].split(",")[0].replace("\xa0", " ").strip())
    return mapping


def strip_accents(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )


def normalize_key(text: str) -> str:
    text = strip_accents(text).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def comparable_title(text: str) -> str:
    text = re.sub(r"\s*-\s*(en vivo|remastered \d{4}|remasterizado.*)$", "", text, flags=re.I)
    text = re.sub(r"\s*\((with .*?)\)$", "", text, flags=re.I)
    return normalize_key(text)


def title_variants(title: str) -> list[str]:
    variants: list[str] = []

    def add(value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip(" -")
        if value and value not in variants:
            variants.append(value)

    add(title)
    add(re.sub(r"\s*-\s*(En Vivo|Remastered \d{4}|Remasterizado.*)$", "", title, flags=re.I))
    add(re.sub(r"\s*\((with .*?)\)$", "", title, flags=re.I))
    add(title.replace("Jose", "José"))
    add(title.replace("José", "Jose"))
    add(strip_accents(title))
    return variants


def fetch_lyrics(artist: str, title: str) -> str | None:
    url = f"{LYRICS_BASE}/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}"
    try:
        payload = fetch_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    lyrics = payload.get("lyrics", "").strip()
    return lyrics or None


def suggest_candidates(search_term: str, limit: int = 5) -> list[TrackInfo]:
    url = f"{SUGGEST_BASE}/{urllib.parse.quote(search_term)}"
    try:
        payload = fetch_json(url)
    except urllib.error.HTTPError:
        return []

    candidates: list[TrackInfo] = []
    for item in payload.get("data", [])[:limit]:
        artist = item.get("artist", {}).get("name")
        title = item.get("title")
        if artist and title:
            candidates.append(TrackInfo(title=title, artist=artist))
    return candidates


def candidate_matches_title(requested_title: str, candidate_title: str) -> bool:
    requested = comparable_title(requested_title)
    candidate = comparable_title(candidate_title)
    if not requested or not candidate:
        return False
    if requested == candidate:
        return True
    if requested in candidate or candidate in requested:
        return True

    ratio = difflib.SequenceMatcher(a=requested, b=candidate).ratio()
    requested_tokens = {token for token in requested.split() if len(token) >= 4}
    candidate_tokens = set(candidate.split())
    overlap = len(requested_tokens & candidate_tokens)
    return ratio >= 0.82 or (requested_tokens and overlap >= max(1, len(requested_tokens) - 1))


def find_lyrics(
    title: str,
    artist: str | None,
    aliases: dict[str, list[TrackInfo]],
) -> tuple[str | None, str]:
    attempted: list[str] = []

    for alias in aliases.get(title, []):
        attempted.append(f"{alias.artist} / {alias.title}")
        lyrics = fetch_lyrics(alias.artist, alias.title)
        if lyrics:
            return lyrics, f"alias:{alias.artist} / {alias.title}"

    if artist:
        for variant in title_variants(title):
            attempted.append(f"{artist} / {variant}")
            lyrics = fetch_lyrics(artist, variant)
            if lyrics:
                return lyrics, f"direct:{artist} / {variant}"

    search_terms = [f"{title} {artist}".strip()]
    for variant in title_variants(title):
        if variant != title:
            search_terms.append(f"{variant} {artist or ''}".strip())

    seen: set[tuple[str, str]] = set()
    for term in search_terms:
        for candidate in suggest_candidates(term):
            if not candidate_matches_title(title, candidate.title):
                continue
            pair = (candidate.artist, candidate.title)
            if pair in seen:
                continue
            seen.add(pair)
            attempted.append(f"{candidate.artist} / {candidate.title}")
            lyrics = fetch_lyrics(candidate.artist, candidate.title)
            if lyrics:
                return lyrics, f"suggest:{candidate.artist} / {candidate.title}"

    return None, "failed:" + " | ".join(attempted[:10])


def replace_placeholder_blocks(text: str, lyrics_by_title: dict[str, str]) -> str:
    lines = text.splitlines()
    current_title: str | None = None
    out: list[str] = []

    for line in lines:
        if line.startswith("## "):
            current_title = line[3:].strip()
            out.append(line)
            continue

        if line == PLACEHOLDER and current_title in lyrics_by_title:
            out.append(lyrics_by_title[current_title])
            continue

        out.append(line)

    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Inserta letras usando lyrics.ovh.")
    parser.add_argument("--qd-path", type=Path, default=DEFAULT_QD_PATH)
    parser.add_argument("--aliases-path", type=Path, default=DEFAULT_ALIASES_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--playlist-url", default=DEFAULT_PLAYLIST_URL)
    parser.add_argument("--write", action="store_true", help="Escribe cambios en el archivo.")
    parser.add_argument("--dry-run", action="store_true", help="No modifica el archivo.")
    parser.add_argument("--limit", type=int, default=0, help="Procesa solo N temas.")
    parser.add_argument("--sleep", type=float, default=0.2, help="Pausa entre requests.")
    args = parser.parse_args()

    if not args.write and not args.dry_run:
        parser.error("Indicá --write o --dry-run.")

    original = args.qd_path.read_text()
    titles = extract_titles_from_qd(original)
    aliases = load_aliases(args.aliases_path)
    spotify_map = parse_spotify_embed(args.playlist_url)
    normalized_spotify_map = {normalize_key(title): artist for title, artist in spotify_map.items()}

    if args.limit > 0:
        titles = titles[: args.limit]

    collected: dict[str, str] = {}
    report_items: list[dict[str, object]] = []
    found = 0
    missing = 0

    for index, title in enumerate(titles, start=1):
        artist = spotify_map.get(title) or normalized_spotify_map.get(normalize_key(title))
        lyrics, source = find_lyrics(title, artist, aliases)
        if lyrics:
            collected[title] = lyrics
            found += 1
            status = "OK"
        else:
            missing += 1
            status = "MISS"
        report_items.append(
            {
                "title": title,
                "artist": artist,
                "status": status,
                "source": source,
                "lyrics_found": bool(lyrics),
            }
        )
        print(f"[{index:02d}/{len(titles):02d}] {status} {title} :: {source}", flush=True)
        time.sleep(args.sleep)

    print(f"\nEncontradas: {found}", flush=True)
    print(f"Sin resultado: {missing}", flush=True)

    args.report_path.write_text(
        json.dumps(
            {
                "found": found,
                "missing": missing,
                "items": report_items,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Reporte escrito en: {args.report_path}", flush=True)

    if args.write:
        updated = replace_placeholder_blocks(original, collected)
        args.qd_path.write_text(updated)
        print(f"\nArchivo actualizado: {args.qd_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
