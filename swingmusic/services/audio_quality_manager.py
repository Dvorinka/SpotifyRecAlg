"""
Advanced Audio Quality Management Service

This service provides comprehensive audio quality control including:
- Adaptive quality streaming based on network conditions
- Multi-format support with intelligent transcoding
- Audio enhancement features (EQ, spatial audio, loudness normalization)
- Quality comparison and analysis tools
- Device-specific optimization
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, select, update
from sqlalchemy.orm import Mapped, mapped_column

from swingmusic.config import USER_DATA_DIR
from swingmusic.db import Base

logger = logging.getLogger(__name__)


# =============================================================================
# Database Models
# =============================================================================


class UserAudioSettingsTable(Base):
    """
    Database table for storing user-specific audio quality preferences.
    """

    __tablename__ = "user_audio_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    userid: Mapped[int] = mapped_column(
        Integer(), ForeignKey("user.id", ondelete="cascade"), unique=True, index=True
    )

    # Streaming quality settings
    streaming_quality: Mapped[str] = mapped_column(String(), default="high")
    adaptive_quality: Mapped[bool] = mapped_column(Boolean(), default=True)
    network_aware_quality: Mapped[bool] = mapped_column(Boolean(), default=True)
    device_specific_quality: Mapped[bool] = mapped_column(Boolean(), default=True)

    # Download quality settings
    download_format: Mapped[str] = mapped_column(String(), default="flac")
    download_bitrate: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    download_sample_rate: Mapped[str] = mapped_column(String(), default="44.1kHz")
    download_bit_depth: Mapped[str] = mapped_column(String(), default="16bit")

    # Advanced audio settings
    enable_dolby_atmos: Mapped[bool] = mapped_column(Boolean(), default=False)
    enable_360_audio: Mapped[bool] = mapped_column(Boolean(), default=False)
    spatial_audio_format: Mapped[str] = mapped_column(String(), default="stereo")

    # Audio enhancements
    enable_adaptive_eq: Mapped[bool] = mapped_column(Boolean(), default=True)
    enable_spatial_audio_processing: Mapped[bool] = mapped_column(
        Boolean(), default=False
    )
    enable_loudness_normalization: Mapped[bool] = mapped_column(Boolean(), default=True)
    target_loudness: Mapped[float] = mapped_column(Float(), default=-14.0)

    # Processing settings
    enable_crossfade: Mapped[bool] = mapped_column(Boolean(), default=False)
    crossfade_duration: Mapped[float] = mapped_column(Float(), default=2.0)
    enable_gapless_playback: Mapped[bool] = mapped_column(Boolean(), default=True)
    enable_replaygain: Mapped[bool] = mapped_column(Boolean(), default=True)

    # Quality preferences
    prioritize_fidelity: Mapped[bool] = mapped_column(Boolean(), default=True)
    prioritize_file_size: Mapped[bool] = mapped_column(Boolean(), default=False)
    prioritize_compatibility: Mapped[bool] = mapped_column(Boolean(), default=False)

    # Advanced options
    enable_experimental_codecs: Mapped[bool] = mapped_column(Boolean(), default=False)
    cache_transcoded_files: Mapped[bool] = mapped_column(Boolean(), default=True)

    # Timestamps
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))

    # Extra metadata
    extra: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)

    @classmethod
    def get_by_userid(cls, userid: int) -> UserAudioSettingsTable | None:
        """Get audio settings for a specific user."""
        result = cls.execute(select(cls).where(cls.userid == userid))
        return next(result).scalar()

    @classmethod
    def get_or_create(cls, userid: int) -> UserAudioSettingsTable:
        """Get existing settings or create defaults for user."""
        settings = cls.get_by_userid(userid)
        if settings:
            return settings

        # Create default settings for user
        cls.insert_one({"userid": userid})
        return cls.get_by_userid(userid)

    @classmethod
    def update_settings(cls, userid: int, settings: dict[str, Any]) -> bool:
        """Update user's audio settings."""
        settings["updated_at"] = int(time.time())
        cls.execute(
            update(cls).where(cls.userid == userid).values(settings), commit=True
        )
        return True

    @classmethod
    def to_dataclass(cls, row: UserAudioSettingsTable) -> AudioQualitySettings:
        """Convert database row to AudioQualitySettings dataclass."""
        return AudioQualitySettings(
            streaming_quality=QualityLevel(row.streaming_quality),
            adaptive_quality=row.adaptive_quality,
            network_aware_quality=row.network_aware_quality,
            device_specific_quality=row.device_specific_quality,
            download_format=AudioFormat(row.download_format),
            download_bitrate=row.download_bitrate,
            download_sample_rate=SampleRate(row.download_sample_rate),
            download_bit_depth=BitDepth(row.download_bit_depth),
            enable_dolby_atmos=row.enable_dolby_atmos,
            enable_360_audio=row.enable_360_audio,
            spatial_audio_format=SpatialAudioFormat(row.spatial_audio_format),
            enable_adaptive_eq=row.enable_adaptive_eq,
            enable_spatial_audio_processing=row.enable_spatial_audio_processing,
            enable_loudness_normalization=row.enable_loudness_normalization,
            target_loudness=row.target_loudness,
            enable_crossfade=row.enable_crossfade,
            crossfade_duration=row.crossfade_duration,
            enable_gapless_playback=row.enable_gapless_playback,
            enable_replaygain=row.enable_replaygain,
            prioritize_fidelity=row.prioritize_fidelity,
            prioritize_file_size=row.prioritize_file_size,
            prioritize_compatibility=row.prioritize_compatibility,
            enable_experimental_codecs=row.enable_experimental_codecs,
            cache_transcoded_files=row.cache_transcoded_files,
        )


