from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_AUDIO_EXTENSIONS = {
    ".flac",
    ".mp3",
    ".m4a",
    ".ogg",
    ".opus",
    ".wav",
    ".aac",
}


@dataclass
class SpotiFlacDownloadResult:
    file_path: str
    codec: str
    bitrate: int
    provider: str = "spotiflac"


def _quality_to_bitrate(quality: str, codec: str) -> int:
    quality = (quality or "high").lower()
    codec = (codec or "mp3").lower()

    if codec == "flac" or quality == "lossless":
        return 1411
    if quality == "high":
        return 320
    if quality == "medium":
        return 192
    return 128


class SpotiFlacWorker:
    """
    Managed SpotiFLAC command wrapper used by the download job worker.
    """

    def __init__(self) -> None:
        self.binary = os.getenv("SPOTIFLAC_BIN", "spotiflac")
        self.command_template = os.getenv(
            "SPOTIFLAC_CMD_TEMPLATE",
            '{bin} "{url}" --output "{output_dir}" --format "{codec}" --quality "{quality}"',
        )
        self.timeout_seconds = int(os.getenv("SPOTIFLAC_TIMEOUT_SECONDS", "3600"))

    def is_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _list_audio_files(self, output_dir: str) -> set[Path]:
        directory = Path(output_dir)
        if not directory.exists():
            return set()

        files: set[Path] = set()
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                files.add(path.resolve())
        return files

    def _build_command(
        self,
        *,
        url: str,
        output_dir: str,
        codec: str,
        quality: str,
    ) -> list[str]:
        command = self.command_template.format(
            bin=self.binary,
            url=url,
            output_dir=output_dir,
            codec=codec,
            quality=quality,
        )
        return shlex.split(command)

    def download(
        self,
        *,
        source_url: str,
        output_dir: str,
        codec: str,
        quality: str,
        item_type: str,
        target_path: str | None = None,
    ) -> SpotiFlacDownloadResult:
        if not source_url:
            raise RuntimeError("SpotiFLAC download requires source_url")

        if not self.is_available():
            raise RuntimeError(
                "SpotiFLAC binary is not available. Set SPOTIFLAC_BIN or install spotiflac."
            )

        os.makedirs(output_dir, exist_ok=True)
        before = self._list_audio_files(output_dir)

        command = self._build_command(
            url=source_url,
            output_dir=output_dir,
            codec=codec,
            quality=quality,
        )

        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if process.returncode != 0:
            error_message = (
                process.stderr.strip()
                or process.stdout.strip()
                or "SpotiFLAC command failed"
            )
            raise RuntimeError(error_message)

        if target_path and Path(target_path).exists():
            resolved = str(Path(target_path).resolve())
            return SpotiFlacDownloadResult(
                file_path=resolved,
                codec=Path(resolved).suffix.lstrip(".") or codec,
                bitrate=_quality_to_bitrate(quality, codec),
            )

        after = self._list_audio_files(output_dir)
        new_files = list(after - before)

        if not new_files:
            # Some providers overwrite in place. Fall back to newest file in output directory.
            new_files = list(after)

        if not new_files:
            raise RuntimeError("SpotiFLAC finished without producing audio files")

        newest = max(
            new_files,
            key=lambda path: path.stat().st_mtime if path.exists() else time.time(),
        )
        resolved = str(newest.resolve())
        resolved_codec = newest.suffix.lstrip(".") or codec

        # For non-track jobs (album/artist/playlist) we keep the job target at directory level.
        final_path = resolved if item_type == "track" else output_dir

        return SpotiFlacDownloadResult(
            file_path=final_path,
            codec=resolved_codec,
            bitrate=_quality_to_bitrate(quality, resolved_codec),
        )


spotiflac_worker = SpotiFlacWorker()
