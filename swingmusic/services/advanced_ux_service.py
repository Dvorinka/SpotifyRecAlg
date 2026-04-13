"""
Advanced UX Service

This service provides enhanced user experience features including:
- AI-powered search suggestions and recommendations
- Enhanced search interface with smart filters
- Download integration throughout the UI
- Contextual suggestions based on user behavior
- Personalized content discovery
"""

import datetime
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from swingmusic.db import db
from swingmusic.models.artist import Artist
from swingmusic.models.track import Track
from swingmusic.services.music_catalog import music_catalog_service
from swingmusic.utils.ai_recommender import AIRecommender
from swingmusic.utils.behavior_tracker import BehaviorTracker

logger = logging.getLogger(__name__)


class SuggestionType(Enum):
    """Types of suggestions"""

    SEARCH_QUERY = "search_query"
    TRACK = "track"
    ARTIST = "artist"
    ALBUM = "album"
    PLAYLIST = "playlist"
    GENRE = "genre"
    MOOD = "mood"
    ACTIVITY = "activity"


class SearchContext(Enum):
    """Search context for suggestions"""

    GENERAL = "general"
    DISCOVERY = "discovery"
    DOWNLOAD = "download"
    PLAYLIST = "playlist"
    OFFLINE = "offline"
    SOCIAL = "social"


@dataclass
class SearchSuggestion:
    """Search suggestion with metadata"""

    id: str
    type: SuggestionType
    title: str
    subtitle: str | None
    image_url: str | None
    url: str | None
    metadata: dict[str, Any]
    relevance_score: float
    context: SearchContext
    created_at: datetime.datetime


@dataclass
class UserBehavior:
    """User behavior patterns for personalization"""

    user_id: int
    favorite_genres: list[str]
    favorite_artists: list[str]
    listening_patterns: dict[str, Any]
    search_history: list[dict[str, Any]]
    download_preferences: dict[str, Any]
    interaction_patterns: dict[str, Any]
    last_updated: datetime.datetime


@dataclass
class SearchFilter:
    """Enhanced search filter"""

    filter_id: str
    name: str
    type: str  # genre, mood, year, quality, duration, etc.
    options: list[dict[str, Any]]
    is_active: bool
    is_multi_select: bool


