"""Audio quality endpoints for settings, presets and environment hints."""

from __future__ import annotations

import json

from flask import Blueprint, jsonify, request

from swingmusic.services.audio_quality_store import audio_quality_store
from swingmusic.utils.auth import get_current_userid

audio_quality_bp = Blueprint("audio_quality", __name__, url_prefix="/api/audio-quality")


def _user_id() -> int:
    return int(get_current_userid())


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


@audio_quality_bp.get("/settings")
def get_quality_settings():
    settings = audio_quality_store.get_settings(_user_id())
    return jsonify({"enabled": True, "settings": settings})


@audio_quality_bp.post("/settings")
def update_quality_settings():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return _error("Request body must be an object")

    settings = audio_quality_store.update_settings(_user_id(), data)
    return jsonify(
        {
            "message": "Audio quality settings updated successfully",
            "settings": settings,
        }
    )


@audio_quality_bp.get("/optimal-streaming")
def get_optimal_streaming_quality():
    context_raw = request.args.get("context")
    context = {}

    if context_raw:
        try:
            decoded = json.loads(context_raw)
            if isinstance(decoded, dict):
                context = decoded
        except json.JSONDecodeError:
            context = {}

    optimal_quality = audio_quality_store.get_optimal_streaming_quality(
        _user_id(), context
    )
    return jsonify({"optimal_quality": optimal_quality, "context": context})


@audio_quality_bp.post("/apply-preset")
def apply_preset():
    data = request.get_json(silent=True) or {}
    preset_name = str(data.get("preset_name") or "").strip()

    if not preset_name:
        return _error("preset_name is required")

    settings, ok = audio_quality_store.apply_preset(_user_id(), preset_name)
    if not ok:
        return _error("Invalid preset_name", 404)

    return jsonify(
        {
            "message": "Preset applied successfully",
            "preset_name": preset_name,
            "settings": settings,
        }
    )


@audio_quality_bp.get("/quality-presets")
def get_quality_presets():
    return jsonify({"presets": audio_quality_store.get_presets()})


@audio_quality_bp.get("/formats")
def get_supported_formats():
    return jsonify({"formats": audio_quality_store.get_supported_formats()})


@audio_quality_bp.get("/network/status")
def get_network_status():
    return jsonify({"network_status": audio_quality_store.get_network_status()})


@audio_quality_bp.get("/device/info")
def get_device_info():
    user_agent = request.headers.get("User-Agent", "")
    return jsonify({"device_info": audio_quality_store.get_device_info(user_agent)})
