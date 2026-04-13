from __future__ import annotations

from flask import Blueprint, jsonify, request

fallback_ux_bp = Blueprint("fallback_ux", __name__, url_prefix="/api/ux")
fallback_updates_bp = Blueprint("fallback_updates", __name__, url_prefix="/api/updates")
fallback_audio_quality_bp = Blueprint(
    "fallback_audio_quality", __name__, url_prefix="/api/audio-quality"
)
fallback_recap_bp = Blueprint("fallback_recap", __name__, url_prefix="/api/recap")
fallback_settings_bp = Blueprint(
    "fallback_settings", __name__, url_prefix="/api/settings"
)


DEFAULT_AUDIO_SETTINGS = {
    "streaming_quality": "high",
    "adaptive_quality": True,
    "network_aware_quality": True,
    "device_specific_quality": True,
    "download_format": "flac",
    "download_sample_rate": "44.1kHz",
    "download_bit_depth": "16bit",
    "enable_loudness_normalization": True,
    "target_loudness": -14.0,
    "enable_adaptive_eq": False,
    "enable_spatial_audio_processing": False,
    "spatial_audio_format": "stereo",
    "enable_crossfade": False,
    "crossfade_duration": 2.0,
    "enable_gapless_playback": True,
    "enable_replaygain": True,
}


DEFAULT_UPDATE_SETTINGS = {
    "enableArtistMonitoring": False,
    "autoDownloadFavorites": False,
    "enableNotifications": False,
    "checkFrequency": "daily",
    "qualityPreference": "flac",
    "excludeExplicit": False,
}


DEFAULT_UD_SETTINGS = {
    "defaultQuality": "high",
    "autoAddToLibrary": True,
    "maxConcurrentDownloads": 3,
}


def _disabled_payload(feature: str, **payload):
    return {"enabled": False, "feature": feature, **payload}


@fallback_ux_bp.get("/search/suggestions")
def fallback_ux_search_suggestions():
    query = request.args.get("q", "")
    context = request.args.get("context", "general")
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            suggestions=[],
            query=query,
            context=context,
            total_count=0,
        )
    )


@fallback_ux_bp.get("/discovery/recommendations")
def fallback_ux_discovery():
    recommendation_type = request.args.get("type", "mixed")
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            recommendations=[],
            type=recommendation_type,
            total_count=0,
        )
    )


@fallback_ux_bp.get("/contextual/suggestions")
def fallback_ux_contextual():
    track_id = request.args.get("track_id")
    context_type = request.args.get("context_type", "similar")
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            suggestions=[],
            track_id=track_id,
            context_type=context_type,
            total_count=0,
        )
    )


@fallback_ux_bp.get("/download/suggestions")
def fallback_ux_download_suggestions():
    query = request.args.get("q", "")
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            suggestions=[],
            query=query,
            total_count=0,
        )
    )


@fallback_ux_bp.get("/search/filters")
def fallback_ux_filters():
    return jsonify(_disabled_payload("advanced_ux", filters=[], total_count=0))


@fallback_ux_bp.post("/behavior/track")
def fallback_ux_track_behavior():
    return jsonify(
        _disabled_payload("advanced_ux", message="Behavior tracking skipped")
    )


@fallback_ux_bp.get("/behavior/profile")
def fallback_ux_behavior_profile():
    profile = {
        "user_id": None,
        "favorite_genres": [],
        "favorite_artists": [],
        "listening_patterns": {},
        "download_preferences": {},
        "interaction_patterns": {},
        "last_updated": None,
        "search_history_count": 0,
        "recent_searches": [],
    }
    return jsonify(_disabled_payload("advanced_ux", profile=profile))


@fallback_ux_bp.get("/trending/content")
def fallback_ux_trending():
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            trending=[],
            type=request.args.get("type", "mixed"),
            timeframe=request.args.get("timeframe", "week"),
            total_count=0,
        )
    )


@fallback_ux_bp.post("/search/advanced")
def fallback_ux_advanced_search():
    payload = request.get_json(silent=True) or {}
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            query=payload.get("query", ""),
            results={
                "tracks": [],
                "albums": [],
                "artists": [],
                "playlists": [],
            },
        )
    )


@fallback_ux_bp.get("/suggestions/quick")
def fallback_ux_quick_suggestions():
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            suggestions=[],
            type=request.args.get("type", "search"),
            total_count=0,
        )
    )


