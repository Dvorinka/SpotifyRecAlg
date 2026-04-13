"""
Song.link / Odesli API Client - FREE

Song.link provides a free API to map music URLs across different streaming services.
Given a Spotify URL/ID, it can find equivalent tracks on:
- Tidal
- Qobuz
- Amazon Music
- Deezer
- Apple Music
- YouTube Music
- SoundCloud

API Documentation: https://linktree.docs.apiary.io/
Rate Limit: ~10 requests per minute (handled automatically)
"""

import logging
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

# Song.link API base URL
SONGLINK_API_BASE = "https://api.song.link/v1-alpha.1"


@dataclass
class PlatformLink:
    """Link to a track on a specific platform"""

    platform: str
    url: str
    entity_type: str  # track, album, playlist
    id: str | None = None
    native_uri: str | None = None


@dataclass
class CrossPlatformLinks:
    """Cross-platform links for a single track"""

    spotify_id: str
    isrc: str | None
    links: dict[str, PlatformLink]

    # Convenience properties
    @property
    def tidal_url(self) -> str | None:
        return self.links.get("tidal", {}).url if "tidal" in self.links else None

    @property
    def qobuz_url(self) -> str | None:
        return self.links.get("qobuz", {}).url if "qobuz" in self.links else None

    @property
    def amazon_url(self) -> str | None:
        return (
            self.links.get("amazonMusic", {}).url
            if "amazonMusic" in self.links
            else None
        )

    @property
    def deezer_url(self) -> str | None:
        return self.links.get("deezer", {}).url if "deezer" in self.links else None

    @property
    def apple_url(self) -> str | None:
        return (
            self.links.get("appleMusic", {}).url if "appleMusic" in self.links else None
        )

    @property
    def youtube_url(self) -> str | None:
        return self.links.get("youtube", {}).url if "youtube" in self.links else None

    @property
    def youtube_music_url(self) -> str | None:
        return (
            self.links.get("youtubeMusic", {}).url
            if "youtubeMusic" in self.links
            else None
        )

    @property
    def soundcloud_url(self) -> str | None:
        return (
            self.links.get("soundcloud", {}).url if "soundcloud" in self.links else None
        )


@dataclass
class TrackAvailability:
    """Track availability across platforms"""

    spotify_id: str
    isrc: str | None = None
    tidal: bool = False
    qobuz: bool = False
    amazon: bool = False
    deezer: bool = False
    apple: bool = False
    youtube: bool = False
    youtube_music: bool = False
    soundcloud: bool = False
    tidal_url: str | None = None
    qobuz_url: str | None = None
    amazon_url: str | None = None
    deezer_url: str | None = None
    apple_url: str | None = None
    youtube_url: str | None = None
    youtube_music_url: str | None = None
    soundcloud_url: str | None = None


