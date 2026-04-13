"""Advanced UX endpoints backed by local stores and lightweight persistence."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from swingmusic.services.advanced_ux_store import advanced_ux_store
from swingmusic.utils.auth import get_current_userid

advanced_ux_bp = Blueprint("advanced_ux", __name__, url_prefix="/api/ux")


def _user_id() -> int:
    return int(get_current_userid())


def _safe_limit(value, default: int = 10, max_value: int = 100) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, max_value))


@advanced_ux_bp.get("/search/suggestions")
def search_suggestions():
    query = str(request.args.get("q") or "")
    context = str(request.args.get("context") or "general")
    limit = _safe_limit(request.args.get("limit"), default=10, max_value=50)

    suggestions = advanced_ux_store.search_suggestions(
        query=query, context=context, limit=limit
    )
    return jsonify(
        {
            "enabled": True,
            "suggestions": suggestions,
            "query": query,
            "context": context,
            "total_count": len(suggestions),
        }
    )


@advanced_ux_bp.get("/discovery/recommendations")
def discovery_recommendations():
    recommendation_type = str(request.args.get("type") or "mixed")
    limit = _safe_limit(request.args.get("limit"), default=20, max_value=100)

    recommendations = advanced_ux_store.get_recommendations(recommendation_type, limit)
    return jsonify(
        {
            "enabled": True,
            "recommendations": recommendations,
            "type": recommendation_type,
            "total_count": len(recommendations),
        }
    )


@advanced_ux_bp.get("/contextual/suggestions")
def contextual_suggestions():
    track_id = str(request.args.get("track_id") or "")
    context_type = str(request.args.get("context_type") or "similar")
    limit = _safe_limit(request.args.get("limit"), default=10, max_value=50)

    suggestions = advanced_ux_store.get_contextual_suggestions(
        track_id, context_type, limit
    )
    return jsonify(
        {
            "enabled": True,
            "suggestions": suggestions,
            "track_id": track_id,
            "context_type": context_type,
            "total_count": len(suggestions),
        }
    )


@advanced_ux_bp.get("/download/suggestions")
def download_suggestions():
    query = str(request.args.get("q") or "")
    limit = _safe_limit(request.args.get("limit"), default=15, max_value=50)

    suggestions = advanced_ux_store.get_download_suggestions(query=query, limit=limit)
    return jsonify(
        {
            "enabled": True,
            "suggestions": suggestions,
            "query": query,
            "total_count": len(suggestions),
        }
    )


@advanced_ux_bp.get("/search/filters")
def search_filters():
    filters = advanced_ux_store.get_search_filters()
    return jsonify(
        {
            "enabled": True,
            "filters": filters,
            "total_count": len(filters),
        }
    )


@advanced_ux_bp.post("/behavior/track")
def behavior_track():
    payload = request.get_json(silent=True) or {}
    event_type = str(payload.get("type") or "unknown")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    advanced_ux_store.track_behavior(_user_id(), event_type, data)
    return jsonify({"enabled": True, "message": "Behavior event tracked"})


@advanced_ux_bp.get("/behavior/profile")
def behavior_profile():
    profile = advanced_ux_store.get_behavior_profile(_user_id())
    return jsonify({"enabled": True, "profile": profile})


@advanced_ux_bp.get("/trending/content")
def trending_content():
    item_type = str(request.args.get("type") or "mixed")
    timeframe = str(request.args.get("timeframe") or "week")
    limit = _safe_limit(request.args.get("limit"), default=20, max_value=100)

    trending = advanced_ux_store.get_trending(
        item_type=item_type, timeframe=timeframe, limit=limit
    )
    return jsonify(
        {
            "enabled": True,
            "trending": trending,
            "type": item_type,
            "timeframe": timeframe,
            "total_count": len(trending),
        }
    )


@advanced_ux_bp.post("/search/advanced")
def advanced_search():
    payload = request.get_json(silent=True) or {}
    result = advanced_ux_store.advanced_search(payload)
    result["enabled"] = True
    return jsonify(result)


@advanced_ux_bp.get("/suggestions/quick")
def quick_suggestions():
    suggestion_type = str(request.args.get("type") or "search")
    limit = _safe_limit(request.args.get("limit"), default=5, max_value=30)

    suggestions = advanced_ux_store.quick_suggestions(
        suggestion_type=suggestion_type, limit=limit
    )
    return jsonify(
        {
            "enabled": True,
            "suggestions": suggestions,
            "type": suggestion_type,
            "total_count": len(suggestions),
        }
    )


@advanced_ux_bp.get("/personalization/preferences")
def get_personalization_preferences():
    prefs = advanced_ux_store.get_preferences(_user_id())
    return jsonify({"enabled": True, "preferences": prefs})


@advanced_ux_bp.put("/personalization/preferences")
def update_personalization_preferences():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}

    prefs = advanced_ux_store.update_preferences(_user_id(), payload)
    return jsonify(
        {
            "enabled": True,
            "message": "Preferences updated",
            "preferences": prefs,
        }
    )
