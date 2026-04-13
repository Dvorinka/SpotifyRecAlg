"""
Universal Music URL Parser for SwingMusic
Supports multiple music streaming services for universal downloading
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MusicService(Enum):
    SPOTIFY = "spotify"
    TIDAL = "tidal"
    APPLE_MUSIC = "apple_music"
    YOUTUBE_MUSIC = "youtube_music"
    YOUTUBE = "youtube"
    SOUNDCLOUD = "soundcloud"
    DEEZER = "deezer"
    BANDCAMP = "bandcamp"
    MUSICBRAINZ = "musicbrainz"
    DISCOGS = "discogs"


@dataclass
class ParsedURL:
    """Represents a parsed music service URL"""

    service: MusicService
    url: str
    item_type: str  # track, album, playlist, artist, etc.
    id: str
    metadata: dict[str, Any] = None


class UniversalMusicURLParser:
    """Universal parser for music service URLs"""

    def __init__(self):
        self.patterns = {
            MusicService.SPOTIFY: [
                r"https://open\.spotify\.com/(track|album|playlist|artist|user)/([a-zA-Z0-9]+)",
                r"https://spotify\.link/([a-zA-Z0-9]+)",  # Short links
            ],
            MusicService.TIDAL: [
                r"https://tidal\.com/(browse|track|album|playlist|artist)/(\d+)",
                r"https://tidal\.com/browse/(album|track|playlist|artist)/(\d+)",
                r"https://listen\.tidal\.com/(browse|track|album|playlist|artist)/(\d+)",
            ],
            MusicService.APPLE_MUSIC: [
                r"https://music\.apple\.com/([a-z]{2})/song/([^/]+)/(\d+)",
                r"https://music\.apple\.com/([a-z]{2})/album/(.*?)/(\d+)",
                r"https://music\.apple\.com/([a-z]{2})/playlist/(.*?)/pl\.(.+)",
                r"https://music\.apple\.com/([a-z]{2})/artist/(.*?)/(\d+)",
            ],
            MusicService.YOUTUBE_MUSIC: [
                r"https://music\.youtube\.com/(watch|playlist|channel)(\?[^#]*)",
                r"https://youtube\.com/music/(watch|playlist|channel)(\?[^#]*)",
            ],
            MusicService.YOUTUBE: [
                r"https://www\.youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",
                r"https://youtu\.be/([a-zA-Z0-9_-]+)",
                r"https://www\.youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)",
                r"https://www\.youtube\.com/channel/([a-zA-Z0-9_-]+)",
                r"https://www\.youtube\.com/c/([a-zA-Z0-9_-]+)",
            ],
            MusicService.SOUNDCLOUD: [
                r"https://soundcloud\.com/([^/]+)/([^/]+)",
                r"https://soundcloud\.com/([^/]+)/sets/([^/]+)",
            ],
            MusicService.DEEZER: [
                r"https://www\.deezer\.com/(en|fr|de|es|it|pt|nl|ru|ja)/(track|album|playlist|artist)/(\d+)",
                r"https://deezer\.page\.link/(track|album|playlist|artist)/(\d+)",
                r"https://link\.deezer\.com/s/([a-zA-Z0-9_-]+)",
            ],
            MusicService.BANDCAMP: [
                r"https://([a-zA-Z0-9-]+)\.bandcamp\.com/(track|album)/(.+)",
                r"https://bandcamp\.com/search\?q=(.+)",
            ],
            MusicService.MUSICBRAINZ: [
                r"https://musicbrainz\.org/(recording|release|release-group|artist)/([a-f0-9-]+)",
                r"https://musicbrainz\.org/doc/([a-f0-9-]+)",  # API docs
                r"https://musicbrainz\.org/artist/([a-f0-9-]+)",  # Direct artist links
                r"https://musicbrainz\.org/release-group/([a-f0-9-]+)",  # Release groups
                r"https://musicbrainz\.org/label/([a-f0-9-]+)",  # Record labels
                r"https://musicbrainz\.org/search\?query=([^&]+)",  # Search queries
            ],
            MusicService.DISCOGS: [
                r"https://www\.discogs\.com/(release|master|artist)/(\d+)",
            ],
        }

    def parse_url(self, url: str) -> ParsedURL | None:
        """
        Parse a music service URL and extract service, type, and ID

        Args:
            url: The URL to parse

        Returns:
            ParsedURL object if successful, None otherwise
        """
        if not url or not isinstance(url, str):
            return None

        url = url.strip()

        # Try each service pattern
        for service, patterns in self.patterns.items():
            for pattern in patterns:
                match = re.match(pattern, url, re.IGNORECASE)
                if match:
                    return self._extract_service_info(service, match, url)

        return None

    def _extract_service_info(
        self, service: MusicService, match: re.Match, url: str
    ) -> ParsedURL:
        """Extract service-specific information from regex match"""
        groups = match.groups()

        if service == MusicService.SPOTIFY:
            if len(groups) == 2:
                item_type, item_id = groups
                return ParsedURL(service, url, item_type, item_id)
            elif len(groups) == 1:  # Short link
                # Would need to resolve short link
                return ParsedURL(service, url, "short", groups[0])

        elif service == MusicService.TIDAL:
            item_type, item_id = groups
            return ParsedURL(service, url, item_type, item_id)

        elif service == MusicService.APPLE_MUSIC:
            if len(groups) >= 2:
                item_type = self._map_apple_music_type(groups[0])
                item_id = groups[-1]  # Last group is usually the ID
                return ParsedURL(
                    service,
                    url,
                    item_type,
                    item_id,
                    {
                        "region": groups[0] if len(groups) > 2 else "us",
                        "name": groups[1] if len(groups) > 2 else "",
                    },
                )

        elif service == MusicService.YOUTUBE_MUSIC:
            item_type = self._extract_youtube_type(groups[0], groups[1])
            item_id = self._extract_youtube_id(groups[1])
            return ParsedURL(service, url, item_type, item_id)

        elif service == MusicService.YOUTUBE:
            if "watch" in url:
                video_id = self._extract_youtube_id(url)
                return ParsedURL(service, url, "video", video_id)
            elif "playlist" in url:
                playlist_id = self._extract_youtube_playlist_id(url)
                return ParsedURL(service, url, "playlist", playlist_id)
            elif "channel" in url or "/c/" in url:
                channel_id = self._extract_youtube_channel_id(url)
                return ParsedURL(service, url, "channel", channel_id)

        elif service == MusicService.SOUNDCLOUD:
            if len(groups) == 2:
                if groups[1] == "sets":
                    item_type = "playlist"
                else:
                    item_type = "track" if groups[1] else "artist"
                item_id = f"{groups[0]}/{groups[1]}"
                return ParsedURL(service, url, item_type, item_id)

        elif service == MusicService.DEEZER:
            if len(groups) == 2:
                item_type, item_id = groups
            else:
                # Short link format: link.deezer.com/s/ID
                item_type = "track"  # Default to track for short links
                item_id = groups[0] if groups else ""
            return ParsedURL(service, url, item_type, item_id)

        elif service == MusicService.BANDCAMP:
            if len(groups) == 3:
                item_type, item_name = groups[1], groups[2]
                item_id = f"{groups[0]}/{item_type}/{item_name}"
                return ParsedURL(service, url, item_type, item_id)

        elif service == MusicService.MUSICBRAINZ:
            if len(groups) == 2:
                item_type, item_id = groups
            elif len(groups) == 1:
                # Handle special cases like doc/, artist/, etc.
                url.split("/")[-2] if "/" in url else ""
                if "doc/" in url:
                    item_type = "doc"
                elif "artist/" in url:
                    item_type = "artist"
                elif "label/" in url:
                    item_type = "label"
                elif "search" in url:
                    item_type = "search"
                    # Extract query from search URL
                    query_match = re.search(r"query=([^&]+)", url)
                    item_id = query_match.group(1) if query_match else groups[0]
                else:
                    item_type = groups[0] if groups else "unknown"
                    item_id = groups[0] if groups else ""
            return ParsedURL(service, url, item_type, item_id)

        elif service == MusicService.DISCOGS:
            item_type, item_id = groups
            return ParsedURL(service, url, item_type, item_id)

        return ParsedURL(service, url, "unknown", "")

    def _map_apple_music_type(self, type_str: str) -> str:
        """Map Apple Music URL types to standard types"""
        mapping = {
            "album": "album",
            "playlist": "playlist",
            "artist": "artist",
            "song": "song",
        }
        return mapping.get(type_str, "unknown")

    def _extract_youtube_type(self, path: str, query: str) -> str:
        """Extract YouTube content type from URL"""
        if "watch" in path or "v=" in query:
            return "watch"
        elif "playlist" in path or "list=" in query:
            return "playlist"
        elif "channel" in path:
            return "channel"
        return "unknown"

    def _extract_youtube_id(self, url: str) -> str:
        """Extract YouTube video or channel ID from URL"""
        # Video ID
        video_match = re.search(r"[?&]v=([a-zA-Z0-9_-]+)", url)
        if video_match:
            return video_match.group(1)

        # Short URL
        short_match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
        if short_match:
            return short_match.group(1)

        # Channel ID
        channel_match = re.search(r"channel/([a-zA-Z0-9_-]+)", url)
        if channel_match:
            return channel_match.group(1)

        # Custom channel
        custom_match = re.search(r"/c/([a-zA-Z0-9_-]+)", url)
        if custom_match:
            return custom_match.group(1)

        return ""

    def _extract_youtube_playlist_id(self, url: str) -> str:
        """Extract YouTube playlist ID from URL"""
        match = re.search(r"[?&]list=([a-zA-Z0-9_-]+)", url)
        return match.group(1) if match else ""

    def _extract_youtube_channel_id(self, url: str) -> str:
        """Extract YouTube channel ID from URL"""
        # Handle both /channel/ and /c/ formats
        channel_match = re.search(r"/(channel|c)/([a-zA-Z0-9_-]+)", url)
        return channel_match.group(2) if channel_match else ""

    def get_supported_services(self) -> list[dict[str, Any]]:
        """Get list of supported services with their info"""
        return [
            {
                "id": MusicService.SPOTIFY.value,
                "name": "Spotify",
                "url_patterns": self.patterns[MusicService.SPOTIFY],
                "supported_types": ["track", "album", "playlist", "artist"],
                "features": ["metadata", "download", "playlist"],
            },
            {
                "id": MusicService.TIDAL.value,
                "name": "Tidal",
                "url_patterns": self.patterns[MusicService.TIDAL],
                "supported_types": ["track", "album", "playlist", "artist"],
                "features": ["metadata", "download", "playlist"],
            },
            {
                "id": MusicService.APPLE_MUSIC.value,
                "name": "Apple Music",
                "url_patterns": self.patterns[MusicService.APPLE_MUSIC],
                "supported_types": ["track", "album", "playlist", "artist"],
                "features": ["metadata", "download", "playlist"],
            },
            {
                "id": MusicService.YOUTUBE_MUSIC.value,
                "name": "YouTube Music",
                "url_patterns": self.patterns[MusicService.YOUTUBE_MUSIC],
                "supported_types": ["video", "playlist", "channel"],
                "features": ["metadata", "download"],
            },
            {
                "id": MusicService.YOUTUBE.value,
                "name": "YouTube",
                "url_patterns": self.patterns[MusicService.YOUTUBE],
                "supported_types": ["video", "playlist", "channel"],
                "features": ["metadata", "download"],
            },
            {
                "id": MusicService.SOUNDCLOUD.value,
                "name": "SoundCloud",
                "url_patterns": self.patterns[MusicService.SOUNDCLOUD],
                "supported_types": ["track", "playlist", "artist"],
                "features": ["metadata", "download"],
            },
            {
                "id": MusicService.DEEZER.value,
                "name": "Deezer",
                "url_patterns": self.patterns[MusicService.DEEZER],
                "supported_types": ["track", "album", "playlist", "artist"],
                "features": ["metadata", "download", "playlist"],
            },
            {
                "id": MusicService.BANDCAMP.value,
                "name": "Bandcamp",
                "url_patterns": self.patterns[MusicService.BANDCAMP],
                "supported_types": ["track", "album"],
                "features": ["metadata", "download"],
            },
            {
                "id": MusicService.MUSICBRAINZ.value,
                "name": "MusicBrainz",
                "url_patterns": self.patterns[MusicService.MUSICBRAINZ],
                "supported_types": ["recording", "release", "artist"],
                "features": ["metadata"],
            },
            {
                "id": MusicService.DISCOGS.value,
                "name": "Discogs",
                "url_patterns": self.patterns[MusicService.DISCOGS],
                "supported_types": ["release", "artist"],
                "features": ["metadata"],
            },
        ]

    def validate_url(self, url: str) -> bool:
        """Validate if URL is from a supported service"""
        return self.parse_url(url) is not None

    def get_service_from_url(self, url: str) -> MusicService | None:
        """Get service type from URL without full parsing"""
        if not url:
            return None

        url_lower = url.lower()

        if "spotify.com" in url_lower or "spotify.link" in url_lower:
            return MusicService.SPOTIFY
        elif "tidal.com" in url_lower or "listen.tidal.com" in url_lower:
            return MusicService.TIDAL
        elif "music.apple.com" in url_lower:
            return MusicService.APPLE_MUSIC
        elif "music.youtube.com" in url_lower:
            return MusicService.YOUTUBE_MUSIC
        elif "youtube.com" in url_lower or "youtu.be" in url_lower:
            return MusicService.YOUTUBE
        elif "soundcloud.com" in url_lower:
            return MusicService.SOUNDCLOUD
        elif "deezer.com" in url_lower or "deezer.page.link" in url_lower:
            return MusicService.DEEZER
        elif "bandcamp.com" in url_lower:
            return MusicService.BANDCAMP
        elif "musicbrainz.org" in url_lower:
            return MusicService.MUSICBRAINZ
        elif "discogs.com" in url_lower:
            return MusicService.DISCOGS

        return None


# Global instance
universal_url_parser = UniversalMusicURLParser()
