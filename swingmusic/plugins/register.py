import contextlib

from sqlalchemy.exc import IntegrityError

from swingmusic.db.userdata import PluginTable


def register_plugins():
    with contextlib.suppress(IntegrityError):
        PluginTable.insert_one(
            {
                "name": "lyrics_finder",
                "active": True,
                "settings": {
                    "auto_download": True,
                    "overide_unsynced": True,
                    "provider_order": ["lrclib", "musixmatch"],
                },
                "extra": {
                    "description": "Find lyrics from the internet",
                },
            }
        )
