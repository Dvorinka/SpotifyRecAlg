"""Spotify downloader API backed by the unified durable download job pipeline."""

from __future__ import annotations

import asyncio

from flask import jsonify, request
from flask_jwt_extended import get_jwt_identity
from flask_openapi3 import APIBlueprint
from pydantic import BaseModel, Field

from swingmusic.services.spotify_downloader import DownloadSource, spotify_downloader
from swingmusic.utils.auth import get_current_userid

spotify_bp = APIBlueprint(
    "spotify",
    import_name="spotify",
    url_prefix="/api/spotify",
)


class SpotifyURLRequest(BaseModel):
    url: str = Field(..., description="Spotify URL (track, album, playlist, artist)")
    quality: str | None = Field(default="flac", description="Audio quality")
    output_dir: str | None = Field(default=None, description="Output directory")


def _current_userid() -> int:
    try:
        identity = get_jwt_identity()
        if isinstance(identity, dict) and identity.get("id") is not None:
            return int(identity["id"])
    except Exception:
        pass

    return get_current_userid()


@spotify_bp.post("/metadata", summary="Get Spotify metadata")
def get_metadata(body: SpotifyURLRequest):
    try:
        metadata = asyncio.run(spotify_downloader.get_metadata(body.url))

        if not metadata:
            return jsonify({"error": "Invalid Spotify URL", "success": False}), 400

        return jsonify(
            {
                "success": True,
                "metadata": {
                    "spotify_id": metadata.spotify_id,
                    "item_type": metadata.item_type,
                    "title": metadata.title,
                    "artist": metadata.artist,
                    "album": metadata.album,
                    "duration_ms": metadata.duration_ms,
                    "image_url": metadata.image_url,
                    "release_date": metadata.release_date,
                    "track_number": metadata.track_number,
                    "total_tracks": metadata.total_tracks,
                    "is_explicit": metadata.is_explicit,
                    "preview_url": metadata.preview_url,
                },
            }
        )
    except Exception as error:
        return jsonify({"error": str(error), "success": False}), 500


@spotify_bp.post("/download", summary="Add Spotify URL to queue")
def download_from_url(body: SpotifyURLRequest):
    userid = _current_userid()

    item_id = spotify_downloader.add_download(
        spotify_url=body.url,
        output_dir=body.output_dir,
        quality=body.quality,
        userid=userid,
    )

    if not item_id:
        return jsonify({"error": "Failed to add download", "success": False}), 400

    return jsonify(
        {
            "success": True,
            "message": "Download added to queue",
            "item_id": item_id,
        }
    )


@spotify_bp.get("/queue", summary="Get queue status")
def get_queue_status():
    userid = _current_userid()
    status = spotify_downloader.get_queue_status(userid)
    return jsonify({"success": True, "data": status})


@spotify_bp.post("/cancel/<item_id>", summary="Cancel download")
def cancel_download(item_id: str):
    userid = _current_userid()
    success = spotify_downloader.cancel_download(item_id, userid=userid)

    if not success:
        return jsonify(
            {"success": False, "message": "Download not found or cannot be cancelled"}
        ), 404

    return jsonify({"success": True, "message": "Download cancelled successfully"})


@spotify_bp.post("/retry/<item_id>", summary="Retry failed download")
def retry_download(item_id: str):
    userid = _current_userid()
    success = spotify_downloader.retry_download(item_id, userid=userid)

    if not success:
        return jsonify(
            {"success": False, "message": "Download not found or cannot be retried"}
        ), 404

    return jsonify({"success": True, "message": "Download retry added to queue"})


@spotify_bp.get("/sources", summary="Get download sources")
def get_download_sources():
    sources = [
        {
            "name": source.value,
            "display_name": source.value.replace("_", " ").title(),
            "enabled": True,
            "priority": index,
        }
        for index, source in enumerate(DownloadSource)
    ]
    return jsonify({"success": True, "sources": sources})


@spotify_bp.get("/qualities", summary="Get audio qualities")
def get_audio_qualities():
    return jsonify(
        {
            "success": True,
            "qualities": [
                {
                    "id": "flac",
                    "name": "FLAC",
                    "description": "Lossless audio quality",
                    "extension": "flac",
                    "bitrate": "Lossless",
                },
                {
                    "id": "mp3_320",
                    "name": "MP3 320kbps",
                    "description": "High quality MP3",
                    "extension": "mp3",
                    "bitrate": "320 kbps",
                },
                {
                    "id": "mp3_128",
                    "name": "MP3 128kbps",
                    "description": "Standard quality MP3",
                    "extension": "mp3",
                    "bitrate": "128 kbps",
                },
            ],
        }
    )


@spotify_bp.get("/history", summary="Get download history")
def get_download_history():
    userid = _current_userid()
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 50))
    status_filter = request.args.get("status", None)

    status = spotify_downloader.get_queue_status(userid)
    history = status.get("history", [])

    if status_filter:
        history = [item for item in history if item.get("state") == status_filter]

    total = len(history)
    start = max(0, (page - 1) * limit)
    end = start + limit
    items = history[start:end]

    return jsonify(
        {
            "success": True,
            "data": {
                "items": items,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "pages": (total + limit - 1) // limit,
                },
            },
        }
    )


@spotify_bp.delete("/clear-history", summary="Clear download history")
def clear_download_history():
    # Durable history is kept in DB for reliability; expose as no-op success for backward compatibility.
    return jsonify(
        {"success": True, "message": "History retention is managed automatically"}
    )
