"""
Spotify Downloader Settings API endpoints
"""

from typing import Any

from flask import jsonify
from flask_jwt_extended import get_jwt_identity
from flask_openapi3 import APIBlueprint
from pydantic import BaseModel, Field

from swingmusic import logger
from swingmusic.config import UserConfig
from swingmusic.services.download_jobs import download_job_manager
from swingmusic.utils.auth import get_current_userid

spotify_settings_bp = APIBlueprint(
    "spotify_settings",
    import_name="spotify_settings",
    url_prefix="/api/settings/spotify",
)


def _current_userid() -> int:
    try:
        identity = get_jwt_identity()
        if isinstance(identity, dict) and identity.get("id") is not None:
            return int(identity["id"])
    except Exception:
        pass

    return get_current_userid()


class SpotifySettingsRequest(BaseModel):
    defaultQuality: str = Field("flac", description="Default download quality")
    downloadFolder: str | None = Field(None, description="Download folder path")
    autoAddToLibrary: bool = Field(True, description="Auto-add downloads to library")
    maxConcurrentDownloads: int = Field(3, description="Max concurrent downloads")
    sources: list | None = Field(None, description="Download sources configuration")
    maxRetryAttempts: int = Field(3, description="Max retry attempts")
    cleanupHistoryDays: int = Field(30, description="Auto-cleanup history days")
    showExplicitWarning: bool = Field(True, description="Show explicit content warning")


class SpotifySettingsResponse(BaseModel):
    success: bool
    settings: dict[str, Any] | None = None
    message: str | None = None


# Default settings
DEFAULT_SETTINGS = {
    "defaultQuality": "flac",
    "downloadFolder": "",
    "autoAddToLibrary": True,
    "maxConcurrentDownloads": 3,
    "sources": [
        {
            "name": "tidal",
            "display_name": "Tidal",
            "enabled": True,
            "priority": 1,
            "config": {
                "quality_preference": ["lossless", "high", "normal"],
                "formats": ["flac", "mp3"],
            },
        },
        {
            "name": "qobuz",
            "display_name": "Qobuz",
            "enabled": True,
            "priority": 2,
            "config": {
                "quality_preference": ["lossless", "high", "normal"],
                "formats": ["flac", "mp3"],
            },
        },
        {
            "name": "amazon",
            "display_name": "Amazon Music",
            "enabled": False,
            "priority": 3,
            "config": {
                "quality_preference": ["high", "normal"],
                "formats": ["mp3", "aac"],
            },
        },
    ],
    "maxRetryAttempts": 3,
    "cleanupHistoryDays": 30,
    "showExplicitWarning": True,
}


def get_spotify_settings():
    """Get Spotify downloader settings from config"""
    try:
        config = UserConfig()
        spotify_settings = (
            config.spotify_downloads if hasattr(config, "spotify_downloads") else {}
        )

        # Merge with defaults
        settings = {**DEFAULT_SETTINGS}
        settings.update(spotify_settings)

        return settings
    except Exception as e:
        logger.error(f"Error loading Spotify settings: {e}")
        return DEFAULT_SETTINGS


def save_spotify_settings(settings_data: dict):
    """Save Spotify downloader settings to config"""
    try:
        config = UserConfig()

        # Update only provided settings
        current_settings = get_spotify_settings()
        current_settings.update(settings_data)

        # Save to config
        config.spotify_downloads = current_settings
        config.save()

        logger.info("Spotify settings saved successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving Spotify settings: {e}")
        return False