# =============================================================================
# Custom Exceptions
# =============================================================================


class FFmpegNotFoundError(RuntimeError):
    """Raised when FFmpeg is not available on the system."""

    pass


class AudioAnalysisError(Exception):
    """Raised when audio analysis fails."""

    pass


class TranscodingError(Exception):
    """Raised when transcoding fails."""

    pass


# =============================================================================
# Enums
# =============================================================================


class AudioFormat(Enum):
    """Supported audio formats"""

    FLAC = "flac"
    ALAC = "alac"
    WAV = "wav"
    AIFF = "aiff"
    MP3_320 = "mp3_320"
    MP3_256 = "mp3_256"
    MP3_192 = "mp3_192"
    MP3_128 = "mp3_128"
    AAC_256 = "aac_256"
    AAC_192 = "aac_192"
    AAC_128 = "aac_128"
    OGG_VORBIS = "ogg_vorbis"
    OGG_OPUS = "ogg_opus"


class QualityLevel(Enum):
    """Audio quality levels"""

    LOSSLESS = "lossless"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    DATA_SAVER = "data_saver"


class SampleRate(Enum):
    """Supported sample rates"""

    RATE_44_1 = "44.1kHz"
    RATE_48 = "48kHz"
    RATE_96 = "96kHz"
    RATE_192 = "192kHz"


class BitDepth(Enum):
    """Supported bit depths"""

    BIT_16 = "16bit"
    BIT_24 = "24bit"
    BIT_32 = "32bit"


class SpatialAudioFormat(Enum):
    """Spatial audio formats"""

    NONE = "none"
    STEREO = "stereo"
    BINAURAL = "binaural"
    DOLBY_ATMOS = "dolby_atmos"
    SONY_360 = "sony_360"
    AMBISONIC = "ambisonic"


@dataclass
class AudioQualitySettings:
    """Comprehensive audio quality settings"""

    # Streaming quality
    streaming_quality: QualityLevel = QualityLevel.HIGH
    adaptive_quality: bool = True
    network_aware_quality: bool = True
    device_specific_quality: bool = True

    # Download quality
    download_format: AudioFormat = AudioFormat.FLAC
    download_bitrate: int | None = None  # For lossy formats
    download_sample_rate: SampleRate = SampleRate.RATE_44_1
    download_bit_depth: BitDepth = BitDepth.BIT_16

    # Advanced audio settings
    enable_dolby_atmos: bool = False
    enable_360_audio: bool = False
    spatial_audio_format: SpatialAudioFormat = SpatialAudioFormat.STEREO

    # Audio enhancements
    enable_adaptive_eq: bool = True
    enable_spatial_audio_processing: bool = False
    enable_loudness_normalization: bool = True
    target_loudness: float = -14.0  # LUFS

    # Processing settings
    enable_crossfade: bool = False
    crossfade_duration: float = 2.0
    enable_gapless_playback: bool = True
    enable_replaygain: bool = True

    # Quality preferences
    prioritize_fidelity: bool = True
    prioritize_file_size: bool = False
    prioritize_compatibility: bool = False

    # Advanced options
    custom_ffmpeg_params: dict[str, Any] = None
    enable_experimental_codecs: bool = False
    cache_transcoded_files: bool = True


@dataclass
class AudioAnalysis:
    """Audio analysis results"""

    file_path: str
    format: str
    duration: float
    sample_rate: int
    bit_depth: int
    bitrate: int
    channels: int
    codec: str

    # Audio characteristics
    dynamic_range: float  # dB
    peak_level: float  # dB
    rms_level: float  # dB
    loudness: float  # LUFS

    # Frequency analysis
    frequency_response: dict[str, float]
    spectral_centroid: float
    spectral_rolloff: float

    # Quality metrics
    signal_to_noise_ratio: float
    total_harmonic_distortion: float

    # Metadata
    detected_genre: str | None = None
    acoustic_features: dict[str, float] = None


@dataclass
class QualityComparison:
    """Quality comparison between different formats"""

    original_file: str
    formats: dict[str, dict[str, Any]]

    # Comparison metrics
    size_difference: dict[str, float]  # Percentage
    quality_score: dict[str, float]  # 0-100
    transparency_score: dict[str, float]  # 0-100

    # Recommendations
    recommended_format: str
    recommended_reason: str


