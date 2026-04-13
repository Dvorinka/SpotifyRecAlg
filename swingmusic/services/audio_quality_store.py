"""Lightweight persistence and helpers for audio quality preferences."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from sqlalchemy import text

from swingmusic.db.engine import DbEngine

DEFAULT_AUDIO_SETTINGS: dict[str, Any] = {
    "streaming_quality": "high",
    "adaptive_quality": True,
    "network_aware_quality": True,
    "device_specific_quality": True,
    "download_format": "flac",
    "download_bitrate": None,
    "download_sample_rate": "44.1kHz",
    "download_bit_depth": "16bit",
    "enable_loudness_normalization": True,
    "target_loudness": -14.0,
    "enable_adaptive_eq": True,
    "enable_spatial_audio_processing": False,
    "spatial_audio_format": "stereo",
    "enable_crossfade": False,
    "crossfade_duration": 2.0,
    "enable_gapless_playback": True,
    "enable_replaygain": True,
    "prioritize_fidelity": True,
    "prioritize_file_size": False,
    "prioritize_compatibility": False,
    "custom_ffmpeg_params": {},
    "enable_experimental_codecs": False,
    "cache_transcoded_files": True,
}

AUDIO_PRESETS: dict[str, dict[str, Any]] = {
    "audiophile": {
        "streaming_quality": "lossless",
        "download_format": "flac",
        "download_sample_rate": "96kHz",
        "download_bit_depth": "24bit",
        "prioritize_fidelity": True,
    },
    "portable": {
        "streaming_quality": "high",
        "download_format": "aac_256",
        "adaptive_quality": True,
        "network_aware_quality": True,
    },
    "data_saver": {
        "streaming_quality": "data_saver",
        "download_format": "mp3_128",
        "prioritize_file_size": True,
        "prioritize_fidelity": False,
    },
    "studio": {
        "streaming_quality": "lossless",
        "download_format": "wav",
        "download_sample_rate": "192kHz",
        "download_bit_depth": "32bit",
        "prioritize_fidelity": True,
    },
    "gaming": {
        "streaming_quality": "medium",
        "download_format": "mp3_256",
        "enable_crossfade": False,
        "enable_gapless_playback": True,
    },
    "podcast": {
        "streaming_quality": "medium",
        "download_format": "aac_128",
        "target_loudness": -16.0,
        "enable_adaptive_eq": True,
    },
}

SUPPORTED_FORMATS = [
    "flac",
    "alac",
    "wav",
    "mp3_320",
    "mp3_256",
    "mp3_192",
    "mp3_128",
    "aac_256",
    "aac_192",
    "aac_128",
    "ogg_vorbis",
    "ogg_opus",
]


class AudioQualityStore:
    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self):
        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS audio_quality_settings (
                        user_id INTEGER PRIMARY KEY,
                        settings_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

    def _normalize_settings(self, incoming: dict[str, Any]) -> dict[str, Any]:
        settings = deepcopy(DEFAULT_AUDIO_SETTINGS)
        for key, value in incoming.items():
            if key not in settings:
                continue
            settings[key] = value

        if settings["streaming_quality"] not in {
            "lossless",
            "high",
            "medium",
            "low",
            "data_saver",
        }:
            settings["streaming_quality"] = DEFAULT_AUDIO_SETTINGS["streaming_quality"]

        if not isinstance(settings["custom_ffmpeg_params"], dict):
            settings["custom_ffmpeg_params"] = {}

        return settings

    def get_settings(self, user_id: int) -> dict[str, Any]:
        with DbEngine.manager() as session:
            row = (
                session.execute(
                    text(
                        """
                    SELECT settings_json
                    FROM audio_quality_settings
                    WHERE user_id = :user_id
                    """
                    ),
                    {"user_id": int(user_id)},
                )
                .mappings()
                .first()
            )

        if not row:
            return deepcopy(DEFAULT_AUDIO_SETTINGS)

        try:
            raw = json.loads(row["settings_json"])
            if not isinstance(raw, dict):
                return deepcopy(DEFAULT_AUDIO_SETTINGS)
            return self._normalize_settings(raw)
        except json.JSONDecodeError:
            return deepcopy(DEFAULT_AUDIO_SETTINGS)

    def save_settings(self, user_id: int, settings: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_settings(settings)
        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    INSERT INTO audio_quality_settings (user_id, settings_json, updated_at)
                    VALUES (:user_id, :settings_json, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        settings_json = excluded.settings_json,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "user_id": int(user_id),
                    "settings_json": json.dumps(normalized),
                },
            )

        return normalized

    def update_settings(self, user_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings(user_id)
        current.update(patch)
        return self.save_settings(user_id, current)

    def apply_preset(
        self, user_id: int, preset_name: str
    ) -> tuple[dict[str, Any] | None, bool]:
        preset = AUDIO_PRESETS.get(preset_name)
        if preset is None:
            return None, False

        settings = self.update_settings(user_id, preset)
        return settings, True

    def get_presets(self) -> list[dict[str, Any]]:
        return [{"key": key, "settings": value} for key, value in AUDIO_PRESETS.items()]

    def get_supported_formats(self) -> list[str]:
        return SUPPORTED_FORMATS[:]

    def get_network_status(self) -> dict[str, Any]:
        # Keep deterministic and cheap. A dedicated bandwidth probe can be added later.
        return {
            "speed": 0,
            "quality": "unknown",
            "metered": False,
            "latency_ms": None,
        }

    def get_device_info(self, user_agent: str) -> dict[str, Any]:
        ua = (user_agent or "").lower()

        if any(token in ua for token in ("iphone", "android", "mobile")):
            device_type = "mobile"
        elif any(token in ua for token in ("ipad", "tablet")):
            device_type = "tablet"
        else:
            device_type = "desktop"

        if "windows" in ua:
            os_name = "windows"
        elif "mac os" in ua or "macintosh" in ua:
            os_name = "macos"
        elif "linux" in ua:
            os_name = "linux"
        elif "android" in ua:
            os_name = "android"
        elif "iphone" in ua or "ipad" in ua:
            os_name = "ios"
        else:
            os_name = "unknown"

        return {
            "type": device_type,
            "os": os_name,
            "supports_lossless": device_type in {"desktop", "tablet"},
            "supports_spatial_audio": device_type != "unknown",
        }

    def get_optimal_streaming_quality(
        self, user_id: int, context: dict[str, Any] | None = None
    ) -> str:
        settings = self.get_settings(user_id)
        preferred = settings.get("streaming_quality", "high")

        context = context or {}
        battery_low = bool(context.get("battery_low"))
        network_quality = str(context.get("network_quality") or "")

        if battery_low and preferred == "lossless":
            return "high"

        if network_quality in {"poor", "slow"}:
            return "medium" if preferred in {"lossless", "high"} else preferred

        return preferred


audio_quality_store = AudioQualityStore()