class SongLinkClient:
    """
    Song.link API Client - FREE

    Maps Spotify tracks to other streaming services.
    Rate limited to ~10 requests per minute.
    """

    # Platform name mapping
    PLATFORM_NAMES = {
        "spotify": "Spotify",
        "tidal": "Tidal",
        "qobuz": "Qobuz",
        "amazonMusic": "Amazon Music",
        "deezer": "Deezer",
        "appleMusic": "Apple Music",
        "youtube": "YouTube",
        "youtubeMusic": "YouTube Music",
        "soundcloud": "SoundCloud",
        "napster": "Napster",
        "pandora": "Pandora",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "SwingMusic/1.0 (https://github.com/geoffrey45/swingmusic)",
                "Accept": "application/json",
            }
        )

        # Rate limiting
        self._last_request_time = 0
        self._request_count = 0
        self._count_reset_time = time.time()
        self._min_request_interval = 7.0  # 7 seconds between requests
        self._max_requests_per_minute = 9  # Stay under 10/min limit

    def _rate_limit(self) -> None:
        """Handle rate limiting"""
        now = time.time()

        # Reset counter every minute
        if now - self._count_reset_time >= 60:
            self._request_count = 0
            self._count_reset_time = now

        # Check if we've hit the per-minute limit
        if self._request_count >= self._max_requests_per_minute:
            wait_time = 60 - (now - self._count_reset_time)
            if wait_time > 0:
                logger.debug(f"Song.link rate limit reached, waiting {wait_time:.1f}s")
                time.sleep(wait_time)
                self._request_count = 0
                self._count_reset_time = time.time()

        # Ensure minimum interval between requests
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            wait_time = self._min_request_interval - elapsed
            time.sleep(wait_time)

        self._last_request_time = time.time()
        self._request_count += 1

    def _make_request(self, url: str, params: dict = None) -> dict | None:
        """Make a rate-limited request to Song.link API"""
        self._rate_limit()

        try:
            response = self.session.get(url, params=params, timeout=30)

            if response.status_code == 429:
                # Rate limited - wait and retry once
                retry_after = int(response.headers.get("Retry-After", 15))
                logger.warning(f"Song.link rate limited, waiting {retry_after}s")
                time.sleep(retry_after)
                self._rate_limit()
                response = self.session.get(url, params=params, timeout=30)

            if response.status_code != 200:
                logger.error(f"Song.link API error: HTTP {response.status_code}")
                return None

            return response.json()

        except requests.exceptions.Timeout:
            logger.error("Song.link API timeout")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Song.link API request error: {e}")
            return None
        except Exception as e:
            logger.error(f"Song.link API error: {e}")
            return None

    def get_links_from_spotify_url(
        self, spotify_url: str, region: str = "US"
    ) -> CrossPlatformLinks | None:
        """
        Get cross-platform links from a Spotify URL.

        Args:
            spotify_url: Full Spotify URL (e.g., https://open.spotify.com/track/xxx)
            region: Country code for region-specific availability

        Returns:
            CrossPlatformLinks object with links to all available platforms
        """
        params = {"url": spotify_url}
        if region:
            params["userCountry"] = region

        url = f"{SONGLINK_API_BASE}/links"
        data = self._make_request(url, params)

        if not data:
            return None

        return self._parse_response(data)

    def get_links_from_spotify_id(
        self, spotify_id: str, item_type: str = "track", region: str = "US"
    ) -> CrossPlatformLinks | None:
        """
        Get cross-platform links from a Spotify ID.

        Args:
            spotify_id: Spotify track/album ID
            item_type: Type of item (track, album, playlist)
            region: Country code for region-specific availability

        Returns:
            CrossPlatformLinks object with links to all available platforms
        """
        spotify_url = f"https://open.spotify.com/{item_type}/{spotify_id}"
        return self.get_links_from_spotify_url(spotify_url, region)

    def check_availability(
        self, spotify_id: str, item_type: str = "track", region: str = "US"
    ) -> TrackAvailability:
        """
        Check track availability across platforms.

        Args:
            spotify_id: Spotify track ID
            item_type: Type of item (track, album)
            region: Country code

        Returns:
            TrackAvailability with boolean flags for each platform
        """
        links = self.get_links_from_spotify_id(spotify_id, item_type, region)

        if not links:
            return TrackAvailability(spotify_id=spotify_id)

        return TrackAvailability(
            spotify_id=spotify_id,
            isrc=links.isrc,
            tidal=links.tidal_url is not None,
            qobuz=links.qobuz_url is not None,
            amazon=links.amazon_url is not None,
            deezer=links.deezer_url is not None,
            apple=links.apple_url is not None,
            youtube=links.youtube_url is not None,
            youtube_music=links.youtube_music_url is not None,
            soundcloud=links.soundcloud_url is not None,
            tidal_url=links.tidal_url,
            qobuz_url=links.qobuz_url,
            amazon_url=links.amazon_url,
            deezer_url=links.deezer_url,
            apple_url=links.apple_url,
            youtube_url=links.youtube_url,
            youtube_music_url=links.youtube_music_url,
            soundcloud_url=links.soundcloud_url,
        )

    def get_isrc_from_spotify(self, spotify_id: str, region: str = "US") -> str | None:
        """
        Get ISRC (International Standard Recording Code) from Spotify ID.
        Uses Deezer as intermediary since they provide ISRC in their API.

        Args:
            spotify_id: Spotify track ID
            region: Country code

        Returns:
            ISRC code if found, None otherwise
        """
        links = self.get_links_from_spotify_id(spotify_id, "track", region)

        if links and links.isrc:
            return links.isrc

        # Try to get ISRC from Deezer
        if links and links.deezer_url:
            return self._get_isrc_from_deezer_url(links.deezer_url)

        return None

    def _get_isrc_from_deezer_url(self, deezer_url: str) -> str | None:
        """Extract ISRC from Deezer API using track URL"""
        try:
            # Extract track ID from Deezer URL
            track_id = deezer_url.split("/track/")[-1].split("?")[0]

            response = self.session.get(
                f"https://api.deezer.com/track/{track_id}", timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                return data.get("isrc")

        except Exception as e:
            logger.debug(f"Failed to get ISRC from Deezer: {e}")

        return None

    def _parse_response(self, data: dict) -> CrossPlatformLinks:
        """Parse Song.link API response into CrossPlatformLinks"""
        links = {}
        isrc = None
        spotify_id = None

        # Extract entity unique IDs (contains ISRC)
        entity_ids = data.get("entitiesByUniqueId", {})

        for entity_id, entity_data in entity_ids.items():
            # Extract ISRC from Deezer entity if available
            if (
                "DEEZER" in entity_id.upper()
                or entity_data.get("apiProvider") == "deezer"
            ):
                isrc = entity_data.get("nativeId")

            # Extract Spotify ID
            if entity_data.get("apiProvider") == "spotify":
                spotify_id = entity_data.get("nativeId")

        # Extract platform links
        links_by_platform = data.get("linksByPlatform", {})

        for platform, link_data in links_by_platform.items():
            entity_key = link_data.get("entityUniqueId", "")
            entity_info = entity_ids.get(entity_key, {})

            links[platform] = PlatformLink(
                platform=platform,
                url=link_data.get("url", ""),
                entity_type=link_data.get("type", "track"),
                id=entity_info.get("nativeId"),
                native_uri=entity_info.get("nativeUri"),
            )

        # Fallback: get Spotify ID from URL
        if not spotify_id:
            page_url = data.get("pageUrl", "")
            if "spotify.com" in page_url:
                parts = page_url.split("/")
                if len(parts) > 4:
                    spotify_id = parts[-1].split("?")[0]

        return CrossPlatformLinks(
            spotify_id=spotify_id or "",
            isrc=isrc,
            links=links,
        )

    def get_streaming_urls(self, spotify_id: str, region: str = "US") -> dict[str, str]:
        """
        Get streaming URLs for all available platforms.

        Args:
            spotify_id: Spotify track ID
            region: Country code

        Returns:
            Dict mapping platform names to URLs
        """
        links = self.get_links_from_spotify_id(spotify_id, "track", region)

        if not links:
            return {}

        return {
            platform: link.url for platform, link in links.links.items() if link.url
        }


# Singleton instance
_songlink_client: SongLinkClient | None = None


def get_songlink_client() -> SongLinkClient:
    """Get or create the singleton Song.link client"""
    global _songlink_client
    if _songlink_client is None:
        _songlink_client = SongLinkClient()
    return _songlink_client