@spotify_settings_bp.get("/", summary="Get Spotify downloader settings")
def get_settings():
    """
    Get current Spotify downloader settings

    Returns all Spotify downloader configuration including:
    - Default quality settings
    - Download folder configuration
    - Source priorities and enablement
    - Advanced options
    """
    try:
        settings = get_spotify_settings()

        return jsonify({"success": True, "settings": settings})

    except Exception as e:
        logger.error(f"Error getting Spotify settings: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@spotify_settings_bp.post("/", summary="Update Spotify downloader settings")
def update_settings(body: SpotifySettingsRequest):
    """
    Update Spotify downloader settings

    - **defaultQuality**: Default download quality (flac, mp3_320, mp3_128)
    - **downloadFolder**: Custom download folder path
    - **autoAddToLibrary**: Whether to auto-add downloads to library
    - **maxConcurrentDownloads**: Maximum concurrent downloads (1-10)
    - **sources**: Download sources configuration
    - **maxRetryAttempts**: Maximum retry attempts for failed downloads
    - **cleanupHistoryDays**: Days to keep download history (0 = disabled)
    - **showExplicitWarning**: Show warning for explicit content

    Updates the Spotify downloader configuration and saves to user settings.
    """
    try:
        # Validate inputs
        if body.defaultQuality not in ["flac", "mp3_320", "mp3_128"]:
            return jsonify(
                {"success": False, "message": "Invalid quality setting"}
            ), 400

        if not 1 <= body.maxConcurrentDownloads <= 10:
            return jsonify(
                {
                    "success": False,
                    "message": "Max concurrent downloads must be between 1 and 10",
                }
            ), 400

        if not 0 <= body.maxRetryAttempts <= 10:
            return jsonify(
                {
                    "success": False,
                    "message": "Max retry attempts must be between 0 and 10",
                }
            ), 400

        if not 0 <= body.cleanupHistoryDays <= 365:
            return jsonify(
                {"success": False, "message": "Cleanup days must be between 0 and 365"}
            ), 400

        # Prepare settings data
        settings_data = {
            "defaultQuality": body.defaultQuality,
            "downloadFolder": body.downloadFolder,
            "autoAddToLibrary": body.autoAddToLibrary,
            "maxConcurrentDownloads": body.maxConcurrentDownloads,
            "sources": body.sources,
            "maxRetryAttempts": body.maxRetryAttempts,
            "cleanupHistoryDays": body.cleanupHistoryDays,
            "showExplicitWarning": body.showExplicitWarning,
        }

        # Remove None values
        settings_data = {k: v for k, v in settings_data.items() if v is not None}

        # Save settings
        if save_spotify_settings(settings_data):
            return jsonify({"success": True, "message": "Settings saved successfully"})
        else:
            return jsonify(
                {"success": False, "message": "Failed to save settings"}
            ), 500

    except Exception as e:
        logger.error(f"Error updating Spotify settings: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@spotify_settings_bp.post("/reset", summary="Reset Spotify settings to defaults")
def reset_settings():
    """
    Reset all Spotify downloader settings to default values

    Resets all Spotify downloader configuration to factory defaults.
    """
    try:
        if save_spotify_settings(DEFAULT_SETTINGS):
            return jsonify(
                {
                    "success": True,
                    "message": "Settings reset to defaults",
                    "settings": DEFAULT_SETTINGS,
                }
            )
        else:
            return jsonify(
                {"success": False, "message": "Failed to reset settings"}
            ), 500

    except Exception as e:
        logger.error(f"Error resetting Spotify settings: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@spotify_settings_bp.delete("/queue", summary="Clear download queue")
def clear_queue():
    """
    Clear pending/active download jobs for current user.
    """
    try:
        userid = _current_userid()
        cancelled = download_job_manager.clear_queue(userid)
        return jsonify(
            {
                "success": True,
                "cancelled_jobs": cancelled,
                "message": f"Cleared queue ({cancelled} job(s) cancelled)",
            }
        )

    except Exception as e:
        logger.error(f"Error clearing download queue: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@spotify_settings_bp.delete("/history", summary="Clear download history")
def clear_history():
    """
    Clear completed/failed/cancelled download history for current user.
    """
    try:
        userid = _current_userid()
        deleted = download_job_manager.clear_history(userid)
        return jsonify(
            {
                "success": True,
                "deleted_jobs": deleted,
                "message": f"Download history cleared ({deleted} job(s) removed)",
            }
        )

    except Exception as e:
        logger.error(f"Error clearing download history: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@spotify_settings_bp.get("/sources", summary="Get available download sources")
def get_available_sources():
    """
    Get list of available download sources

    Returns information about supported download sources and their capabilities.
    """
    try:
        sources = [
            {
                "name": "tidal",
                "display_name": "Tidal",
                "description": "High-quality FLAC downloads from Tidal",
                "quality_options": ["lossless", "high", "normal"],
                "formats": ["flac", "mp3"],
                "available": True,
                "requires_auth": False,
                "max_quality": "lossless",
            },
            {
                "name": "qobuz",
                "display_name": "Qobuz",
                "description": "Alternative high-quality source with extensive catalog",
                "quality_options": ["lossless", "high", "normal"],
                "formats": ["flac", "mp3"],
                "available": True,
                "requires_auth": True,
                "max_quality": "lossless",
            },
            {
                "name": "amazon",
                "display_name": "Amazon Music",
                "description": "Fallback source with wide availability",
                "quality_options": ["high", "normal"],
                "formats": ["mp3", "aac"],
                "available": False,  # Disabled by default
                "requires_auth": True,
                "max_quality": "high",
            },
        ]

        return jsonify({"success": True, "sources": sources})

    except Exception as e:
        logger.error(f"Error getting available sources: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# Error handlers
@spotify_settings_bp.errorhandler(400)
def bad_request(error):
    return jsonify(
        {"error": "Bad request", "message": str(error), "success": False}
    ), 400


@spotify_settings_bp.errorhandler(500)
def internal_error(error):
    return jsonify(
        {"error": "Internal server error", "message": str(error), "success": False}
    ), 500