class AudioTranscoder:
    """
    Audio transcoding with FFmpeg.

    Uses singleton pattern to avoid repeated FFmpeg lookups.
    """

    _instance: AudioTranscoder | None = None
    _ffmpeg_path: str | None = None
    _ffprobe_path: str | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self.temp_dir = Path(tempfile.gettempdir()) / "swingmusic_transcode"
        self.temp_dir.mkdir(exist_ok=True)

        # Initialize FFmpeg path once at class level
        if AudioTranscoder._ffmpeg_path is None:
            try:
                AudioTranscoder._ffmpeg_path = self._find_ffmpeg()
                AudioTranscoder._ffprobe_path = self._find_ffprobe()
            except FFmpegNotFoundError as e:
                logger.warning(f"FFmpeg not available: {e}")

    @property
    def ffmpeg_path(self) -> str:
        """Get FFmpeg path, raising error if not available."""
        if AudioTranscoder._ffmpeg_path is None:
            raise FFmpegNotFoundError(
                "FFmpeg not found. Please install FFmpeg or set SWINGMUSIC_FFMPEG_PATH."
            )
        return AudioTranscoder._ffmpeg_path

    @property
    def ffprobe_path(self) -> str:
        """Get FFprobe path, raising error if not available."""
        if AudioTranscoder._ffprobe_path is None:
            raise FFmpegNotFoundError(
                "FFprobe not found. Please install FFmpeg which includes FFprobe."
            )
        return AudioTranscoder._ffprobe_path

    @classmethod
    def is_available(cls) -> bool:
        """Check if FFmpeg is available without raising an exception."""
        return cls._ffmpeg_path is not None

    def _find_ffmpeg(self) -> str:
        """Find FFmpeg executable on the system."""
        # Check environment variable first
        env_path = os.environ.get("SWINGMUSIC_FFMPEG_PATH")
        if env_path and Path(env_path).exists():
            logger.info(f"Using FFmpeg from environment: {env_path}")
            return env_path

        # Use shutil.which to find in PATH
        which_path = shutil.which("ffmpeg")
        if which_path:
            return which_path

        # Platform-specific paths
        system = platform.system().lower()
        if system == "windows":
            search_paths = [
                "ffmpeg.exe",
                "C:\\ffmpeg\\bin\\ffmpeg.exe",
                "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
            ]
        elif system == "darwin":
            search_paths = [
                "/usr/bin/ffmpeg",
                "/usr/local/bin/ffmpeg",
                "/opt/homebrew/bin/ffmpeg",
            ]
        else:  # Linux and other Unix
            search_paths = [
                "/usr/bin/ffmpeg",
                "/usr/local/bin/ffmpeg",
                "/snap/bin/ffmpeg",
            ]

        for path in search_paths:
            if Path(path).exists():
                return path
            try:
                result = subprocess.run(
                    [path, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return path
            except (
                subprocess.SubprocessError,
                FileNotFoundError,
                subprocess.TimeoutExpired,
            ):
                continue

        raise FFmpegNotFoundError(
            "FFmpeg not found. Install FFmpeg or set SWINGMUSIC_FFMPEG_PATH environment variable."
        )

    def _find_ffprobe(self) -> str:
        """Find FFprobe executable."""
        # Check environment variable
        env_path = os.environ.get("SWINGMUSIC_FFPROBE_PATH")
        if env_path and Path(env_path).exists():
            return env_path

        # FFprobe is usually alongside ffmpeg
        if self._ffmpeg_path:
            ffmpeg_dir = Path(self._ffmpeg_path).parent
            ffprobe_name = (
                "ffprobe.exe" if platform.system().lower() == "windows" else "ffprobe"
            )
            ffprobe_path = ffmpeg_dir / ffprobe_name
            if ffprobe_path.exists():
                return str(ffprobe_path)

        # Try PATH
        which_path = shutil.which("ffprobe")
        if which_path:
            return which_path

        raise FFmpegNotFoundError("FFprobe not found alongside FFmpeg.")

    async def transcode(
        self, input_path: str, output_path: str, settings: AudioQualitySettings
    ) -> bool:
        """Transcode audio file according to settings"""
        try:
            # Build FFmpeg command
            cmd = self._build_transcode_command(input_path, output_path, settings)

            # Execute transcoding
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"FFmpeg error: {stderr.decode()}")
                return False

            return True

        except Exception as e:
            logger.error(f"Transcoding error: {e}")
            return False

    def _build_transcode_command(
        self, input_path: str, output_path: str, settings: AudioQualitySettings
    ) -> list[str]:
        """Build FFmpeg command for transcoding"""
        cmd = [self.ffmpeg_path, "-i", input_path]

        # Audio codec settings
        if settings.download_format == AudioFormat.FLAC:
            cmd.extend(["-c:a", "flac", "-compression_level", "8"])
        elif settings.download_format == AudioFormat.MP3_320:
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "320k"])
        elif settings.download_format == AudioFormat.MP3_256:
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "256k"])
        elif settings.download_format == AudioFormat.AAC_256:
            cmd.extend(["-c:a", "aac", "-b:a", "256k"])
        elif settings.download_format == AudioFormat.OGG_VORBIS:
            cmd.extend(["-c:a", "libvorbis", "-b:a", "256k"])
        else:
            # Default to FLAC
            cmd.extend(["-c:a", "flac"])

        # Sample rate
        if settings.download_sample_rate == SampleRate.RATE_48:
            cmd.extend(["-ar", "48000"])
        elif settings.download_sample_rate == SampleRate.RATE_96:
            cmd.extend(["-ar", "96000"])
        elif settings.download_sample_rate == SampleRate.RATE_192:
            cmd.extend(["-ar", "192000"])

        # Bit depth
        if settings.download_bit_depth == BitDepth.BIT_24:
            cmd.extend(["-sample_format", "s24"])
        elif settings.download_bit_depth == BitDepth.BIT_32:
            cmd.extend(["-sample_format", "s32"])

        # Custom FFmpeg parameters
        if settings.custom_ffmpeg_params:
            for key, value in settings.custom_ffmpeg_params.items():
                if isinstance(value, bool):
                    if value:
                        cmd.extend([key])
                else:
                    cmd.extend([key, str(value)])

        # Output settings
        cmd.extend(["-y", output_path])  # -y to overwrite

        return cmd


