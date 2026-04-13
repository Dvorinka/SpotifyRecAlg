"""
Update Tracking API Endpoints

Provides stable endpoints for following artists, update preferences,
recent release updates, and dashboard statistics.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from flask import Blueprint, Response, jsonify, request

from swingmusic.services.update_tracker import (
    VALID_CHECK_FREQUENCIES,
    VALID_FOLLOW_LEVELS,
    VALID_QUALITY_VALUES,
    VALID_RELEASE_TYPES,
    update_tracker,
)
from swingmusic.utils.auth import get_current_userid

logger = logging.getLogger(__name__)

update_tracking_bp = Blueprint("update_tracking", __name__, url_prefix="/api/updates")


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _user_id() -> int:
    return int(get_current_userid())


def _safe_limit(value: Any, default: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default

    return max(0, min(parsed, max_value))


@update_tracking_bp.post("/follow-artist")
def follow_artist():
    data = request.get_json(silent=True) or {}
    artist_id = str(data.get("artist_id") or "").strip()

    if not artist_id:
        return _error("artist_id is required")

    payload = {
        "user_id": _user_id(),
        "artist_id": artist_id,
        "artist_name": str(data.get("artist_name") or artist_id),
        "follow_level": str(data.get("follow_level") or "followed"),
        "auto_download": bool(data.get("auto_download", False)),
        "preferred_quality": str(data.get("preferred_quality") or "flac"),
        "notification_preferences": data.get("notification_preferences"),
        "image": data.get("image"),
    }

    if payload["follow_level"] not in VALID_FOLLOW_LEVELS:
        return _error("Invalid follow_level")

    if payload["preferred_quality"] not in VALID_QUALITY_VALUES:
        return _error("Invalid preferred_quality")

    success = update_tracker.follow_artist(payload)
    if not success:
        return _error("Failed to follow artist", 500)

    return jsonify(
        {
            "message": "Artist followed successfully",
            "artist_id": artist_id,
        }
    )


@update_tracking_bp.post("/unfollow-artist")
def unfollow_artist():
    data = request.get_json(silent=True) or {}
    artist_id = str(data.get("artist_id") or "").strip()

    if not artist_id:
        return _error("artist_id is required")

    success = update_tracker.unfollow_artist(_user_id(), artist_id)
    if not success:
        return _error("Artist not followed", 404)

    return jsonify(
        {
            "message": "Artist unfollowed successfully",
            "artist_id": artist_id,
        }
    )


@update_tracking_bp.get("/recent")
def get_recent_updates():
    limit = _safe_limit(request.args.get("limit"), default=20, max_value=100)
    offset = _safe_limit(request.args.get("offset"), default=0, max_value=100000)
    release_type = request.args.get("release_type")
    unread_only = str(request.args.get("unread_only", "false")).lower() == "true"

    if release_type and release_type not in VALID_RELEASE_TYPES:
        return _error("Invalid release_type")

    updates = update_tracker.get_user_updates(
        user_id=_user_id(),
        limit=limit,
        offset=offset,
        release_type=release_type,
        unread_only=unread_only,
    )

    return jsonify(
        {
            "updates": updates,
            "limit": limit,
            "offset": offset,
            "total": len(updates),
        }
    )


@update_tracking_bp.get("/settings")
def get_settings():
    return jsonify(update_tracker.get_user_settings(_user_id()))


@update_tracking_bp.post("/settings")
def update_settings():
    data = request.get_json(silent=True) or {}

    check_frequency = data.get("checkFrequency", data.get("check_frequency"))
    if check_frequency and check_frequency not in VALID_CHECK_FREQUENCIES:
        return _error("Invalid checkFrequency")

    quality_preference = data.get("qualityPreference", data.get("quality_preference"))
    if quality_preference and quality_preference not in VALID_QUALITY_VALUES:
        return _error("Invalid qualityPreference")

    if not update_tracker.update_user_settings(_user_id(), data):
        return _error("Failed to update settings", 500)

    return jsonify(
        {
            "message": "Settings updated successfully",
            "settings": update_tracker.get_user_settings(_user_id()),
        }
    )


@update_tracking_bp.post("/auto-download/<release_id>")
def auto_download_release(release_id: str):
    if not update_tracker.auto_download_release(_user_id(), release_id):
        return _error("Release not found", 404)

    return jsonify(
        {
            "message": "Download queued successfully",
            "release_id": release_id,
        }
    )


@update_tracking_bp.get("/stats")
def get_update_stats():
    stats = update_tracker.get_user_stats(_user_id())
    return jsonify({"stats": stats})


@update_tracking_bp.get("/followed-artists")
def get_followed_artists():
    limit = _safe_limit(request.args.get("limit"), default=50, max_value=200)
    offset = _safe_limit(request.args.get("offset"), default=0, max_value=100000)
    follow_level = request.args.get("follow_level")

    if follow_level and follow_level not in VALID_FOLLOW_LEVELS:
        return _error("Invalid follow_level")

    artists = update_tracker.get_followed_artists(
        user_id=_user_id(),
        limit=limit,
        offset=offset,
        follow_level=follow_level,
    )

    return jsonify(
        {
            "artists": artists,
            "limit": limit,
            "offset": offset,
            "total": len(artists),
        }
    )


@update_tracking_bp.get("/artist/<artist_id>/follow-status")
def get_artist_follow_status(artist_id: str):
    status = update_tracker.get_artist_follow_status(_user_id(), artist_id)

    if status:
        return jsonify(status)

    return jsonify(
        {
            "is_following": False,
            "artist_id": artist_id,
            "follow_level": "followed",
            "auto_download_new_releases": False,
            "preferred_quality": "flac",
        }
    )


@update_tracking_bp.route("/artist/<artist_id>", methods=["POST", "PUT"])
def update_artist_follow(artist_id: str):
    data = request.get_json(silent=True) or {}

    follow_level = data.get("follow_level")
    if follow_level and follow_level not in VALID_FOLLOW_LEVELS:
        return _error("Invalid follow_level")

    preferred_quality = data.get("preferred_quality")
    if preferred_quality and preferred_quality not in VALID_QUALITY_VALUES:
        return _error("Invalid preferred_quality")

    success = update_tracker.update_artist_follow(_user_id(), artist_id, data)
    if not success:
        return _error("Failed to update artist", 500)

    return jsonify(
        {
            "message": "Artist follow settings updated",
            "artist_id": artist_id,
            "settings": data,
        }
    )


@update_tracking_bp.get("/search/artists")
def search_artists():
    query = str(request.args.get("q") or "").strip()
    limit = _safe_limit(request.args.get("limit"), default=20, max_value=100)

    artists = update_tracker.search_artists(query, _user_id(), limit=limit)
    return jsonify(
        {
            "artists": artists,
            "query": query,
        }
    )


@update_tracking_bp.post("/release/<release_id>/mark-read")
def mark_release_read(release_id: str):
    if not update_tracker.mark_release_read(_user_id(), release_id):
        return _error("Failed to mark release as read", 500)

    return jsonify(
        {
            "message": "Marked release as read",
            "release_id": release_id,
        }
    )


@update_tracking_bp.post("/notifications/mark-all-read")
def mark_all_read():
    count = update_tracker.mark_all_notifications_read(_user_id())
    return jsonify(
        {
            "message": "All notifications marked as read",
            "updated": count,
        }
    )


@update_tracking_bp.get("/export/followed-artists")
def export_followed_artists():
    export_format = str(request.args.get("format") or "json").lower()
    artists = update_tracker.export_followed_artists(_user_id())

    if export_format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "artist_id",
                "artist_name",
                "follow_level",
                "auto_download",
                "preferred_quality",
                "follow_date",
            ],
        )
        writer.writeheader()
        writer.writerows(artists)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=followed_artists.csv",
            },
        )

    return jsonify({"followed_artists": artists})


@update_tracking_bp.errorhandler(404)
def not_found(_error):
    return jsonify({"error": "Endpoint not found"}), 404


@update_tracking_bp.errorhandler(500)
def internal_error(_error):
    return jsonify({"error": "Internal server error"}), 500
