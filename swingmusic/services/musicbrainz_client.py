"""
MusicBrainz API v2 Client for Universal Music Downloader
Provides comprehensive music metadata from MusicBrainz database
"""

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class MusicBrainzRecording:
    """MusicBrainz recording metadata"""

    mbid: str
    title: str
    artist: str
    artist_mbid: str | None = None
    release: str | None = None
    release_mbid: str | None = None
    isrc: str | None = None
    duration: int | None = None
    position: int | None = None
    genres: list[str] = None
    release_date: str | None = None
    country: str | None = None
    tags: list[str] = None
    cover_art: str | None = None


@dataclass
class MusicBrainzArtist:
    """MusicBrainz artist metadata"""

    mbid: str
    name: str
    sort_name: str | None = None
    disambiguation: str | None = None
    country: str | None = None
    life_span: dict[str, str] | None = None
    genres: list[str] = None
    tags: list[str] = None
    rating: float | None = None


class MusicBrainzClient:
    """MusicBrainz API v2 client"""

    def __init__(self, app_name: str = "SwingMusic", app_version: str = "1.0.0"):
        self.base_url = "https://musicbrainz.org/ws/2"
        self.app_name = app_name
        self.app_version = app_version
        self.session = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    def _build_url(self, endpoint: str, params: dict[str, str] = None) -> str:
        """Build MusicBrainz API URL"""
        url = f"{self.base_url}/{endpoint}"
        if params:
            param_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url += f"?{param_string}"
        return url

    async def lookup_recording(
        self, mbid: str, includes: list[str] = None
    ) -> MusicBrainzRecording | None:
        """Lookup detailed recording information"""
        try:
            session = await self._get_session()

            params = {}
            if includes:
                params["inc"] = ",".join(includes)

            url = self._build_url(f"recording/{mbid}", params)

            headers = {
                "User-Agent": f"{self.app_name}/{self.app_version}",
                "Accept": "application/json",
            }

            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_recording_response(data)
                else:
                    logger.warning(
                        f"MusicBrainz recording lookup failed: {response.status}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Error looking up MusicBrainz recording: {e}")
            return None

    async def lookup_artist(
        self, mbid: str, includes: list[str] = None
    ) -> MusicBrainzArtist | None:
        """Lookup detailed artist information"""
        try:
            session = await self._get_session()

            params = {}
            if includes:
                params["inc"] = ",".join(includes)

            url = self._build_url(f"artist/{mbid}", params)

            headers = {
                "User-Agent": f"{self.app_name}/{self.app_version}",
                "Accept": "application/json",
            }

            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_artist_response(data)
                else:
                    logger.warning(
                        f"MusicBrainz artist lookup failed: {response.status}"
                    )
                    return None

        except Exception as e:
            logger.error(f"Error looking up MusicBrainz artist: {e}")
            return None

    async def search_recordings(
        self, query: str, artist: str = None, limit: int = 25
    ) -> list[MusicBrainzRecording]:
        """Search for recordings"""
        try:
            session = await self._get_session()

            params = {"query": f'"{query}"', "limit": str(limit)}

            if artist:
                params["artist"] = f'"{artist}"'

            url = self._build_url("recording", params)

            headers = {
                "User-Agent": f"{self.app_name}/{self.app_version}",
                "Accept": "application/json",
            }

            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._parse_recording_list_response(data)
                else:
                    logger.warning(
                        f"MusicBrainz recording search failed: {response.status}"
                    )
                    return []

        except Exception as e:
            logger.error(f"Error searching MusicBrainz recordings: {e}")
            return []

    async def get_artist_releases(
        self, mbid: str, release_types: list[str] = None
    ) -> list[str]:
        """Get all releases for an artist"""
        try:
            session = await self._get_session()

            params = {}
            if release_types:
                params["type"] = ",".join(release_types)

            url = self._build_url("release", {"artist": mbid, **params})

            headers = {
                "User-Agent": f"{self.app_name}/{self.app_version}",
                "Accept": "application/json",
            }

            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    releases = data.get("releases", [])
                    return [release.get("id", "") for release in releases]
                else:
                    logger.warning(
                        f"MusicBrainz artist releases failed: {response.status}"
                    )
                    return []

        except Exception as e:
            logger.error(f"Error getting MusicBrainz artist releases: {e}")
            return []

    def _parse_recording_response(
        self, data: dict[str, Any]
    ) -> MusicBrainzRecording | None:
        """Parse MusicBrainz recording response"""
        try:
            recording_data = data.get("recording")
            if not recording_data:
                return None

            # Extract basic info
            title = recording_data.get("title", "")

            # Extract artist info
            artist_credit = recording_data.get("artist-credit", [])
            artist = (
                artist_credit[0].get("artist", {}).get("name", "")
                if artist_credit
                else ""
            )
            artist_mbid = (
                artist_credit[0].get("artist", {}).get("id") if artist_credit else None
            )

            # Extract release info
            release_list = recording_data.get("release-list", [])
            release = release_list[0] if release_list else None
            release_title = release.get("title", "") if release else None
            release_mbid = release.get("id") if release else None

            # Extract ISRC
            isrc_list = recording_data.get("isrc-list", [])
            isrc = isrc_list[0] if isrc_list else None

            # Extract duration
            duration = recording_data.get("length")

            # Extract tags and genres
            tag_list = recording_data.get("tag-list", [])
            tags = [tag.get("name", "") for tag in tag_list]

            # Extract release info
            release_info = recording_data.get("release", {})
            release_date = release_info.get("date")
            country = release_info.get("country")

            # Extract cover art
            cover_art = None
            if release:
                cover_art_archive = release.get("cover-art-archive", [])
                if cover_art_archive:
                    cover_art = cover_art_archive[0].get("image")

            return MusicBrainzRecording(
                mbid=data.get("id", ""),
                title=title,
                artist=artist,
                artist_mbid=artist_mbid,
                release=release_title,
                release_mbid=release_mbid,
                isrc=isrc,
                duration=duration,
                position=recording_data.get("position"),
                genres=tags,
                release_date=release_date,
                country=country,
                tags=tags,
                cover_art=cover_art,
            )

        except Exception as e:
            logger.error(f"Error parsing MusicBrainz recording response: {e}")
            return None

    def _parse_artist_response(self, data: dict[str, Any]) -> MusicBrainzArtist | None:
        """Parse MusicBrainz artist response"""
        try:
            artist_data = data.get("artist")
            if not artist_data:
                return None

            name = artist_data.get("name", "")
            sort_name = artist_data.get("sort-name")
            disambiguation = artist_data.get("disambiguation")
            country = artist_data.get("country")

            # Extract life span
            life_span = artist_data.get("life-span")

            # Extract tags and genres
            tag_list = artist_data.get("tag-list", [])
            tags = [tag.get("name", "") for tag in tag_list]

            # Extract rating
            rating = artist_data.get("rating", {}).get("value")

            return MusicBrainzArtist(
                mbid=data.get("id", ""),
                name=name,
                sort_name=sort_name,
                disambiguation=disambiguation,
                country=country,
                life_span=life_span,
                genres=tags,
                tags=tags,
                rating=rating,
            )

        except Exception as e:
            logger.error(f"Error parsing MusicBrainz artist response: {e}")
            return None

    def _parse_recording_list_response(
        self, data: dict[str, Any]
    ) -> list[MusicBrainzRecording]:
        """Parse MusicBrainz recording list response"""
        try:
            recordings = []
            recording_list = data.get("recordings", [])

            for recording_data in recording_list:
                recording = self._parse_recording_response(
                    {"recording": recording_data}
                )
                if recording:
                    recordings.append(recording)

            return recordings

        except Exception as e:
            logger.error(f"Error parsing MusicBrainz recording list: {e}")
            return []

    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()


# Singleton instance for easy access
_musicbrainz_client: MusicBrainzClient | None = None


def get_musicbrainz_client() -> MusicBrainzClient:
    """Get or create the MusicBrainz client"""
    global _musicbrainz_client
    if _musicbrainz_client is None:
        _musicbrainz_client = MusicBrainzClient()
    return _musicbrainz_client


# Global instance
musicbrainz_client = MusicBrainzClient()