class AdvancedUXService:
    """Service for enhanced user experience features"""

    def __init__(self):
        self.ai_recommender = AIRecommender()
        self.behavior_tracker = BehaviorTracker()
        self.search_history = defaultdict(list)
        self.suggestion_cache = {}
        self.user_behaviors = {}

        # Search configuration
        self.max_suggestions = 10
        self.search_history_limit = 100
        self.behavior_update_interval = 3600  # 1 hour

    async def get_search_suggestions(
        self,
        user_id: int,
        query: str,
        context: SearchContext = SearchContext.GENERAL,
        limit: int = 10,
    ) -> list[SearchSuggestion]:
        """
        Get intelligent search suggestions based on query and context

        Args:
            user_id: User ID
            query: Search query
            context: Search context
            limit: Maximum suggestions to return

        Returns:
            List of search suggestions
        """
        try:
            # Clean and normalize query
            clean_query = self._clean_search_query(query)

            if len(clean_query) < 2:
                return await self._get_default_suggestions(user_id, context, limit)

            # Generate suggestions from multiple sources
            suggestions = []

            # 1. Track suggestions from local library
            local_tracks = await self._get_local_track_suggestions(
                user_id, clean_query, limit // 3
            )
            suggestions.extend(local_tracks)

            # 2. Artist suggestions
            artists = await self._get_artist_suggestions(
                user_id, clean_query, limit // 4
            )
            suggestions.extend(artists)

            # 3. Album suggestions
            albums = await self._get_album_suggestions(user_id, clean_query, limit // 4)
            suggestions.extend(albums)

            # 4. Global catalog suggestions
            if context in [SearchContext.DISCOVERY, SearchContext.DOWNLOAD]:
                global_suggestions = await self._get_global_suggestions(
                    user_id, clean_query, limit // 3
                )
                suggestions.extend(global_suggestions)

            # 5. Behavior-based suggestions
            behavior_suggestions = await self._get_behavior_suggestions(
                user_id, clean_query, context, limit // 4
            )
            suggestions.extend(behavior_suggestions)

            # Sort by relevance and limit
            suggestions.sort(key=lambda x: x.relevance_score, reverse=True)
            suggestions = suggestions[:limit]

            # Log search for learning
            await self._log_search_query(user_id, query, context, suggestions)

            return suggestions

        except Exception as e:
            logger.error(f"Error getting search suggestions: {e}")
            return []

    async def get_discovery_recommendations(
        self, user_id: int, recommendation_type: str = "mixed", limit: int = 20
    ) -> list[SearchSuggestion]:
        """
        Get personalized discovery recommendations

        Args:
            user_id: User ID
            recommendation_type: Type of recommendations (tracks, artists, albums, mixed)
            limit: Maximum recommendations to return

        Returns:
            List of discovery recommendations
        """
        try:
            # Get user behavior data
            behavior = await self._get_user_behavior(user_id)

            recommendations = []

            # Generate recommendations based on type
            if recommendation_type in ["tracks", "mixed"]:
                track_recs = await self._get_track_recommendations(
                    user_id, behavior, limit // 2
                )
                recommendations.extend(track_recs)

            if recommendation_type in ["artists", "mixed"]:
                artist_recs = await self._get_artist_recommendations(
                    user_id, behavior, limit // 2
                )
                recommendations.extend(artist_recs)

            if recommendation_type in ["albums", "mixed"]:
                album_recs = await self._get_album_recommendations(
                    user_id, behavior, limit // 2
                )
                recommendations.extend(album_recs)

            # Sort by relevance and limit
            recommendations.sort(key=lambda x: x.relevance_score, reverse=True)
            recommendations = recommendations[:limit]

            return recommendations

        except Exception as e:
            logger.error(f"Error getting discovery recommendations: {e}")
            return []

    async def get_contextual_suggestions(
        self, user_id: int, current_track_id: str, context_type: str
    ) -> list[SearchSuggestion]:
        """
        Get contextual suggestions based on current track

        Args:
            user_id: User ID
            current_track_id: Currently playing track ID
            context_type: Type of context (similar, same_artist, same_genre, etc.)

        Returns:
            List of contextual suggestions
        """
        try:
            suggestions = []

            # Get current track information
            current_track = await self._get_track_info(current_track_id)
            if not current_track:
                return []

            # Generate suggestions based on context type
            if context_type == "similar":
                similar_suggestions = await self._get_similar_track_suggestions(
                    user_id, current_track, 10
                )
                suggestions.extend(similar_suggestions)

            elif context_type == "same_artist":
                artist_suggestions = await self._get_same_artist_suggestions(
                    user_id, current_track["artist"], 10
                )
                suggestions.extend(artist_suggestions)

            elif context_type == "same_genre":
                genre_suggestions = await self._get_same_genre_suggestions(
                    user_id, current_track, 10
                )
                suggestions.extend(genre_suggestions)

            elif context_type == "popular":
                popular_suggestions = await self._get_popular_suggestions(user_id, 10)
                suggestions.extend(popular_suggestions)

            return suggestions[:10]

        except Exception as e:
            logger.error(f"Error getting contextual suggestions: {e}")
            return []

    async def get_download_suggestions(
        self, user_id: int, query: str = "", limit: int = 15
    ) -> list[SearchSuggestion]:
        """
        Get download-specific suggestions with universal downloader integration

        Args:
            user_id: User ID
            query: Search query
            limit: Maximum suggestions to return

        Returns:
            List of download suggestions
        """
        try:
            suggestions = []

            # If query is provided, search for matching content
            if query:
                # Search global catalog
                catalog_results = await music_catalog_service.search_global_catalog(
                    query, "all", limit
                )

                # Convert to suggestions
                for track in catalog_results.tracks[: limit // 2]:
                    suggestion = SearchSuggestion(
                        id=f"track_{track.spotify_id}",
                        type=SuggestionType.TRACK,
                        title=track.title,
                        subtitle=f"{track.artist} • {track.album}",
                        image_url=track.image_url,
                        url=f"/download/{track.spotify_id}",
                        metadata={
                            "spotify_id": track.spotify_id,
                            "artist": track.artist,
                            "album": track.album,
                            "duration": track.duration_ms,
                            "popularity": track.popularity,
                            "preview_url": track.preview_url,
                        },
                        relevance_score=self._calculate_download_relevance(
                            track, user_id
                        ),
                        context=SearchContext.DOWNLOAD,
                        created_at=datetime.datetime.utcnow(),
                    )
                    suggestions.append(suggestion)

                # Add artist suggestions
                for artist in catalog_results.artists[: limit // 4]:
                    suggestion = SearchSuggestion(
                        id=f"artist_{artist.spotify_id}",
                        type=SuggestionType.ARTIST,
                        title=artist.title,
                        subtitle=f"{artist.popularity} popularity",
                        image_url=artist.image_url,
                        url=f"/artist/{artist.spotify_id}",
                        metadata={
                            "spotify_id": artist.spotify_id,
                            "popularity": artist.popularity,
                            "followers": artist.data.get("followers", 0),
                        },
                        relevance_score=self._calculate_download_relevance(
                            artist, user_id
                        ),
                        context=SearchContext.DOWNLOAD,
                        created_at=datetime.datetime.utcnow(),
                    )
                    suggestions.append(suggestion)

                # Add album suggestions
                for album in catalog_results.albums[: limit // 4]:
                    suggestion = SearchSuggestion(
                        id=f"album_{album.spotify_id}",
                        type=SuggestionType.ALBUM,
                        title=album.title,
                        subtitle=f"{album.artist} • {album.data.get('total_tracks', 0)} tracks",
                        image_url=album.image_url,
                        url=f"/album/{album.spotify_id}",
                        metadata={
                            "spotify_id": album.spotify_id,
                            "artist": album.artist,
                            "total_tracks": album.data.get("total_tracks", 0),
                            "release_date": album.release_date,
                            "album_type": album.data.get("album_type", "album"),
                        },
                        relevance_score=self._calculate_download_relevance(
                            album, user_id
                        ),
                        context=SearchContext.DOWNLOAD,
                        created_at=datetime.datetime.utcnow(),
                    )
                    suggestions.append(suggestion)

            # Add trending/popular suggestions if no query
            if not query:
                trending_suggestions = await self._get_trending_download_suggestions(
                    user_id, limit
                )
                suggestions.extend(trending_suggestions)

            # Sort by relevance and limit
            suggestions.sort(key=lambda x: x.relevance_score, reverse=True)
            suggestions = suggestions[:limit]

            return suggestions

        except Exception as e:
            logger.error(f"Error getting download suggestions: {e}")
            return []

    async def get_enhanced_search_filters(self, user_id: int) -> list[SearchFilter]:
        """
        Get enhanced search filters with user personalization

        Args:
            user_id: User ID

        Returns:
            List of enhanced search filters
        """
        try:
            filters = []

            # Get user behavior for personalization
            behavior = await self._get_user_behavior(user_id)

            # Genre filter
            genre_options = []
            popular_genres = await self._get_popular_genres(user_id)
            for genre in popular_genres:
                genre_options.append(
                    {
                        "value": genre,
                        "label": genre.title(),
                        "count": await self._get_genre_track_count(genre),
                        "is_favorite": genre in behavior.favorite_genres,
                    }
                )

            filters.append(
                SearchFilter(
                    filter_id="genre",
                    name="Genre",
                    type="genre",
                    options=genre_options,
                    is_active=False,
                    is_multi_select=True,
                )
            )

            # Mood filter
            mood_options = [
                {"value": "energetic", "label": "Energetic", "icon": "zap"},
                {"value": "relaxed", "label": "Relaxed", "icon": "leaf"},
                {"value": "happy", "label": "Happy", "icon": "smile"},
                {"value": "sad", "label": "Sad", "icon": "frown"},
                {"value": "focused", "label": "Focused", "icon": "brain"},
                {"value": "workout", "label": "Workout", "icon": "dumbbell"},
            ]

            filters.append(
                SearchFilter(
                    filter_id="mood",
                    name="Mood",
                    type="mood",
                    options=mood_options,
                    is_active=False,
                    is_multi_select=False,
                )
            )

            # Year filter
            current_year = datetime.datetime.now().year
            year_options = []
            for year_offset in range(0, 10):
                year = current_year - year_offset
                year_options.append(
                    {
                        "value": str(year),
                        "label": str(year),
                        "count": await self._get_year_track_count(year),
                    }
                )

            filters.append(
                SearchFilter(
                    filter_id="year",
                    name="Year",
                    type="year",
                    options=year_options,
                    is_active=False,
                    is_multi_select=True,
                )
            )

            # Quality filter
            quality_options = [
                {"value": "lossless", "label": "Lossless (FLAC)", "icon": "gem"},
                {"value": "high", "label": "High (320kbps)", "icon": "star"},
                {"value": "medium", "label": "Medium (256kbps)", "icon": "music"},
                {"value": "low", "label": "Low (128kbps)", "icon": "headphones"},
            ]

            filters.append(
                SearchFilter(
                    filter_id="quality",
                    name="Audio Quality",
                    type="quality",
                    options=quality_options,
                    is_active=False,
                    is_multi_select=False,
                )
            )

            # Duration filter
            duration_options = [
                {"value": "short", "label": "Short (< 2 min)", "max_seconds": 120},
                {
                    "value": "medium",
                    "label": "Medium (2-4 min)",
                    "min_seconds": 120,
                    "max_seconds": 240,
                },
                {"value": "long", "label": "Long (> 4 min)", "min_seconds": 240},
            ]

            filters.append(
                SearchFilter(
                    filter_id="duration",
                    name="Duration",
                    type="duration",
                    options=duration_options,
                    is_active=False,
                    is_multi_select=False,
                )
            )

            return filters

        except Exception as e:
            logger.error(f"Error getting enhanced search filters: {e}")
            return []

    async def update_user_behavior(
        self, user_id: int, interaction_data: dict[str, Any]
    ):
        """
        Update user behavior based on interactions

        Args:
            user_id: User ID
            interaction_data: Interaction data
        """
        try:
            # Get existing behavior
            behavior = await self._get_user_behavior(user_id)

            # Update based on interaction type
            interaction_type = interaction_data.get("type")

            if interaction_type == "search":
                await self._update_search_behavior(behavior, interaction_data)
            elif interaction_type == "play":
                await self._update_play_behavior(behavior, interaction_data)
            elif interaction_type == "download":
                await self._update_download_behavior(behavior, interaction_data)
            elif interaction_type == "like":
                await self._update_like_behavior(behavior, interaction_data)

            # Save updated behavior
            behavior.last_updated = datetime.datetime.utcnow()
            self.user_behaviors[user_id] = behavior

            # Periodically save to database
            if (
                datetime.datetime.utcnow().timestamp() % self.behavior_update_interval
                < 60
            ):
                await self._save_user_behavior(behavior)

        except Exception as e:
            logger.error(f"Error updating user behavior: {e}")

    # Private helper methods

    def _clean_search_query(self, query: str) -> str:
        """Clean and normalize search query"""
        if not query:
            return ""

        # Remove special characters and normalize whitespace
        clean_query = re.sub(r"[^\w\s]", "", query.lower())
        clean_query = re.sub(r"\s+", " ", clean_query).strip()

        return clean_query

    async def _get_default_suggestions(
        self, user_id: int, context: SearchContext, limit: int
    ) -> list[SearchSuggestion]:
        """Get default suggestions when query is too short"""
        suggestions = []

        try:
            behavior = await self._get_user_behavior(user_id)

            # Add favorite artists
            for artist in behavior.favorite_artists[: limit // 3]:
                suggestion = SearchSuggestion(
                    id=f"artist_{artist}",
                    type=SuggestionType.ARTIST,
                    title=artist,
                    subtitle="Favorite Artist",
                    image_url=None,
                    url=f"/search?q={artist}",
                    metadata={"source": "favorites"},
                    relevance_score=0.8,
                    context=context,
                    created_at=datetime.datetime.utcnow(),
                )
                suggestions.append(suggestion)

            # Add trending content
            if context == SearchContext.DISCOVERY:
                trending = await self._get_trending_suggestions(user_id, limit // 3)
                suggestions.extend(trending)

            # Add popular genres
            for genre in behavior.favorite_genres[: limit // 3]:
                suggestion = SearchSuggestion(
                    id=f"genre_{genre}",
                    type=SuggestionType.GENRE,
                    title=genre.title(),
                    subtitle="Popular Genre",
                    image_url=None,
                    url=f"/search?genre={genre}",
                    metadata={"source": "favorites"},
                    relevance_score=0.7,
                    context=context,
                    created_at=datetime.datetime.utcnow(),
                )
                suggestions.append(suggestion)

        except Exception as e:
            logger.error(f"Error getting default suggestions: {e}")

        return suggestions[:limit]

    async def _get_local_track_suggestions(
        self, user_id: int, query: str, limit: int
    ) -> list[SearchSuggestion]:
        """Get track suggestions from local library"""
        suggestions = []

        try:
            with Session(db.engine) as session:
                # Search tracks in local library
                search_pattern = f"%{query}%"
                tracks_query = (
                    select(Track)
                    .where(
                        or_(
                            Track.title.ilike(search_pattern),
                            Track.artist.ilike(search_pattern),
                            Track.album.ilike(search_pattern),
                        )
                    )
                    .limit(limit)
                )

                tracks = session.execute(tracks_query).scalars().all()

                for track in tracks:
                    suggestion = SearchSuggestion(
                        id=f"track_{track.id}",
                        type=SuggestionType.TRACK,
                        title=track.title,
                        subtitle=f"{track.artist} • {track.album}",
                        image_url=track.image,
                        url=f"/track/{track.id}",
                        metadata={
                            "track_id": track.id,
                            "artist": track.artist,
                            "album": track.album,
                            "duration": track.duration,
                            "play_count": track.playcount,
                        },
                        relevance_score=self._calculate_local_relevance(track, query),
                        context=SearchContext.GENERAL,
                        created_at=datetime.datetime.utcnow(),
                    )
                    suggestions.append(suggestion)

        except Exception as e:
            logger.error(f"Error getting local track suggestions: {e}")

        return suggestions

    async def _get_artist_suggestions(
        self, user_id: int, query: str, limit: int
    ) -> list[SearchSuggestion]:
        """Get artist suggestions"""
        suggestions = []

        try:
            with Session(db.engine) as session:
                # Search artists
                search_pattern = f"%{query}%"
                artists_query = (
                    select(Artist).where(Artist.name.ilike(search_pattern)).limit(limit)
                )

                artists = session.execute(artists_query).scalars().all()

                for artist in artists:
                    suggestion = SearchSuggestion(
                        id=f"artist_{artist.id}",
                        type=SuggestionType.ARTIST,
                        title=artist.name,
                        subtitle=f"{artist.trackcount} tracks",
                        image_url=artist.image,
                        url=f"/artist/{artist.id}",
                        metadata={
                            "artist_id": artist.id,
                            "track_count": artist.trackcount,
                            "album_count": artist.albumcount,
                        },
                        relevance_score=self._calculate_artist_relevance(artist, query),
                        context=SearchContext.GENERAL,
                        created_at=datetime.datetime.utcnow(),
                    )
                    suggestions.append(suggestion)

        except Exception as e:
            logger.error(f"Error getting artist suggestions: {e}")

        return suggestions

    async def _get_album_suggestions(
        self, user_id: int, query: str, limit: int
    ) -> list[SearchSuggestion]:
        """Get album suggestions"""
        suggestions = []

        try:
            # This would search albums in local library
            # For now, return empty list as placeholder
            pass

        except Exception as e:
            logger.error(f"Error getting album suggestions: {e}")

        return suggestions

    async def _get_global_suggestions(
        self, user_id: int, query: str, limit: int
    ) -> list[SearchSuggestion]:
        """Get suggestions from global catalog"""
        suggestions = []

        try:
            # Search global catalog
            catalog_results = await music_catalog_service.search_global_catalog(
                query, "all", limit
            )

            # Convert tracks to suggestions
            for track in catalog_results.tracks[: limit // 2]:
                suggestion = SearchSuggestion(
                    id=f"global_track_{track.spotify_id}",
                    type=SuggestionType.TRACK,
                    title=track.title,
                    subtitle=f"{track.artist} • {track.album}",
                    image_url=track.image_url,
                    url=f"/catalog/track/{track.spotify_id}",
                    metadata={
                        "spotify_id": track.spotify_id,
                        "source": "global_catalog",
                        "popularity": track.popularity,
                    },
                    relevance_score=self._calculate_global_relevance(
                        track, query, user_id
                    ),
                    context=SearchContext.DISCOVERY,
                    created_at=datetime.datetime.utcnow(),
                )
                suggestions.append(suggestion)

            # Convert artists to suggestions
            for artist in catalog_results.artists[: limit // 2]:
                suggestion = SearchSuggestion(
                    id=f"global_artist_{artist.spotify_id}",
                    type=SuggestionType.ARTIST,
                    title=artist.title,
                    subtitle="Discover on Spotify",
                    image_url=artist.image_url,
                    url=f"/catalog/artist/{artist.spotify_id}",
                    metadata={
                        "spotify_id": artist.spotify_id,
                        "source": "global_catalog",
                        "popularity": artist.popularity,
                    },
                    relevance_score=self._calculate_global_relevance(
                        artist, query, user_id
                    ),
                    context=SearchContext.DISCOVERY,
                    created_at=datetime.datetime.utcnow(),
                )
                suggestions.append(suggestion)

        except Exception as e:
            logger.error(f"Error getting global suggestions: {e}")

        return suggestions

    async def _get_behavior_suggestions(
        self, user_id: int, query: str, context: SearchContext, limit: int
    ) -> list[SearchSuggestion]:
        """Get behavior-based suggestions"""
        suggestions = []

        try:
            behavior = await self._get_user_behavior(user_id)

            # Suggest based on favorite genres
            for genre in behavior.favorite_genres[: limit // 3]:
                if query.lower() in genre.lower():
                    suggestion = SearchSuggestion(
                        id=f"genre_suggestion_{genre}",
                        type=SuggestionType.GENRE,
                        title=genre.title(),
                        subtitle="Based on your preferences",
                        image_url=None,
                        url=f"/search?genre={genre}",
                        metadata={"source": "behavior"},
                        relevance_score=0.9,
                        context=context,
                        created_at=datetime.datetime.utcnow(),
                    )
                    suggestions.append(suggestion)

            # Suggest based on listening patterns
            if "search" in behavior.listening_patterns:
                recent_searches = behavior.listening_patterns["search"][-5:]
                for recent_search in recent_searches:
                    if query.lower() in recent_search.get("query", "").lower():
                        suggestion = SearchSuggestion(
                            id=f"recent_search_{recent_search.get('query', '')}",
                            type=SuggestionType.SEARCH_QUERY,
                            title=recent_search.get("query", ""),
                            subtitle="Recent search",
                            image_url=None,
                            url=f"/search?q={recent_search.get('query', '')}",
                            metadata={"source": "recent_searches"},
                            relevance_score=0.8,
                            context=context,
                            created_at=datetime.datetime.utcnow(),
                        )
                        suggestions.append(suggestion)

        except Exception as e:
            logger.error(f"Error getting behavior suggestions: {e}")

        return suggestions

    def _calculate_local_relevance(self, track: Track, query: str) -> float:
        """Calculate relevance score for local track"""
        score = 0.0

        query_lower = query.lower()

        # Title match
        if track.title and query_lower in track.title.lower():
            score += 0.8

        # Artist match
        if track.artist and query_lower in track.artist.lower():
            score += 0.6

        # Album match
        if track.album and query_lower in track.album.lower():
            score += 0.4

        # Play count boost
        if track.playcount > 0:
            score += min(track.playcount / 100, 0.3)

        return min(score, 1.0)

    def _calculate_artist_relevance(self, artist: Artist, query: str) -> float:
        """Calculate relevance score for artist"""
        score = 0.0

        query_lower = query.lower()

        # Name match
        if artist.name and query_lower in artist.name.lower():
            score += 0.9

        # Track count boost
        if artist.trackcount > 0:
            score += min(artist.trackcount / 50, 0.3)

        return min(score, 1.0)

    def _calculate_global_relevance(self, item: Any, query: str, user_id: int) -> float:
        """Calculate relevance score for global catalog item"""
        score = 0.0

        query_lower = query.lower()

        # Title/name match
        if hasattr(item, "title") and item.title and query_lower in item.title.lower():
            score += 0.7

        # Artist match
        if (
            hasattr(item, "artist")
            and item.artist
            and query_lower in item.artist.lower()
        ):
            score += 0.5

        # Popularity boost
        if hasattr(item, "popularity") and item.popularity:
            score += min(item.popularity / 100, 0.3)

        return min(score, 1.0)

    def _calculate_download_relevance(self, item: Any, user_id: int) -> float:
        """Calculate relevance score for download suggestions"""
        score = 0.0

        # Base relevance from popularity
        if hasattr(item, "popularity") and item.popularity:
            score += min(item.popularity / 100, 0.5)

        # User behavior boost
        if user_id in self.user_behaviors:
            behavior = self.user_behaviors[user_id]

            # Favorite artist boost
            if hasattr(item, "artist") and item.artist in behavior.favorite_artists:
                score += 0.3

            # Favorite genre boost
            # This would require genre information from the item

        return min(score, 1.0)

    async def _get_user_behavior(self, user_id: int) -> UserBehavior:
        """Get or create user behavior data"""
        if user_id in self.user_behaviors:
            return self.user_behaviors[user_id]

        # Create default behavior
        behavior = UserBehavior(
            user_id=user_id,
            favorite_genres=[],
            favorite_artists=[],
            listening_patterns={},
            search_history=[],
            download_preferences={},
            interaction_patterns={},
            last_updated=datetime.datetime.utcnow(),
        )

        self.user_behaviors[user_id] = behavior
        return behavior

    async def _log_search_query(
        self,
        user_id: int,
        query: str,
        context: SearchContext,
        suggestions: list[SearchSuggestion],
    ):
        """Log search query for learning"""
        try:
            search_data = {
                "query": query,
                "context": context.value,
                "suggestion_count": len(suggestions),
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "suggestions": [
                    {
                        "id": s.id,
                        "type": s.type.value,
                        "title": s.title,
                        "relevance_score": s.relevance_score,
                    }
                    for s in suggestions[:5]  # Log top 5 suggestions
                ],
            }

            # Add to search history
            behavior = await self._get_user_behavior(user_id)
            behavior.search_history.append(search_data)

            # Limit history size
            if len(behavior.search_history) > self.search_history_limit:
                behavior.search_history = behavior.search_history[
                    -self.search_history_limit :
                ]

        except Exception as e:
            logger.error(f"Error logging search query: {e}")

    async def _get_track_info(self, track_id: str) -> dict[str, Any] | None:
        """Get track information"""
        try:
            with Session(db.engine) as session:
                track = session.get(Track, track_id)
                if not track:
                    return None

                return {
                    "id": track.id,
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "duration": track.duration,
                    "playcount": track.playcount,
                    "image": track.image,
                }
        except Exception as e:
            logger.error(f"Error getting track info: {e}")
            return None

    async def _get_popular_genres(self, user_id: int) -> list[str]:
        """Get popular genres for user"""
        try:
            with Session(db.engine):
                # This would query genre data from tracks
                # For now, return common genres
                return [
                    "rock",
                    "pop",
                    "electronic",
                    "jazz",
                    "classical",
                    "hip-hop",
                    "country",
                    "blues",
                ]
        except Exception as e:
            logger.error(f"Error getting popular genres: {e}")
            return []

    async def _get_genre_track_count(self, genre: str) -> int:
        """Get track count for genre"""
        try:
            with Session(db.engine):
                # This would count tracks by genre
                # For now, return placeholder
                return 100
        except Exception as e:
            logger.error(f"Error getting genre track count: {e}")
            return 0

    async def _get_year_track_count(self, year: int) -> int:
        """Get track count for year"""
        try:
            with Session(db.engine):
                # This would count tracks by year
                # For now, return placeholder
                return 50
        except Exception as e:
            logger.error(f"Error getting year track count: {e}")
            return 0

    async def _get_trending_suggestions(
        self, user_id: int, limit: int
    ) -> list[SearchSuggestion]:
        """Get trending suggestions"""
        # This would implement trending logic
        return []

    async def _get_trending_download_suggestions(
        self, user_id: int, limit: int
    ) -> list[SearchSuggestion]:
        """Get trending download suggestions"""
        # This would implement trending download logic
        return []

    async def _get_track_recommendations(
        self, user_id: int, behavior: UserBehavior, limit: int
    ) -> list[SearchSuggestion]:
        """Get track recommendations based on behavior"""
        # This would use AI recommender
        return []

    async def _get_artist_recommendations(
        self, user_id: int, behavior: UserBehavior, limit: int
    ) -> list[SearchSuggestion]:
        """Get artist recommendations based on behavior"""
        # This would use AI recommender
        return []

    async def _get_album_recommendations(
        self, user_id: int, behavior: UserBehavior, limit: int
    ) -> list[SearchSuggestion]:
        """Get album recommendations based on behavior"""
        # This would use AI recommender
        return []

    async def _get_similar_track_suggestions(
        self, user_id: int, current_track: dict[str, Any], limit: int
    ) -> list[SearchSuggestion]:
        """Get similar track suggestions"""
        # This would implement similarity logic
        return []

    async def _get_same_artist_suggestions(
        self, user_id: int, artist_name: str, limit: int
    ) -> list[SearchSuggestion]:
        """Get suggestions from same artist"""
        # This would query tracks by artist
        return []

    async def _get_same_genre_suggestions(
        self, user_id: int, current_track: dict[str, Any], limit: int
    ) -> list[SearchSuggestion]:
        """Get suggestions from same genre"""
        # This would query tracks by genre
        return []

    async def _get_popular_suggestions(
        self, user_id: int, limit: int
    ) -> list[SearchSuggestion]:
        """Get popular suggestions"""
        # This would implement popularity logic
        return []

    async def _update_search_behavior(
        self, behavior: UserBehavior, interaction_data: dict[str, Any]
    ):
        """Update search behavior"""
        # This would update search patterns
        pass

    async def _update_play_behavior(
        self, behavior: UserBehavior, interaction_data: dict[str, Any]
    ):
        """Update play behavior"""
        # This would update listening patterns
        pass

    async def _update_download_behavior(
        self, behavior: UserBehavior, interaction_data: dict[str, Any]
    ):
        """Update download behavior"""
        # This would update download preferences
        pass

    async def _update_like_behavior(
        self, behavior: UserBehavior, interaction_data: dict[str, Any]
    ):
        """Update like behavior"""
        # This would update favorites
        pass

    async def _save_user_behavior(self, behavior: UserBehavior):
        """Save user behavior to database"""
        # This would save to database
        pass


# Global service instance
advanced_ux_service = AdvancedUXService()