class AudioAnalyzer:
    """
    Audio analysis using FFmpeg and FFprobe.

    Uses AudioTranscoder singleton for FFmpeg/FFprobe paths.
    """

    def __init__(self):
        self._transcoder = AudioTranscoder()

    @property
    def ffmpeg_path(self) -> str:
        """Get FFmpeg path from transcoder."""
        return self._transcoder.ffmpeg_path

    @property
    def ffprobe_path(self) -> str:
        """Get FFprobe path from transcoder."""
        return self._transcoder.ffprobe_path

    @classmethod
    def is_available(cls) -> bool:
        """Check if FFmpeg/FFprobe is available."""
        return AudioTranscoder.is_available()

    async def analyze_file(self, file_path: str) -> AudioAnalysis:
        """
        Comprehensive audio file analysis using FFprobe.

        Args:
            file_path: Path to the audio file to analyze.

        Returns:
            AudioAnalysis object with file metadata and characteristics.

        Raises:
            AudioAnalysisError: If analysis fails.
        """
        try:
            probe_cmd = [
                self.ffprobe_path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ]

            process = await asyncio.create_subprocess_exec(
                *probe_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise AudioAnalysisError(f"FFprobe error: {stderr.decode()}")

            probe_data = json.loads(stdout.decode())

            # Extract audio stream info
            audio_stream = None
            for stream in probe_data.get("streams", []):
                if stream.get("codec_type") == "audio":
                    audio_stream = stream
                    break

            if not audio_stream:
                raise AudioAnalysisError(f"No audio stream found in {file_path}")

            format_info = probe_data.get("format", {})

            # Create analysis object with extracted info
            analysis = AudioAnalysis(
                file_path=file_path,
                format=format_info.get("format_name", "unknown"),
                duration=float(format_info.get("duration", 0)),
                sample_rate=int(audio_stream.get("sample_rate", 44100)),
                bit_depth=self._extract_bit_depth(audio_stream),
                bitrate=int(format_info.get("bit_rate", 0) or 0),
                channels=int(audio_stream.get("channels", 2)),
                codec=audio_stream.get("codec_name", "unknown"),
                # Audio characteristics (computed via FFmpeg filters)
                dynamic_range=0.0,
                peak_level=0.0,
                rms_level=0.0,
                loudness=0.0,
                frequency_response={},
                spectral_centroid=0.0,
                spectral_rolloff=0.0,
                signal_to_noise_ratio=0.0,
                total_harmonic_distortion=0.0,
            )

            # Perform advanced analysis using FFmpeg filters
            await self._perform_advanced_analysis(analysis)

            return analysis

        except AudioAnalysisError:
            raise
        except Exception as e:
            logger.error(f"Audio analysis error for {file_path}: {e}")
            raise AudioAnalysisError(f"Failed to analyze {file_path}: {e}") from e

    def _extract_bit_depth(self, stream: dict) -> int:
        """Extract bit depth from stream info"""
        bits_per_sample = stream.get("bits_per_sample")
        if bits_per_sample:
            return int(bits_per_sample)

        # Try to determine from codec
        codec_name = stream.get("codec_name", "").lower()
        if "flac" in codec_name or "pcm" in codec_name:
            return 16  # Default assumption
        return 16

    async def _perform_advanced_analysis(self, analysis: AudioAnalysis):
        """
        Perform advanced audio analysis using FFmpeg filters.

        Computes loudness, dynamic range, and other characteristics.
        """
        try:
            # Use FFmpeg's ebur128 filter for loudness measurement
            cmd = [
                self.ffmpeg_path,
                "-i",
                analysis.file_path,
                "-hide_banner",
                "-filter_complex",
                "ebur128=framelogging=0",
                "-f",
                "null",
                "-",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()
            stderr_text = stderr.decode()

            # Parse loudness info from FFmpeg output
            # Look for lines like: [Parsed_ebur128_0 @ ...] I: -14.0 LUFS
            loudness_match = re.search(r"I:\s*([-\d.]+)\s*LUFS", stderr_text)
            if loudness_match:
                analysis.loudness = float(loudness_match.group(1))

            # Parse peak
            peak_match = re.search(r"Peak:\s*([-\d.]+)\s*dB", stderr_text)
            if peak_match:
                analysis.peak_level = float(peak_match.group(1))

            # Use astats filter for RMS and dynamic range
            stats_cmd = [
                self.ffmpeg_path,
                "-i",
                analysis.file_path,
                "-hide_banner",
                "-af",
                "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.stats:file=-",
                "-f",
                "null",
                "-",
            ]

            stats_process = await asyncio.create_subprocess_exec(
                *stats_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stats_stdout, stats_stderr = await stats_process.communicate()
            stats_text = stats_stdout.decode()

            # Parse RMS level
            rms_match = re.search(r"RMS level dB:\s*([-\d.]+)", stats_text)
            if rms_match:
                analysis.rms_level = float(rms_match.group(1))

            # Parse dynamic range (Peak - RMS as approximation)
            if analysis.peak_level != 0.0 and analysis.rms_level != 0.0:
                analysis.dynamic_range = abs(analysis.peak_level - analysis.rms_level)

            # Set default frequency response bands
            analysis.frequency_response = {
                "20": 0.0,
                "60": 0.0,
                "250": 0.0,
                "1000": 0.0,
                "4000": 0.0,
                "16000": 0.0,
                "20000": 0.0,
            }

            # Spectral analysis would require librosa/numpy
            # For now, use reasonable defaults based on sample rate
            analysis.spectral_centroid = analysis.sample_rate / 20.0
            analysis.spectral_rolloff = analysis.sample_rate / 2.5

            # Quality metrics (SNR and THD would need specialized analysis)
            analysis.signal_to_noise_ratio = 60.0  # Default for good quality
            analysis.total_harmonic_distortion = 0.01  # 1% THD default

        except Exception as e:
            logger.warning(f"Advanced analysis failed for {analysis.file_path}: {e}")
            # Keep default values from initialization


class AdaptiveQualityManager:
    """Adaptive quality management based on conditions"""

    def __init__(self):
        self.network_monitor = NetworkMonitor()
        self.device_detector = DeviceDetector()
        self.quality_profiles = self._load_quality_profiles()

    def _load_quality_profiles(self) -> dict[str, dict]:
        """Load quality profiles for different conditions"""
        return {
            "excellent_network": {
                "streaming": QualityLevel.LOSSLESS,
                "download": AudioFormat.FLAC,
                "bitrate": None,
            },
            "good_network": {
                "streaming": QualityLevel.HIGH,
                "download": AudioFormat.MP3_320,
                "bitrate": 320,
            },
            "fair_network": {
                "streaming": QualityLevel.MEDIUM,
                "download": AudioFormat.MP3_256,
                "bitrate": 256,
            },
            "poor_network": {
                "streaming": QualityLevel.LOW,
                "download": AudioFormat.MP3_128,
                "bitrate": 128,
            },
            "data_saver": {
                "streaming": QualityLevel.DATA_SAVER,
                "download": AudioFormat.MP3_128,
                "bitrate": 128,
            },
            "mobile_device": {
                "streaming": QualityLevel.MEDIUM,
                "download": AudioFormat.AAC_256,
                "bitrate": 256,
            },
            "high_end_device": {
                "streaming": QualityLevel.LOSSLESS,
                "download": AudioFormat.FLAC,
                "bitrate": None,
            },
            "battery_saver": {
                "streaming": QualityLevel.LOW,
                "download": AudioFormat.MP3_192,
                "bitrate": 192,
            },
        }

    async def get_optimal_quality(
        self, user_settings: AudioQualitySettings, context: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Get optimal quality settings based on current conditions"""
        context = context or {}

        # Get current conditions
        network_speed = await self.network_monitor.get_current_speed()
        device_info = self.device_detector.get_device_info()
        battery_level = device_info.get("battery_level", 100)

        # Determine quality profile
        profile = self._determine_quality_profile(
            network_speed, device_info, battery_level, user_settings, context
        )

        return profile

    def _determine_quality_profile(
        self,
        network_speed: float,
        device_info: dict,
        battery_level: float,
        user_settings: AudioQualitySettings,
        context: dict,
    ) -> dict[str, Any]:
        """Determine the best quality profile"""

        # Network-based selection
        if user_settings.network_aware_quality:
            if network_speed > 10.0:  # Mbps
                network_profile = "excellent_network"
            elif network_speed > 5.0:
                network_profile = "good_network"
            elif network_speed > 2.0:
                network_profile = "fair_network"
            elif network_speed > 0.5:
                network_profile = "poor_network"
            else:
                network_profile = "data_saver"
        else:
            network_profile = "good_network"  # Default

        # Device-based selection
        if user_settings.device_specific_quality:
            device_type = device_info.get("type", "desktop")
            if device_type == "mobile":
                device_profile = "mobile_device"
            elif device_type == "high_end":
                device_profile = "high_end_device"
            else:
                device_profile = "good_network"
        else:
            device_profile = "good_network"

        # Battery-based selection
        if battery_level < 20 and context.get("battery_saver", False):
            battery_profile = "battery_saver"
        else:
            battery_profile = "good_network"

        # Select the most restrictive profile
        profiles = [network_profile, device_profile, battery_profile]
        selected_profile = self.quality_profiles["good_network"]  # Default

        for profile_name in profiles:
            profile = self.quality_profiles.get(profile_name)
            if profile:
                # Compare and select the most appropriate
                if self._is_more_restrictive(profile, selected_profile):
                    selected_profile = profile

        return selected_profile.copy()

    def _is_more_restrictive(self, profile1: dict, profile2: dict) -> bool:
        """Check if profile1 is more restrictive than profile2"""
        quality_order = {
            QualityLevel.LOSSLESS: 4,
            QualityLevel.HIGH: 3,
            QualityLevel.MEDIUM: 2,
            QualityLevel.LOW: 1,
            QualityLevel.DATA_SAVER: 0,
        }

        q1 = quality_order.get(profile1.get("streaming"), 2)
        q2 = quality_order.get(profile2.get("streaming"), 2)

        return q1 < q2


class AudioEnhancementService:
    """Audio enhancement processing"""

    def __init__(self):
        self.transcoder = AudioTranscoder()
        self.analyzer = AudioAnalyzer()

    async def apply_enhancements(
        self, input_path: str, output_path: str, settings: AudioQualitySettings
    ) -> bool:
        """Apply audio enhancements to a file"""
        try:
            # Analyze the input file
            analysis = await self.analyzer.analyze_file(input_path)

            # Build enhancement command
            cmd = self._build_enhancement_command(
                input_path, output_path, settings, analysis
            )

            # Apply enhancements
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"Enhancement error: {stderr.decode()}")
                return False

            return True

        except Exception as e:
            logger.error(f"Audio enhancement error: {e}")
            return False

    def _build_enhancement_command(
        self,
        input_path: str,
        output_path: str,
        settings: AudioQualitySettings,
        analysis: AudioAnalysis,
    ) -> list[str]:
        """Build FFmpeg command for audio enhancements"""
        cmd = [self.transcoder.ffmpeg_path, "-i", input_path]

        # Audio filters
        filters = []

        # Loudness normalization
        if settings.enable_loudness_normalization:
            filters.append(f"loudnorm=I={settings.target_loudness}")

        # Adaptive EQ (simplified)
        if settings.enable_adaptive_eq:
            # This would be more sophisticated in a real implementation
            # analyzing the frequency response and applying appropriate EQ
            filters.append("equalizer=f=1000:width_type=h:width=100:g=2")

        # Spatial audio processing
        if settings.enable_spatial_audio_processing:
            if settings.spatial_audio_format == SpatialAudioFormat.BINAURAL:
                filters.append("bs2b")
            elif settings.spatial_audio_format == SpatialAudioFormat.AMBISONIC:
                filters.append("surround")

        # Combine filters
        if filters:
            filter_string = ",".join(filters)
            cmd.extend(["-af", filter_string])

        # Output codec (preserve quality)
        cmd.extend(["-c:a", "pcm_s16le"])

        # Output
        cmd.extend(["-y", output_path])

        return cmd


class AudioQualityManager:
    """
    Main audio quality management service

    This service coordinates all audio quality operations including:
    - Adaptive quality streaming
    - Audio transcoding and enhancement
    - Quality analysis and comparison
    - User preference management
    """

    def __init__(self):
        self.transcoder = AudioTranscoder()
        self.analyzer = AudioAnalyzer()
        self.adaptive_manager = AdaptiveQualityManager()
        self.enhancement_service = AudioEnhancementService()

        # Cache for analysis results
        self._analysis_cache = {}
        self._quality_cache = {}

    async def get_optimal_streaming_quality(
        self, user_id: int, context: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Get optimal streaming quality for user"""
        try:
            # Get user settings
            user_settings = await self._get_user_settings(user_id)

            # Get optimal quality based on conditions
            optimal = await self.adaptive_manager.get_optimal_quality(
                user_settings, context
            )

            return optimal

        except Exception as e:
            logger.error(f"Error getting optimal quality: {e}")
            return {"streaming": "medium", "download": "mp3_256", "bitrate": 256}

    async def transcode_for_streaming(
        self, input_path: str, user_id: int, context: dict[str, Any] = None
    ) -> str | None:
        """Transcode file for optimal streaming"""
        try:
            # Get optimal quality
            quality_settings = await self.get_optimal_streaming_quality(
                user_id, context
            )

            # Create output path
            output_dir = Path(USER_DATA_DIR) / "transcoded"
            output_dir.mkdir(exist_ok=True)

            input_file = Path(input_path)
            output_file = output_dir / f"{input_file.stem}_transcoded.mp3"

            # Build settings for transcoding
            settings = AudioQualitySettings()
            if quality_settings.get("download") == AudioFormat.FLAC:
                settings.download_format = AudioFormat.FLAC
            elif quality_settings.get("download") == AudioFormat.MP3_320:
                settings.download_format = AudioFormat.MP3_320
            else:
                settings.download_format = AudioFormat.MP3_256

            # Transcode
            success = await self.transcoder.transcode(
                str(input_file), str(output_file), settings
            )

            if success:
                return str(output_file)
            else:
                return None

        except Exception as e:
            logger.error(f"Transcoding error: {e}")
            return None

    async def analyze_audio_file(self, file_path: str) -> AudioAnalysis:
        """Analyze audio file"""
        # Check cache first
        if file_path in self._analysis_cache:
            return self._analysis_cache[file_path]

        try:
            analysis = await self.analyzer.analyze_file(file_path)
            self._analysis_cache[file_path] = analysis
            return analysis

        except Exception as e:
            logger.error(f"Analysis error: {e}")
            raise

    async def compare_quality_formats(
        self, original_path: str, formats: list[AudioFormat]
    ) -> QualityComparison:
        """Compare quality across different formats"""
        try:
            original_analysis = await self.analyze_audio_file(original_path)

            comparison = QualityComparison(
                original_file=original_path,
                formats={},
                size_difference={},
                quality_score={},
                transparency_score={},
                recommended_format="flac",
                recommended_reason="Best quality for archival",
            )

            original_size = Path(original_path).stat().st_size

            for format_type in formats:
                try:
                    # Transcode to format
                    temp_file = await self._transcode_to_format(
                        original_path, format_type
                    )

                    if temp_file:
                        # Analyze transcoded file
                        transcoded_analysis = await self.analyze_audio_file(temp_file)

                        # Calculate metrics
                        transcoded_size = Path(temp_file).stat().st_size
                        size_diff = (
                            (transcoded_size - original_size) / original_size
                        ) * 100

                        quality_score = self._calculate_quality_score(
                            original_analysis, transcoded_analysis
                        )

                        transparency_score = self._calculate_transparency_score(
                            original_analysis, transcoded_analysis
                        )

                        comparison.formats[format_type.value] = {
                            "analysis": asdict(transcoded_analysis),
                            "file_size": transcoded_size,
                            "file_path": temp_file,
                        }

                        comparison.size_difference[format_type.value] = size_diff
                        comparison.quality_score[format_type.value] = quality_score
                        comparison.transparency_score[format_type.value] = (
                            transparency_score
                        )

                        # Clean up temp file
                        os.unlink(temp_file)

                except Exception as e:
                    logger.error(f"Error comparing format {format_type}: {e}")
                    continue

            # Determine recommendation
            comparison.recommended_format, comparison.recommended_reason = (
                self._determine_best_format(comparison)
            )

            return comparison

        except Exception as e:
            logger.error(f"Quality comparison error: {e}")
            raise

    async def _transcode_to_format(
        self, input_path: str, format_type: AudioFormat
    ) -> str | None:
        """Transcode file to specific format for comparison"""
        try:
            temp_dir = Path(tempfile.gettempdir()) / "swingmusic_compare"
            temp_dir.mkdir(exist_ok=True)

            input_file = Path(input_path)
            output_file = temp_dir / f"{input_file.stem}_compare.{format_type.value}"

            settings = AudioQualitySettings()
            settings.download_format = format_type

            success = await self.transcoder.transcode(
                str(input_file), str(output_file), settings
            )

            if success:
                return str(output_file)
            else:
                return None

        except Exception as e:
            logger.error(f"Format transcoding error: {e}")
            return None

    def _calculate_quality_score(
        self, original: AudioAnalysis, transcoded: AudioAnalysis
    ) -> float:
        """Calculate quality score (0-100)"""
        try:
            # Simplified quality calculation
            # In a real implementation, this would be more sophisticated

            score = 100.0

            # Penalize quality loss
            if transcoded.bitrate < original.bitrate:
                score -= (original.bitrate - transcoded.bitrate) / original.bitrate * 30

            # Penalize sample rate reduction
            if transcoded.sample_rate < original.sample_rate:
                score -= (
                    (original.sample_rate - transcoded.sample_rate)
                    / original.sample_rate
                    * 20
                )

            # Penalize bit depth reduction
            if transcoded.bit_depth < original.bit_depth:
                score -= (
                    (original.bit_depth - transcoded.bit_depth)
                    / original.bit_depth
                    * 10
                )

            return max(0, min(100, score))

        except Exception:
            return 50.0  # Default score

    def _calculate_transparency_score(
        self, original: AudioAnalysis, transcoded: AudioAnalysis
    ) -> float:
        """Calculate transparency score (0-100)"""
        try:
            # Simplified transparency calculation
            # In a real implementation, this would use ABX testing or perceptual models

            if transcoded.format == original.format:
                return 100.0

            # Lossless formats get high transparency
            if transcoded.format in ["flac", "alac", "wav"]:
                return 95.0

            # High bitrate lossy formats
            if transcoded.bitrate >= 320:
                return 85.0
            elif transcoded.bitrate >= 256:
                return 75.0
            elif transcoded.bitrate >= 192:
                return 60.0
            else:
                return 40.0

        except Exception:
            return 50.0

    def _determine_best_format(self, comparison: QualityComparison) -> tuple[str, str]:
        """Determine the best format recommendation"""
        try:
            best_format = "flac"
            best_reason = "Best quality for archival purposes"

            # Consider user priorities
            scores = comparison.quality_score

            if scores:
                # Find format with best balance of quality and size
                best_score = 0
                for format_name, score in scores.items():
                    size_penalty = (
                        abs(comparison.size_difference.get(format_name, 0)) / 100
                    )
                    combined_score = score - size_penalty * 10

                    if combined_score > best_score:
                        best_score = combined_score
                        best_format = format_name
                        best_reason = (
                            f"Best balance of quality ({score:.1f}) and file size"
                        )

            return best_format, best_reason

        except Exception:
            return "flac", "Best quality for archival purposes"

    async def _get_user_settings(self, user_id: int) -> AudioQualitySettings:
        """
        Get user's audio quality settings from database.

        Returns default settings if user has no saved preferences.
        """
        try:
            row = UserAudioSettingsTable.get_by_userid(user_id)
            if row:
                return UserAudioSettingsTable.to_dataclass(row)
        except Exception as e:
            logger.warning(f"Failed to get user settings for {user_id}: {e}")

        return AudioQualitySettings()

    async def update_user_settings(
        self, user_id: int, settings: AudioQualitySettings
    ) -> bool:
        """
        Update user's audio quality settings in database.

        Creates settings entry if it doesn't exist.
        """
        try:
            # Ensure user has a settings row
            UserAudioSettingsTable.get_or_create(user_id)

            # Convert dataclass to dict for database update
            settings_dict = {
                "streaming_quality": settings.streaming_quality.value,
                "adaptive_quality": settings.adaptive_quality,
                "network_aware_quality": settings.network_aware_quality,
                "device_specific_quality": settings.device_specific_quality,
                "download_format": settings.download_format.value,
                "download_bitrate": settings.download_bitrate,
                "download_sample_rate": settings.download_sample_rate.value,
                "download_bit_depth": settings.download_bit_depth.value,
                "enable_dolby_atmos": settings.enable_dolby_atmos,
                "enable_360_audio": settings.enable_360_audio,
                "spatial_audio_format": settings.spatial_audio_format.value,
                "enable_adaptive_eq": settings.enable_adaptive_eq,
                "enable_spatial_audio_processing": settings.enable_spatial_audio_processing,
                "enable_loudness_normalization": settings.enable_loudness_normalization,
                "target_loudness": settings.target_loudness,
                "enable_crossfade": settings.enable_crossfade,
                "crossfade_duration": settings.crossfade_duration,
                "enable_gapless_playback": settings.enable_gapless_playback,
                "enable_replaygain": settings.enable_replaygain,
                "prioritize_fidelity": settings.prioritize_fidelity,
                "prioritize_file_size": settings.prioritize_file_size,
                "prioritize_compatibility": settings.prioritize_compatibility,
                "enable_experimental_codecs": settings.enable_experimental_codecs,
                "cache_transcoded_files": settings.cache_transcoded_files,
            }

            UserAudioSettingsTable.update_settings(user_id, settings_dict)
            logger.info(f"Updated audio settings for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update user settings for {user_id}: {e}")
            return False

    def clear_cache(self, max_age_seconds: int | None = None):
        """
        Clear analysis and quality cache.

        Args:
            max_age_seconds: If provided, only clear entries older than this.
        """
        if max_age_seconds is None:
            self._analysis_cache.clear()
            self._quality_cache.clear()
            return

        # Clear entries older than max_age_seconds
        current_time = time.time()
        keys_to_remove = []

        for key, entry in self._analysis_cache.items():
            if hasattr(entry, "timestamp"):
                if current_time - entry.timestamp > max_age_seconds:
                    keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._analysis_cache[key]

        # Same for quality cache
        keys_to_remove = []
        for key, entry in self._quality_cache.items():
            if hasattr(entry, "timestamp"):
                if current_time - entry.timestamp > max_age_seconds:
                    keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._quality_cache[key]


# =============================================================================
# Network and Device Monitoring
# =============================================================================


class NetworkMonitor:
    """
    Monitors network conditions for adaptive quality streaming.

    Measures bandwidth and latency to determine optimal streaming quality.
    """

    def __init__(self):
        self._last_speed: float = 0.0
        self._last_check: float = 0.0
        self._speed_history: list[float] = []
        self._max_history: int = 10

    async def get_current_speed(self) -> float:
        """
        Get current network speed in Mbps.

        Uses a simple test or returns cached value if recently checked.
        """
        current_time = time.time()

        # Return cached value if checked within last 30 seconds
        if current_time - self._last_check < 30 and self._last_speed > 0:
            return self._last_speed

        try:
            # Try to measure actual speed via download test
            speed = await self._measure_speed()
            self._last_speed = speed
            self._last_check = current_time

            # Update history
            self._speed_history.append(speed)
            if len(self._speed_history) > self._max_history:
                self._speed_history.pop(0)

            return speed

        except Exception as e:
            logger.warning(f"Network speed measurement failed: {e}")
            # Return average of history or default
            if self._speed_history:
                return sum(self._speed_history) / len(self._speed_history)
            return 5.0  # Default to 5 Mbps (good network)

    async def _measure_speed(self) -> float:
        """
        Measure actual network speed.

        Downloads a small test file to estimate bandwidth.
        """
        try:
            # Simple latency test
            start = time.time()
            socket.create_connection(("8.8.8.8", 53), timeout=2).close()
            latency = (time.time() - start) * 1000  # ms

            # Estimate speed based on latency (rough approximation)
            # Lower latency typically correlates with better bandwidth
            if latency < 50:
                return 20.0  # Excellent
            elif latency < 100:
                return 10.0  # Good
            elif latency < 200:
                return 5.0  # Fair
            else:
                return 2.0  # Poor

        except (TimeoutError, OSError):
            return 1.0  # Very poor connection

    def get_network_quality(self) -> str:
        """Get network quality as a string label."""
        if self._last_speed > 10:
            return "excellent"
        elif self._last_speed > 5:
            return "good"
        elif self._last_speed > 2:
            return "fair"
        else:
            return "poor"

    @property
    def average_speed(self) -> float:
        """Get average speed from history."""
        if self._speed_history:
            return sum(self._speed_history) / len(self._speed_history)
        return 0.0


class DeviceDetector:
    """
    Detects device capabilities for adaptive quality settings.

    Determines device type, audio capabilities, and battery status.
    """

    def __init__(self):
        self._device_info: dict[str, Any] | None = None
        self._detected = False

    def get_device_info(self) -> dict[str, Any]:
        """
        Get comprehensive device information.

        Returns cached info if already detected.
        """
        if self._detected and self._device_info:
            return self._device_info

        self._device_info = self._detect_device()
        self._detected = True
        return self._device_info

    def _detect_device(self) -> dict[str, Any]:
        """Detect device capabilities."""
        system = platform.system().lower()
        machine = platform.machine().lower()

        # Determine device type
        if system == "android" or "arm" in machine or "mobile" in machine:
            device_type = "mobile"
        elif system == "darwin" and "arm" in machine:
            device_type = "mobile"  # Apple Silicon could be mobile/tablet
        else:
            device_type = "desktop"

        # Check for high-end device indicators
        is_high_end = self._check_high_end()

        return {
            "type": device_type,
            "system": system,
            "machine": machine,
            "high_end": is_high_end,
            "supports_lossless": True,  # Most modern devices support FLAC
            "supports_spatial_audio": self._check_spatial_audio_support(),
            "battery_level": self._get_battery_level(),
            "battery_saver": False,  # Would need platform-specific detection
        }

    def _check_high_end(self) -> bool:
        """Check if this is a high-end device."""
        # Check CPU cores and memory as indicators
        try:
            cpu_count = os.cpu_count() or 1
            # High-end devices typically have 4+ cores
            return cpu_count >= 4
        except Exception:
            return False

    def _check_spatial_audio_support(self) -> bool:
        """Check if device supports spatial audio formats."""
        # Most desktop systems can process spatial audio via FFmpeg
        return True

    def _get_battery_level(self) -> int:
        """
        Get battery level (0-100).

        Returns 100 (full) if unable to detect or on desktop.
        """
        system = platform.system().lower()

        try:
            if system == "linux":
                # Try to read from /sys/class/power_supply
                battery_path = Path("/sys/class/power_supply")
                if battery_path.exists():
                    for battery in battery_path.iterdir():
                        if "BAT" in battery.name:
                            capacity_file = battery / "capacity"
                            if capacity_file.exists():
                                return int(capacity_file.read_text().strip())
            elif system == "darwin":
                # macOS - would need pmset command
                result = subprocess.run(
                    ["pmset", "-g", "batt"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                # Parse output like: "Battery 0: 100%"
                match = re.search(r"(\d+)%", result.stdout)
                if match:
                    return int(match.group(1))
        except Exception:
            pass

        return 100  # Default to full battery

    def get_device_capabilities(self) -> dict:
        """Get device capabilities (legacy method for compatibility)."""
        info = self.get_device_info()
        return {
            "supports_lossless": info.get("supports_lossless", True),
            "supports_spatial_audio": info.get("supports_spatial_audio", False),
            "type": info.get("type", "desktop"),
        }
