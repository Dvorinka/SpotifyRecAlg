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
class AdapterDownloadResult:
    file_path: str
    codec: str
    bitrate: int
    provider: str


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


class CommandFallbackAdapter:
    """
    Generic command adapter used as fallback when the primary SpotiFLAC
    provider is not available or fails.

    Configure with:
    - SWINGMUSIC_FALLBACK_DOWNLOAD_CMD
      Default: disabled.
      Example:
      '{url}' -> source URL
      '{output_dir}' -> destination directory
      '{codec}' / '{quality}' / '{item_type}' / '{target_path}'
    """

    def __init__(self) -> None:
        self.name = os.getenv("SWINGMUSIC_FALLBACK_PROVIDER_NAME", "fallback-command")
        self.command_template = os.getenv(
            "SWINGMUSIC_FALLBACK_DOWNLOAD_CMD", ""
        ).strip()
        self.timeout_seconds = int(
            os.getenv("SWINGMUSIC_FALLBACK_TIMEOUT_SECONDS", "3600")
        )

    def is_available(self) -> bool:
        if not self.command_template:
            return False

        try:
            command = shlex.split(self.command_template)
        except ValueError:
            return False

        if not command:
            return False

        executable = command[0]
        return shutil.which(executable) is not None

    @staticmethod
    def _list_audio_files(output_dir: str) -> set[Path]:
        directory = Path(output_dir)
        if not directory.exists():
            return set()

        files: set[Path] = set()
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                files.add(path.resolve())
        return files

    def _build_command(
        self,
        *,
        source_url: str,
        output_dir: str,
        codec: str,
        quality: str,
        item_type: str,
        target_path: str | None,
    ) -> list[str]:
        command = self.command_template.format(
            url=source_url,
            output_dir=output_dir,
            codec=codec,
            quality=quality,
            item_type=item_type,
            target_path=target_path or "",
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
    ) -> AdapterDownloadResult:
        if not source_url:
            raise RuntimeError("Fallback adapter requires source_url")

        if not self.is_available():
            raise RuntimeError(
                "Fallback adapter command is not configured or unavailable"
            )

        os.makedirs(output_dir, exist_ok=True)
        before = self._list_audio_files(output_dir)
        command = self._build_command(
            source_url=source_url,
            output_dir=output_dir,
            codec=codec,
            quality=quality,
            item_type=item_type,
            target_path=target_path,
        )

        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if process.returncode != 0:
            err = (
                process.stderr.strip()
                or process.stdout.strip()
                or "Fallback command failed"
            )
            raise RuntimeError(err)

        if target_path and Path(target_path).exists():
            resolved = str(Path(target_path).resolve())
            return AdapterDownloadResult(
                file_path=resolved if item_type == "track" else output_dir,
                codec=Path(resolved).suffix.lstrip(".") or codec,
                bitrate=_quality_to_bitrate(quality, codec),
                provider=self.name,
            )

        after = self._list_audio_files(output_dir)
        new_files = list(after - before)
        if not new_files:
            new_files = list(after)
        if not new_files:
            raise RuntimeError(
                "Fallback adapter finished without producing audio files"
            )

        newest = max(
            new_files,
            key=lambda path: path.stat().st_mtime if path.exists() else time.time(),
        )
        resolved = str(newest.resolve())
        resolved_codec = newest.suffix.lstrip(".") or codec

        return AdapterDownloadResult(
            file_path=resolved if item_type == "track" else output_dir,
            codec=resolved_codec,
            bitrate=_quality_to_bitrate(quality, resolved_codec),
            provider=self.name,
        )


fallback_download_adapter = CommandFallbackAdapter()
