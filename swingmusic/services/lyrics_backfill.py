from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from swingmusic.db.libdata import TrackTable
from swingmusic.db.production import LyricsStatusTable
from swingmusic.lib.lyrics import get_lyrics_from_tags
from swingmusic.plugins.lyrics import Lyrics

SUPPORTED_EMBED_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
}


def _read_lrc(filepath: str) -> str | None:
    lrc_path = Path(filepath).with_suffix(".lrc")
    if not lrc_path.exists():
        return None
    try:
        return lrc_path.read_text(encoding="utf-8")
    except Exception:
        return None


def _has_embedded_lyrics(trackhash: str | None) -> bool:
    if not trackhash:
        return False
    try:
        lyrics = get_lyrics_from_tags(trackhash)
        return bool(lyrics)
    except Exception:
        return False


def _embed_lyrics_with_ffmpeg(filepath: str, lyrics_text: str) -> bool:
    source = Path(filepath)
    if source.suffix.lower() not in SUPPORTED_EMBED_EXTENSIONS:
        return False
    if not shutil.which("ffmpeg"):
        return False
    if not lyrics_text or not lyrics_text.strip():
        return False

    temp_dir = tempfile.mkdtemp(prefix="swingmusic-lyrics-")
    temp_path = Path(temp_dir) / source.name

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-map",
        "0",
        "-c",
        "copy",
        "-metadata",
        f"lyrics={lyrics_text}",
        str(temp_path),
    ]

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0 or not temp_path.exists():
            return False

        os.replace(temp_path, source)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def backfill_lyrics_for_track(
    *,
    filepath: str,
    title: str | None,
    artist: str | None,
    album: str | None = None,
    trackhash: str | None = None,
) -> None:
    if not filepath:
        return

    if not os.path.exists(filepath):
        if trackhash:
            LyricsStatusTable.upsert(
                trackhash=trackhash,
                filepath=filepath,
                status="failed",
                source="download",
                last_error="audio_file_missing",
                increment_attempt=True,
            )
        return

    has_embedded = _has_embedded_lyrics(trackhash)
    lrc_text = _read_lrc(filepath)

    # Keep existing embedded lyrics as canonical when present.
    if has_embedded and trackhash:
        LyricsStatusTable.upsert(
            trackhash=trackhash,
            filepath=filepath,
            status="embedded",
            source="tags",
            has_embedded=True,
            has_lrc=bool(lrc_text),
            last_error=None,
            extra={"strategy": "existing_embedded"},
        )
        return

    if not lrc_text and title and artist:
        try:
            plugin = Lyrics()
            if getattr(plugin, "enabled", False):
                lrc_text = plugin.download_lyrics_by_metadata(
                    title=title,
                    artist=artist,
                    album=album or "",
                    path=filepath,
                )
        except Exception as error:
            if trackhash:
                LyricsStatusTable.upsert(
                    trackhash=trackhash,
                    filepath=filepath,
                    status="failed",
                    source="download",
                    has_embedded=False,
                    has_lrc=False,
                    last_error=str(error),
                    increment_attempt=True,
                )
            return

    if not lrc_text:
        if trackhash:
            LyricsStatusTable.upsert(
                trackhash=trackhash,
                filepath=filepath,
                status="missing",
                source="download",
                has_embedded=False,
                has_lrc=False,
                last_error="lyrics_not_found",
                increment_attempt=True,
            )
        return

    embedded = _embed_lyrics_with_ffmpeg(filepath, lrc_text)

    if trackhash:
        LyricsStatusTable.upsert(
            trackhash=trackhash,
            filepath=filepath,
            status="embedded" if embedded else "lrc",
            source="download",
            has_embedded=embedded,
            has_lrc=True,
            last_error=None,
            extra={"strategy": "embed_and_lrc"},
            increment_attempt=True,
        )


def backfill_lyrics_async(
    *,
    filepath: str,
    title: str | None,
    artist: str | None,
    album: str | None = None,
    trackhash: str | None = None,
) -> None:
    if not filepath:
        return

    thread = threading.Thread(
        target=backfill_lyrics_for_track,
        kwargs={
            "filepath": filepath,
            "title": title,
            "artist": artist,
            "album": album,
            "trackhash": trackhash,
        },
        daemon=True,
        name="lyrics-backfill",
    )
    thread.start()


def _backfill_library_worker():
    for track in TrackTable.get_all():
        backfill_lyrics_for_track(
            filepath=track.filepath,
            title=track.title,
            artist=track.artists[0]["name"] if track.artists else "",
            album=track.album,
            trackhash=track.trackhash,
        )


def backfill_library_async() -> None:
    thread = threading.Thread(
        target=_backfill_library_worker,
        daemon=True,
        name="lyrics-library-backfill",
    )
    thread.start()
