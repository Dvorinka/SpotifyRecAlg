"""
Music Unlocker - Auth-free music metadata service
Wraps swingmusic's reverse-engineered clients
"""

import sys
import json
import logging
from pathlib import Path
from dataclasses import asdict

# Add swingmusic to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "swingmusic"))

from flask import Flask, jsonify, request
from flask_cors import CORS

from services.spotify_web_player_client import SpotifyWebPlayerClient
from services.songlink_client import SongLinkClient
from services.universal_url_parser import UniversalMusicURLParser, MusicService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Clients
spotify_client = SpotifyWebPlayerClient()
songlink_client = SongLinkClient()
url_parser = UniversalMusicURLParser()


def track_to_dict(track):
    """Convert SpotifyTrack to dict"""
    return {
        "id": track.id,
        "title": track.name,
        "artist": track.artists[0]["name"] if track.artists else "Unknown",
        "artists": [a["name"] for a in track.artists],
        "album": track.album.get("name", "") if track.album else "",
        "duration_ms": track.duration_ms,
        "explicit": track.explicit,
        "external_urls": track.external_urls or {},
    }


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/parse", methods=["POST"])
def parse_url():
    """Parse any music service URL"""
    data = request.get_json()
    url = data.get("url", "")

    parsed = url_parser.parse_url(url)
    if not parsed:
        return jsonify({"error": "Unsupported URL"}), 400

    return jsonify({
        "service": parsed.service.value,
        "item_type": parsed.item_type,
        "id": parsed.id,
        "metadata": parsed.metadata or {}
    })


@app.route("/spotify/track/<track_id>", methods=["GET"])
def get_track(track_id):
    """Get track metadata from Spotify (no auth required)"""
    try:
        track = spotify_client.get_track(track_id)
        if not track:
            return jsonify({"error": "Track not found"}), 404

        return jsonify(track_to_dict(track))
    except Exception as e:
        logger.error(f"Error fetching track: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/spotify/album/<album_id>", methods=["GET"])
def get_album(album_id):
    """Get album with tracks from Spotify (no auth required)"""
    try:
        album = spotify_client.get_album(album_id)
        if not album:
            return jsonify({"error": "Album not found"}), 404

        return jsonify({
            "id": album.id,
            "title": album.name,
            "artist": album.artists[0]["name"] if album.artists else "Unknown",
            "tracks": [track_to_dict(t) for t in (album.tracks or [])],
        })
    except Exception as e:
        logger.error(f"Error fetching album: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/spotify/playlist/<playlist_id>", methods=["GET"])
def get_playlist(playlist_id):
    """Get playlist with tracks from Spotify (no auth required)"""
    try:
        playlist = spotify_client.get_playlist(playlist_id)
        if not playlist:
            return jsonify({"error": "Playlist not found"}), 404

        return jsonify({
            "id": playlist.id,
            "title": playlist.name,
            "description": playlist.description,
            "tracks": [track_to_dict(t) for t in (playlist.tracks or [])],
        })
    except Exception as e:
        logger.error(f"Error fetching playlist: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/spotify/search", methods=["POST"])
def search():
    """Search Spotify (no auth required)"""
    data = request.get_json()
    query = data.get("q", "")
    item_type = data.get("type", "track")
    limit = data.get("limit", 10)

    try:
        results = spotify_client.search(query, item_type, limit)
        return jsonify({
            "results": [track_to_dict(r) for r in results]
        })
    except Exception as e:
        logger.error(f"Error searching: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/links/<spotify_id>", methods=["GET"])
def get_links(spotify_id):
    """Get cross-platform links for a Spotify track"""
    try:
        links = songlink_client.get_links_from_spotify_id(spotify_id, "track")
        if not links:
            return jsonify({"error": "Links not found"}), 404

        return jsonify({
            "spotify_id": links.spotify_id,
            "isrc": links.isrc,
            "links": {
                platform: {"url": link.url, "id": link.id}
                for platform, link in links.links.items()
            }
        })
    except Exception as e:
        logger.error(f"Error getting links: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/import", methods=["POST"])
def import_track():
    """Import track from any URL - returns metadata + cross-platform links"""
    data = request.get_json()
    url = data.get("url", "")

    # Parse URL
    parsed = url_parser.parse_url(url)
    if not parsed:
        return jsonify({"error": "Unsupported URL format"}), 400

    try:
        track_data = None

        # Get metadata based on service
        if parsed.service == MusicService.SPOTIFY:
            if parsed.item_type == "track":
                track = spotify_client.get_track(parsed.id)
                if track:
                    track_data = track_to_dict(track)
            elif parsed.item_type == "album":
                album = spotify_client.get_album(parsed.id)
                if album and album.tracks:
                    track_data = track_to_dict(album.tracks[0])
            elif parsed.item_type == "playlist":
                playlist = spotify_client.get_playlist(parsed.id)
                if playlist and playlist.tracks:
                    track_data = track_to_dict(playlist.tracks[0])

        # For other services, we'd need their respective clients
        # For now, return the parsed info

        if not track_data:
            return jsonify({
                "parsed": {
                    "service": parsed.service.value,
                    "type": parsed.item_type,
                    "id": parsed.id,
                },
                "note": "Metadata fetch for this service not yet implemented"
            })

        # Get cross-platform links
        links = songlink_client.get_links_from_spotify_id(track_data["id"], "track")

        return jsonify({
            "track": track_data,
            "links": {
                platform: link.url
                for platform, link in (links.links.items() if links else [])
            } if links else {}
        })

    except Exception as e:
        logger.error(f"Error importing: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