@fallback_ux_bp.get("/personalization/preferences")
def fallback_ux_get_preferences():
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            preferences={"enable_personalization": False},
        )
    )


@fallback_ux_bp.put("/personalization/preferences")
def fallback_ux_update_preferences():
    payload = request.get_json(silent=True) or {}
    return jsonify(
        _disabled_payload(
            "advanced_ux",
            message="Preferences saved in fallback mode",
            preferences=payload,
        )
    )


@fallback_updates_bp.get("/stats")
def fallback_updates_stats():
    stats = {
        "followedArtists": 0,
        "newReleases": 0,
        "pendingDownloads": 0,
        "unreadNotifications": 0,
    }
    return jsonify(_disabled_payload("update_tracking", stats=stats))


@fallback_updates_bp.get("/recent")
def fallback_updates_recent():
    limit = request.args.get("limit", 20, type=int)
    offset = request.args.get("offset", 0, type=int)
    return jsonify(
        _disabled_payload(
            "update_tracking",
            updates=[],
            limit=limit,
            offset=offset,
            total=0,
        )
    )


@fallback_updates_bp.get("/followed-artists")
def fallback_updates_followed_artists():
    return jsonify(
        _disabled_payload(
            "update_tracking",
            artists=[],
            limit=50,
            offset=0,
            total=0,
        )
    )


@fallback_updates_bp.get("/settings")
def fallback_updates_get_settings():
    return jsonify(_disabled_payload("update_tracking", **DEFAULT_UPDATE_SETTINGS))


@fallback_updates_bp.post("/settings")
def fallback_updates_set_settings():
    payload = request.get_json(silent=True) or {}
    merged = {**DEFAULT_UPDATE_SETTINGS, **payload}
    return jsonify(
        _disabled_payload(
            "update_tracking",
            message="Settings saved in fallback mode",
            settings=merged,
        )
    )


@fallback_updates_bp.get("/search/artists")
def fallback_updates_search_artists():
    return jsonify(
        _disabled_payload(
            "update_tracking",
            artists=[],
            query=request.args.get("q", ""),
        )
    )


@fallback_updates_bp.post("/follow-artist")
def fallback_updates_follow_artist():
    payload = request.get_json(silent=True) or {}
    return jsonify(
        _disabled_payload(
            "update_tracking",
            message="Artist follow stored in fallback mode",
            artist_id=payload.get("artist_id"),
        )
    )


@fallback_updates_bp.post("/unfollow-artist")
def fallback_updates_unfollow_artist():
    payload = request.get_json(silent=True) or {}
    return jsonify(
        _disabled_payload(
            "update_tracking",
            message="Artist unfollow stored in fallback mode",
            artist_id=payload.get("artist_id"),
        )
    )


@fallback_updates_bp.get("/artist/<artist_id>/follow-status")
def fallback_updates_follow_status(artist_id: str):
    return jsonify(
        _disabled_payload(
            "update_tracking",
            is_following=False,
            artist_id=artist_id,
            follow_level="followed",
            auto_download_new_releases=False,
            preferred_quality="flac",
        )
    )


@fallback_updates_bp.post("/artist/<artist_id>")
def fallback_updates_update_artist(artist_id: str):
    payload = request.get_json(silent=True) or {}
    return jsonify(
        _disabled_payload(
            "update_tracking",
            message="Artist preferences saved in fallback mode",
            artist_id=artist_id,
            settings=payload,
        )
    )


@fallback_updates_bp.post("/auto-download/<release_id>")
def fallback_updates_auto_download(release_id: str):
    return jsonify(
        _disabled_payload(
            "update_tracking",
            message="Download queued in fallback mode",
            release_id=release_id,
        )
    )


@fallback_updates_bp.post("/release/<release_id>/mark-read")
def fallback_updates_mark_read(release_id: str):
    return jsonify(
        _disabled_payload(
            "update_tracking",
            message="Marked as read",
            release_id=release_id,
        )
    )


@fallback_updates_bp.post("/notifications/mark-all-read")
def fallback_updates_mark_all_read():
    return jsonify(
        _disabled_payload(
            "update_tracking",
            message="All notifications marked as read",
        )
    )


@fallback_updates_bp.get("/export/followed-artists")
def fallback_updates_export_followed_artists():
    return jsonify(_disabled_payload("update_tracking", followed_artists=[]))


