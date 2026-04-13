"""
Swing Music API package.

The package intentionally avoids eager imports so a broken or optional API
module cannot crash process boot.
"""

from __future__ import annotations

import importlib

_MODULES = {
    "album": "swingmusic.api.album",
    "artist": "swingmusic.api.artist",
    "collections": "swingmusic.api.collections",
    "colors": "swingmusic.api.colors",
    "favorites": "swingmusic.api.favorites",
    "folder": "swingmusic.api.folder",
    "imgserver": "swingmusic.api.imgserver",
    "playlist": "swingmusic.api.playlist",
    "search": "swingmusic.api.search",
    "settings": "swingmusic.api.settings",
    "lyrics": "swingmusic.api.lyrics",
    "plugins": "swingmusic.api.plugins",
    "scrobble": "swingmusic.api.scrobble",
    "home": "swingmusic.api.home",
    "getall": "swingmusic.api.getall",
    "auth": "swingmusic.api.auth",
    "stream": "swingmusic.api.stream",
    "backup_and_restore": "swingmusic.api.backup_and_restore",
    "spotify": "swingmusic.api.spotify",
    "spotify_settings": "swingmusic.api.spotify_settings",
    "enhanced_search": "swingmusic.api.enhanced_search",
    "universal_downloader": "swingmusic.api.universal_downloader",
    "music_catalog": "swingmusic.api.music_catalog",
    "upload": "swingmusic.api.upload",
    "downloads": "swingmusic.api.downloads",
    "setup": "swingmusic.api.setup",
    "plugins_lyrics": "swingmusic.api.plugins.lyrics",
    "plugins_mixes": "swingmusic.api.plugins.mixes",
    "dragonfly": "swingmusic.api.dragonfly",
}


def __getattr__(name: str):
    module_path = _MODULES.get(name)
    if module_path is None:
        raise AttributeError(f"module 'swingmusic.api' has no attribute '{name}'")

    module = importlib.import_module(module_path)
    globals()[name] = module
    return module


__all__ = sorted(_MODULES.keys())
