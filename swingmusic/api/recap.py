"""Year-in-review recap endpoints."""

from __future__ import annotations

import datetime as dt

from flask import Blueprint, jsonify, request

from swingmusic.services.recap_store import recap_store
from swingmusic.utils.auth import get_current_userid

recap_bp = Blueprint("recap", __name__, url_prefix="/api/recap")


def _user_id() -> int:
    return int(get_current_userid())


def _error(message: str, status: int = 400):
    return jsonify({"error": message, "message": message}), status


def _validate_year(year: int) -> bool:
    now_year = dt.datetime.now(dt.UTC).year
    return 2000 <= int(year) <= now_year + 1


@recap_bp.get("/available-years")
def available_years():
    years = recap_store.get_available_years(_user_id())
    return jsonify({"available_years": years, "total_recaps": len(years)})


@recap_bp.get("/summary/<int:year>")
def summary(year: int):
    if not _validate_year(year):
        return _error("Invalid year")

    recap = recap_store.get_summary(_user_id(), year)
    return jsonify({"year": year, "recap": recap})


@recap_bp.get("/details/<int:year>")
def details(year: int):
    if not _validate_year(year):
        return _error("Invalid year")

    recap = recap_store.get_recap(_user_id(), year, generate_if_missing=False)
    return jsonify({"year": year, "recap": recap})


@recap_bp.post("/generate/<int:year>")
def generate(year: int):
    if not _validate_year(year):
        return _error("Invalid year")

    recap = recap_store.generate_recap(_user_id(), year)
    if not recap:
        return _error("No listening data available for this year", 404)

    return jsonify(
        {
            "message": "Recap generated successfully",
            "year": year,
            "recap": recap,
        }
    )


@recap_bp.post("/video/<int:year>")
def generate_video(year: int):
    if not _validate_year(year):
        return _error("Invalid year")

    recap = recap_store.get_recap(_user_id(), year, generate_if_missing=True)
    if not recap:
        return _error("No listening data available for this year", 404)

    options = request.get_json(silent=True) or {}
    return jsonify(
        {
            "message": "Video generation queued",
            "year": year,
            "video_status": "queued",
            "options": options,
        }
    )


@recap_bp.post("/share/<int:year>")
def share(year: int):
    if not _validate_year(year):
        return _error("Invalid year")

    payload = request.get_json(silent=True) or {}
    include_personal_data = bool(
        payload.get("includePersonalData", payload.get("include_personal_data", False))
    )

    try:
        expires_in_days = int(
            payload.get("expiresInDays", payload.get("expires_in_days", 30))
        )
    except (TypeError, ValueError):
        expires_in_days = 30

    share_data = recap_store.create_share_link(
        user_id=_user_id(),
        year=year,
        include_personal_data=include_personal_data,
        expires_in_days=expires_in_days,
    )

    if not share_data:
        return _error("Unable to create share link", 404)

    return jsonify(share_data)


@recap_bp.get("/shared/<token>")
def shared(token: str):
    shared_recap = recap_store.get_shared_recap(token)
    if not shared_recap:
        return _error("Shared recap not found or expired", 404)

    return jsonify(shared_recap)


@recap_bp.get("/compare/<int:year1>/<int:year2>")
def compare(year1: int, year2: int):
    if not _validate_year(year1) or not _validate_year(year2):
        return _error("Invalid year")

    if year1 == year2:
        return _error("Year values must be different")

    comparison = recap_store.compare_years(_user_id(), year1, year2)
    if not comparison:
        return _error("Comparison unavailable for selected years", 404)

    return jsonify({"years": [year1, year2], "comparison": comparison})
