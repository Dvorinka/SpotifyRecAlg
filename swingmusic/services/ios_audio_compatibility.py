"""
iOS Audio Compatibility Service for SwingMusic
Handles iOS-specific audio playback issues and format compatibility
"""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swingmusic import logger
from swingmusic.utils.files import guess_mime_type


@dataclass
class IOSAudioCapabilities:
    """iOS device audio capabilities"""

    is_safari: bool
    is_ios: bool
    supports_flac: bool
    supports_webm: bool
    supports_alac: bool
    supports_aac: bool
    user_agent: str
    optimal_format: str
    optimal_codec: str


class IOSAudioManager:
    """Manages iOS audio compatibility and transcoding"""

    def __init__(self):
        self.temp_dir = tempfile.gettempdir()
        self.transcode_cache = {}

    def detect_ios_capabilities(self, user_agent: str = "") -> IOSAudioCapabilities:
        """Detect iOS device capabilities from user agent"""
        is_safari = "Safari" in user_agent and "Chrome" not in user_agent
        is_ios = bool(re.search(r"iPad|iPhone|iPod", user_agent))

        # iOS format support matrix
        supports_flac = False  # iOS doesn't support FLAC natively
        supports_webm = False  # Limited WebM support on iOS
        supports_alac = True  # Apple Lossless supported on iOS
        supports_aac = True  # AAC widely supported

        # Determine optimal format for iOS
        if is_ios:
            if supports_alac:
                optimal_format = "mp4"  # ALAC in MP4 container
                optimal_codec = "alac"
            else:
                optimal_format = "mp4"  # AAC in MP4 container
                optimal_codec = "aac"
        else:
            optimal_format = "flac"  # Use original format for non-iOS
            optimal_codec = "flac"

        return IOSAudioCapabilities(
            is_safari=is_safari,
            is_ios=is_ios,
            supports_flac=supports_flac,
            supports_webm=supports_webm,
            supports_alac=supports_alac,
            supports_aac=supports_aac,
            user_agent=user_agent,
            optimal_format=optimal_format,
            optimal_codec=optimal_codec,
        )

    def needs_transcoding(
        self, file_path: str, capabilities: IOSAudioCapabilities
    ) -> bool:
        """Check if file needs transcoding for iOS compatibility"""
        if not capabilities.is_ios:
            return False

        original_mime = guess_mime_type(file_path)

        # iOS doesn't support FLAC, need transcoding
        if original_mime == "audio/flac" and not capabilities.supports_flac:
            return True

        # iOS has limited WebM support
        return bool(original_mime == "audio/webm" and not capabilities.supports_webm)

    def get_optimal_audio_format(
        self, file_path: str, capabilities: IOSAudioCapabilities
    ) -> tuple[str, str]:
        """Get optimal audio format and codec for the device"""
        if not capabilities.is_ios:
            # Return original format for non-iOS devices
            original_mime = guess_mime_type(file_path)
            if original_mime == "audio/flac":
                return "flac", "flac"
            elif original_mime == "audio/mpeg":
                return "mp3", "mp3"
            else:
                return "mp4", "aac"

        # Return iOS-optimized format
        return capabilities.optimal_format, capabilities.optimal_codec

    def transcode_for_ios(
        self, file_path: str, capabilities: IOSAudioCapabilities, quality: str = "high"
    ) -> str | None:
        """Transcode audio file for iOS compatibility"""
        try:
            # Check if already transcoded
            cache_key = f"{file_path}_{capabilities.optimal_format}_{quality}"
            if cache_key in self.transcode_cache:
                cached_file = self.transcode_cache[cache_key]
                if os.path.exists(cached_file):
                    return cached_file

            # Create output file path
            input_path = Path(file_path)
            output_filename = f"{input_path.stem}_ios_{capabilities.optimal_format}.{capabilities.optimal_format}"
            output_path = os.path.join(self.temp_dir, output_filename)

            # Prepare FFmpeg command based on target format
            if capabilities.optimal_codec == "alac":
                # Apple Lossless Audio Codec
                cmd = [
                    "ffmpeg",
                    "-i",
                    file_path,
                    "-c:a",
                    "alac",
                    "-ar",
                    "44100",  # Sample rate
                    "-ac",
                    "2",  # Stereo
                    "-y",
                    output_path,
                ]
            elif capabilities.optimal_codec == "aac":
                # AAC codec with quality settings
                bitrate_map = {
                    "low": "96k",
                    "medium": "128k",
                    "high": "256k",
                    "lossless": "320k",
                }
                bitrate = bitrate_map.get(quality, "256k")

                cmd = [
                    "ffmpeg",
                    "-i",
                    file_path,
                    "-c:a",
                    "aac",
                    "-b:a",
                    bitrate,
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-y",
                    output_path,
                ]
            else:
                # Default to AAC
                cmd = [
                    "ffmpeg",
                    "-i",
                    file_path,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "256k",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-y",
                    output_path,
                ]

            # Execute transcoding
            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0 and os.path.exists(output_path):
                # Cache the transcoded file
                self.transcode_cache[cache_key] = output_path
                logger.info(
                    f"Successfully transcoded {file_path} for iOS: {output_path}"
                )
                return output_path
            else:
                logger.error(f"FFmpeg transcoding failed: {result.stderr}")
                return None

        except Exception as e:
            logger.error(f"Error transcoding for iOS: {e}")
            return None

    def get_ios_compatible_mime_type(
        self, file_path: str, capabilities: IOSAudioCapabilities
    ) -> str:
        """Get iOS-compatible MIME type"""
        if not capabilities.is_ios:
            return guess_mime_type(file_path)

        if capabilities.optimal_format == "mp4":
            if capabilities.optimal_codec == "alac":
                return "audio/mp4"  # ALAC in MP4 container
            else:
                return "audio/mp4"  # AAC in MP4 container
        elif capabilities.optimal_format == "mp3":
            return "audio/mpeg"
        else:
            return "audio/mp4"  # Default to MP4 container for iOS

    def create_ios_audio_source(
        self, file_path: str, capabilities: IOSAudioCapabilities, quality: str = "high"
    ) -> dict[str, Any]:
        """Create iOS-compatible audio source configuration"""
        source_config = {
            "file_path": file_path,
            "needs_transcoding": self.needs_transcoding(file_path, capabilities),
            "mime_type": self.get_ios_compatible_mime_type(file_path, capabilities),
            "format": capabilities.optimal_format,
            "codec": capabilities.optimal_codec,
        }

        if source_config["needs_transcoding"]:
            transcoded_path = self.transcode_for_ios(file_path, capabilities, quality)
            if transcoded_path:
                source_config["transcoded_path"] = transcoded_path
                source_config["file_path"] = transcoded_path
            else:
                # Fallback to original file if transcoding fails
                logger.warning(f"Transcoding failed, using original file: {file_path}")
                source_config["needs_transcoding"] = False
                source_config["mime_type"] = guess_mime_type(file_path)

        return source_config

    def cleanup_transcoded_files(self):
        """Clean up temporary transcoded files"""
        try:
            for cached_file in self.transcode_cache.values():
                if os.path.exists(cached_file):
                    os.remove(cached_file)
            self.transcode_cache.clear()
        except Exception as e:
            logger.error(f"Error cleaning up transcoded files: {e}")


# Global iOS audio manager instance
ios_audio_manager = IOSAudioManager()
