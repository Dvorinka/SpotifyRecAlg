"""Unified multi-service downloader API backed by durable download jobs."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from swingmusic.services.download_jobs import download_job_manager
from swingmusic.services.spotify_downloader import spotify_downloader
from swingmusic.services.universal_url_parser import universal_url_parser
from swingmusic.utils.auth import get_current_userid
from swingmusic.utils.hashing import create_hash

universal_downloader_bp = Blueprint(
    "universal_downloader", __name__, url_prefix="/api/universal"
)


def _current_userid() -> int:
    try:
        identity = get_jwt_identity()
        if isinstance(identity, dict) and identity.get("id") is not None:
            return int(identity["id"])
    except Exception:
        pass

    return get_current_userid()


def _quality_to_job(quality: str | None) -> tuple[str, str]:
    quality = (quality or "high").lower()
    mapping = {
        "lossless": ("lossless", "flac"),
        "high": ("high", "mp3"),
        "medium": ("medium", "mp3"),
        "low": ("low", "mp3"),
    }
    return mapping.get(quality, ("high", "mp3"))


def _serialize_jobs(jobs: list[dict]) -> list[dict]:
    serialized = []
    for job in jobs:
        payload = job.get("payload") or {}
        serialized.append(
            {
                "id": str(job.get("id")),
                "url": job.get("source_url"),
                "title": job.get("title") or payload.get("title"),
                "artist": job.get("artist") or payload.get("artist"),
                "album": job.get("album") or payload.get("album"),
                "service": job.get("source") or payload.get("service") or "generic",
                "item_type": job.get("item_type")
                or payload.get("item_type")
                or "track",
                "quality": job.get("quality") or "high",
                "status": job.get("state"),
                "state": job.get("state"),
                "progress": job.get("progress") or 0,
                "error_message": job.get("error"),
                "file_path": job.get("target_path"),
                "created_at": job.get("created_at"),
                "started_at": job.get("started_at"),
                "finished_at": job.get("finished_at"),
            }
        )
    return serialized


@universal_downloader_bp.route("/download", methods=["POST"])
def add_download():
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    parsed = universal_url_parser.parse_url(url)
    if not parsed:
        return jsonify({"error": "Unsupported URL format"}), 400

    quality, codec = _quality_to_job(data.get("quality"))
    output_dir = data.get("output_dir")
    userid = _current_userid()

    title = None
    artist = None
    album = None
    trackhash = None

    if parsed.service.value == "spotify":
        metadata = asyncio.run(spotify_downloader.get_metadata(url))
        if metadata:
            title = metadata.title
            artist = metadata.artist
            album = metadata.album
            if metadata.item_type == "track" and title and artist:
                trackhash = create_hash(title, album or "", artist)

    job_id = download_job_manager.enqueue(
        userid=userid,
        source_url=url,
        source=parsed.service.value,
        quality=quality,
        codec=codec,
        trackhash=trackhash,
        title=title,
        artist=artist,
        album=album,
        item_type=parsed.item_type,
        target_path=output_dir,
        payload={
            "service": parsed.service.value,
            "item_type": parsed.item_type,
            "service_id": parsed.id,
            "metadata": parsed.metadata,
        },
    )

    return jsonify(
        {
            "success": True,
            "item_id": str(job_id),
            "service": parsed.service.value,
            "item_type": parsed.item_type,
            "message": f"Added to download queue from {parsed.service.value}",
        }
    )


@universal_downloader_bp.route("/metadata", methods=["POST"])
def get_metadata():
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    parsed = universal_url_parser.parse_url(url)
    if not parsed:
        return jsonify({"error": "Unsupported URL format"}), 400

    if parsed.service.value == "spotify":
        metadata = asyncio.run(spotify_downloader.get_metadata(url))
        if metadata:
            return jsonify(
                {
                    "success": True,
                    "service": "spotify",
                    "service_id": metadata.spotify_id,
                    "item_type": metadata.item_type,
                    "title": metadata.title,
                    "artist": metadata.artist,
                    "album": metadata.album,
                    "duration_ms": metadata.duration_ms,
                    "image_url": metadata.image_url,
                    "release_date": metadata.release_date,
                    "explicit": metadata.is_explicit,
                    "preview_url": metadata.preview_url,
                    "original_url": url,
                }
            )

    return jsonify(
        {
            "success": True,
            "service": parsed.service.value,
            "service_id": parsed.id,
            "item_type": parsed.item_type,
            "title": f"{parsed.service.value.title()} {parsed.item_type.title()}",
            "artist": "Unknown Artist",
            "album": "",
            "duration_ms": None,
            "image_url": None,
            "release_date": None,
            "explicit": False,
            "preview_url": None,
            "original_url": url,
        }
    )


@universal_downloader_bp.route("/queue", methods=["GET"])
def get_queue_status():
    userid = _current_userid()
    jobs = download_job_manager.list_jobs(userid, limit=500)

    queued = [job for job in jobs if job["state"] in {"queued", "downloading"}]
    active = [job for job in jobs if job["state"] == "downloading"]
    history = [
        job for job in jobs if job["state"] in {"completed", "failed", "cancelled"}
    ]

    return jsonify(
        {
            "queue_length": len([job for job in jobs if job["state"] == "queued"]),
            "active_downloads": len(active),
            "queue": _serialize_jobs(queued),
            "pending": _serialize_jobs(
                [job for job in jobs if job["state"] == "queued"]
            ),
            "active": _serialize_jobs(active),
            "history": _serialize_jobs(history),
        }
    )


@universal_downloader_bp.route("/queue/<item_id>/cancel", methods=["POST"])
def cancel_download(item_id: str):
    userid = _current_userid()
    try:
        success = download_job_manager.cancel(int(item_id), userid)
    except ValueError:
        success = False

    if success:
        return jsonify({"success": True, "message": "Download cancelled"})

    return jsonify({"error": "Download not found or cannot be cancelled"}), 404


@universal_downloader_bp.route("/queue/<item_id>/retry", methods=["POST"])
def retry_download(item_id: str):
    userid = _current_userid()
    try:
        success = download_job_manager.retry(int(item_id), userid)
    except ValueError:
        success = False

    if success:
        return jsonify({"success": True, "message": "Download retry added to queue"})

    return jsonify({"error": "Download not found or cannot be retried"}), 404


@universal_downloader_bp.route("/history", methods=["GET"])
def get_download_history():
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    userid = _current_userid()

    jobs = download_job_manager.list_jobs(userid, limit=1000)
    history = [
        job for job in jobs if job["state"] in {"completed", "failed", "cancelled"}
    ]
    sliced = history[offset : offset + limit]

    return jsonify(
        {
            "downloads": _serialize_jobs(sliced),
            "total": len(history),
            "limit": limit,
            "offset": offset,
        }
    )


@universal_downloader_bp.route("/services", methods=["GET"])
def get_supported_services():
    services = universal_url_parser.get_supported_services()
    return jsonify({"services": services, "total": len(services)})


@universal_downloader_bp.route("/services/<service_name>/enable", methods=["POST"])
def enable_service(service_name: str):
    return jsonify({"success": True, "message": f"{service_name} service enabled"})


@universal_downloader_bp.route("/services/<service_name>/disable", methods=["POST"])
def disable_service(service_name: str):
    return jsonify({"success": True, "message": f"{service_name} service disabled"})


@universal_downloader_bp.route(
    "/services/<service_name>/config", methods=["GET", "POST"]
)
def service_config(service_name: str):
    if request.method == "GET":
        return jsonify(
            {
                "service": service_name,
                "display_name": service_name.replace("_", " ").title(),
                "enabled": True,
                "priority": 0,
                "supported_types": [],
                "features": ["metadata", "download"],
                "config": {},
            }
        )

    return jsonify({"success": True, "message": "Service configuration updated"})


@universal_downloader_bp.route("/validate-url", methods=["POST"])
def validate_url():
    data = request.get_json() or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    parsed = universal_url_parser.parse_url(url)
    if parsed:
        return jsonify(
            {
                "valid": True,
                "service": parsed.service.value,
                "item_type": parsed.item_type,
                "id": parsed.id,
                "metadata": parsed.metadata,
            }
        )

    return jsonify({"valid": False, "error": "Unsupported URL format"})


@universal_downloader_bp.route("/statistics", methods=["GET"])
def get_statistics():
    userid = _current_userid()
    jobs = download_job_manager.list_jobs(userid, limit=1000)

    stats: dict[str, dict[str, int]] = defaultdict(dict)
    grouped = defaultdict(Counter)

    for job in jobs:
        source = job.get("source") or "generic"
        state = job.get("state") or "unknown"
        grouped[source][state] += 1

    for source, counts in grouped.items():
        stats[source] = dict(counts)

    return jsonify({"statistics": stats})


@universal_downloader_bp.route("/batch", methods=["POST"])
def batch_download():
    data = request.get_json() or {}
    urls = data.get("urls") or []
    if not isinstance(urls, list) or len(urls) == 0:
        return jsonify({"error": "URLs array is required"}), 400

    quality = data.get("quality")
    output_dir = data.get("output_dir")

    results = []
    for url in urls:
        value = (url or "").strip()
        if not value:
            continue

        parsed = universal_url_parser.parse_url(value)
        if not parsed:
            results.append(
                {"url": value, "success": False, "error": "Unsupported URL format"}
            )
            continue

        quality_name, codec = _quality_to_job(quality)
        userid = _current_userid()

        job_id = download_job_manager.enqueue(
            userid=userid,
            source_url=value,
            source=parsed.service.value,
            quality=quality_name,
            codec=codec,
            item_type=parsed.item_type,
            target_path=output_dir,
            payload={
                "service": parsed.service.value,
                "item_type": parsed.item_type,
                "service_id": parsed.id,
                "metadata": parsed.metadata,
            },
        )

        results.append(
            {
                "url": value,
                "success": True,
                "item_id": str(job_id),
                "service": parsed.service.value,
                "item_type": parsed.item_type,
            }
        )

    successful = sum(1 for item in results if item["success"])
    failed = len(results) - successful

    return jsonify(
        {
            "total": len(results),
            "successful": successful,
            "failed": failed,
            "results": results,
        }
    )


def register_universal_downloader_api(app):
    app.register_blueprint(universal_downloader_bp)