@fallback_audio_quality_bp.get("/settings")
def fallback_audio_get_settings():
    return jsonify(_disabled_payload("audio_quality", settings=DEFAULT_AUDIO_SETTINGS))


@fallback_audio_quality_bp.post("/settings")
def fallback_audio_set_settings():
    payload = request.get_json(silent=True) or {}
    merged = {**DEFAULT_AUDIO_SETTINGS, **payload}
    return jsonify(
        _disabled_payload(
            "audio_quality",
            message="Audio quality settings saved in fallback mode",
            settings=merged,
        )
    )


@fallback_audio_quality_bp.get("/network/status")
def fallback_audio_network_status():
    return jsonify(
        _disabled_payload(
            "audio_quality",
            network_status={"speed": 0, "quality": "unknown"},
        )
    )


@fallback_audio_quality_bp.get("/device/info")
def fallback_audio_device_info():
    return jsonify(
        _disabled_payload(
            "audio_quality",
            device_info={"type": "unknown"},
        )
    )


@fallback_audio_quality_bp.post("/apply-preset")
def fallback_audio_apply_preset():
    payload = request.get_json(silent=True) or {}
    return jsonify(
        _disabled_payload(
            "audio_quality",
            message="Preset applied in fallback mode",
            preset_name=payload.get("preset_name"),
            settings=DEFAULT_AUDIO_SETTINGS,
        )
    )


@fallback_recap_bp.get("/available-years")
def fallback_recap_available_years():
    return jsonify(_disabled_payload("recap", available_years=[], total_recaps=0))


@fallback_recap_bp.get("/summary/<int:year>")
def fallback_recap_summary(year: int):
    return jsonify(_disabled_payload("recap", recap=None, year=year))


@fallback_recap_bp.get("/details/<int:year>")
def fallback_recap_details(year: int):
    return jsonify(_disabled_payload("recap", recap=None, year=year))


@fallback_recap_bp.post("/generate/<int:year>")
def fallback_recap_generate(year: int):
    return jsonify(
        _disabled_payload(
            "recap",
            message="Recap generation is unavailable",
            year=year,
        )
    )


@fallback_recap_bp.post("/video/<int:year>")
def fallback_recap_video(year: int):
    return jsonify(
        _disabled_payload(
            "recap",
            message="Recap video generation is unavailable",
            year=year,
        )
    )


@fallback_recap_bp.post("/share/<int:year>")
def fallback_recap_share(year: int):
    return jsonify(
        _disabled_payload(
            "recap",
            message="Share links are unavailable",
            year=year,
            share_url=None,
        )
    )


@fallback_recap_bp.get("/shared/<token>")
def fallback_recap_shared(token: str):
    return jsonify(_disabled_payload("recap", recap=None, token=token))


@fallback_recap_bp.get("/compare/<int:year1>/<int:year2>")
def fallback_recap_compare(year1: int, year2: int):
    return jsonify(_disabled_payload("recap", comparison=None, years=[year1, year2]))


@fallback_settings_bp.get("/universal-downloader")
def fallback_universal_downloader_get():
    return jsonify(
        _disabled_payload(
            "universal_downloader_settings",
            success=True,
            settings=DEFAULT_UD_SETTINGS,
        )
    )


@fallback_settings_bp.post("/universal-downloader")
def fallback_universal_downloader_post():
    payload = request.get_json(silent=True) or {}
    merged = {**DEFAULT_UD_SETTINGS, **payload}
    return jsonify(
        _disabled_payload(
            "universal_downloader_settings",
            success=True,
            settings=merged,
            message="Settings saved in fallback mode",
        )
    )


def _has_route(app, route: str) -> bool:
    return any(rule.rule == route for rule in app.url_map.iter_rules())


def register_optional_feature_fallbacks(app):
    if not _has_route(app, "/api/ux/search/suggestions"):
        app.register_blueprint(fallback_ux_bp)

    if not _has_route(app, "/api/updates/stats"):
        app.register_blueprint(fallback_updates_bp)

    if not _has_route(app, "/api/audio-quality/settings"):
        app.register_blueprint(fallback_audio_quality_bp)

    if not _has_route(app, "/api/recap/available-years"):
        app.register_blueprint(fallback_recap_bp)

    if not _has_route(app, "/api/settings/universal-downloader"):
        app.register_blueprint(fallback_settings_bp)
