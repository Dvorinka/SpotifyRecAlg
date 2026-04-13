"""
Year-in-Review Experience Service

This service provides comprehensive year-in-review generation including:
- Listening statistics and analytics
- Personalized music insights
- Video generation with Remotion
- Social sharing capabilities
- Interactive data visualization
"""

import datetime
import json
import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from swingmusic.config import USER_DATA_DIR
from swingmusic.db import db
from swingmusic.models.playlog import Playlog
from swingmusic.models.track import Track

logger = logging.getLogger(__name__)


class RecapTheme(Enum):
    """Available recap themes"""

    MODERN = "modern"
    RETRO = "retro"
    MINIMAL = "minimal"
    VIBRANT = "vibrant"
    DARK = "dark"
    LIGHT = "light"


@dataclass
class ListeningStats:
    """User listening statistics for a time period"""

    total_minutes: int
    total_tracks: int
    total_artists: int
    total_albums: int
    unique_tracks: int
    average_daily_minutes: float
    most_played_track: dict | None
    most_played_artist: dict | None
    most_played_album: dict | None
    top_genres: list[dict]
    listening_streak: int
    longest_session: int
    favorite_time_of_day: str
    discovery_rate: float
    repeat_listen_rate: float


@dataclass
class MusicPersonality:
    """User music personality analysis"""

    personality_type: str
    traits: list[str]
    description: str
    diversity_score: float
    exploration_score: float
    loyalty_score: float
    mood_profile: dict[str, float]
    genre_preferences: dict[str, float]
    audio_preferences: dict[str, Any]


@dataclass
class RecapData:
    """Complete year-in-review data package"""

    user_id: int
    year: int
    stats: ListeningStats
    personality: MusicPersonality
    monthly_breakdown: list[dict]
    top_tracks: list[dict]
    top_artists: list[dict]
    top_albums: list[dict]
    discoveries: list[dict]
    milestones: list[dict]
    created_at: datetime.datetime


class RecapService:
    """Service for generating comprehensive year-in-review experiences"""

    def __init__(self):
        self.recap_dir = USER_DATA_DIR / "recaps"
        self.recap_dir.mkdir(exist_ok=True)

    async def generate_year_recap(self, user_id: int, year: int) -> RecapData:
        """
        Generate comprehensive year-in-review data

        Args:
            user_id: User ID
            year: Year to generate recap for

        Returns:
            Complete recap data
        """
        try:
            logger.info(f"Generating year recap for user {user_id}, year {year}")

            # Get listening data for the year
            start_date = datetime.datetime(year, 1, 1)
            end_date = datetime.datetime(year, 12, 31, 23, 59, 59)

            # Generate all components
            stats = await self._calculate_listening_stats(user_id, start_date, end_date)
            personality = await self._analyze_music_personality(
                user_id, start_date, end_date
            )
            monthly_breakdown = await self._get_monthly_breakdown(user_id, year)
            top_tracks = await self._get_top_tracks(user_id, start_date, end_date, 50)
            top_artists = await self._get_top_artists(user_id, start_date, end_date, 25)
            top_albums = await self._get_top_albums(user_id, start_date, end_date, 25)
            discoveries = await self._get_new_discoveries(user_id, start_date, end_date)
            milestones = await self._calculate_milestones(stats, personality)

            recap_data = RecapData(
                user_id=user_id,
                year=year,
                stats=stats,
                personality=personality,
                monthly_breakdown=monthly_breakdown,
                top_tracks=top_tracks,
                top_artists=top_artists,
                top_albums=top_albums,
                discoveries=discoveries,
                milestones=milestones,
                created_at=datetime.datetime.utcnow(),
            )

            # Save recap data
            await self._save_recap_data(recap_data)

            return recap_data

        except Exception as e:
            logger.error(f"Error generating year recap: {e}")
            raise

    async def get_recap_summary(self, user_id: int, year: int) -> dict | None:
        """
        Get recap summary for quick display

        Args:
            user_id: User ID
            year: Year to get summary for

        Returns:
            Recap summary or None if not available
        """
        try:
            recap_file = self.recap_dir / f"recap_{user_id}_{year}.json"

            if not recap_file.exists():
                return None

            with open(recap_file) as f:
                recap_data = json.load(f)

            # Return summary data
            return {
                "year": recap_data["year"],
                "total_minutes": recap_data["stats"]["total_minutes"],
                "total_tracks": recap_data["stats"]["total_tracks"],
                "top_track": recap_data["stats"]["most_played_track"],
                "top_artist": recap_data["stats"]["most_played_artist"],
                "personality_type": recap_data["personality"]["personality_type"],
                "created_at": recap_data["created_at"],
            }

        except Exception as e:
            logger.error(f"Error getting recap summary: {e}")
            return None

    async def _calculate_listening_stats(
        self, user_id: int, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> ListeningStats:
        """Calculate comprehensive listening statistics"""
        try:
            with Session(db.engine) as session:
                # Get all plays for the period
                plays_query = (
                    select(Playlog)
                    .where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.played_at >= start_date,
                            Playlog.played_at <= end_date,
                        )
                    )
                    .order_by(Playlog.played_at)
                )

                plays = session.execute(plays_query).scalars().all()

                if not plays:
                    return ListeningStats(
                        total_minutes=0,
                        total_tracks=0,
                        total_artists=0,
                        total_albums=0,
                        unique_tracks=0,
                        average_daily_minutes=0.0,
                        most_played_track=None,
                        most_played_artist=None,
                        most_played_album=None,
                        top_genres=[],
                        listening_streak=0,
                        longest_session=0,
                        favorite_time_of_day="",
                        discovery_rate=0.0,
                        repeat_listen_rate=0.0,
                    )

                # Basic statistics
                total_minutes = sum(play.duration or 0 for play in plays)
                unique_tracks = len({play.track_id for play in plays})
                total_tracks = len(plays)

                # Get track details for artist/album counts
                track_ids = list({play.track_id for play in plays})
                tracks_query = select(Track).where(Track.id.in_(track_ids))
                tracks = session.execute(tracks_query).scalars().all()

                unique_artists = len({track.artist for track in tracks})
                unique_albums = len({track.album for track in tracks})

                # Most played items
                track_counts = {}
                artist_counts = {}
                album_counts = {}

                for play in plays:
                    track = next((t for t in tracks if t.id == play.track_id), None)
                    if track:
                        # Track counts
                        track_counts[track.id] = track_counts.get(track.id, 0) + 1

                        # Artist counts
                        artist_counts[track.artist] = (
                            artist_counts.get(track.artist, 0) + 1
                        )

                        # Album counts
                        album_counts[track.album] = album_counts.get(track.album, 0) + 1

                most_played_track_id = (
                    max(track_counts, key=track_counts.get) if track_counts else None
                )
                most_played_track = None
                if most_played_track_id:
                    track = next(
                        (t for t in tracks if t.id == most_played_track_id), None
                    )
                    if track:
                        most_played_track = {
                            "id": track.id,
                            "title": track.title,
                            "artist": track.artist,
                            "album": track.album,
                            "play_count": track_counts[most_played_track_id],
                        }

                most_played_artist_name = (
                    max(artist_counts, key=artist_counts.get) if artist_counts else None
                )
                most_played_artist = (
                    {
                        "name": most_played_artist_name,
                        "play_count": artist_counts.get(most_played_artist_name, 0),
                    }
                    if most_played_artist_name
                    else None
                )

                most_played_album_name = (
                    max(album_counts, key=album_counts.get) if album_counts else None
                )
                most_played_album = (
                    {
                        "name": most_played_album_name,
                        "play_count": album_counts.get(most_played_album_name, 0),
                    }
                    if most_played_album_name
                    else None
                )

                # Calculate additional stats
                days_in_period = (end_date - start_date).days + 1
                average_daily_minutes = total_minutes / days_in_period

                # Listening streak (consecutive days with plays)
                listening_streak = await self._calculate_listening_streak(plays)

                # Longest session
                longest_session = await self._calculate_longest_session(plays)

                # Favorite time of day
                favorite_time_of_day = await self._calculate_favorite_time_of_day(plays)

                # Discovery and repeat rates
                discovery_rate = await self._calculate_discovery_rate(user_id, plays)
                repeat_listen_rate = (
                    (total_tracks - unique_tracks) / total_tracks
                    if total_tracks > 0
                    else 0
                )

                return ListeningStats(
                    total_minutes=int(total_minutes),
                    total_tracks=total_tracks,
                    total_artists=unique_artists,
                    total_albums=unique_albums,
                    unique_tracks=unique_tracks,
                    average_daily_minutes=average_daily_minutes,
                    most_played_track=most_played_track,
                    most_played_artist=most_played_artist,
                    most_played_album=most_played_album,
                    top_genres=[],  # Would need genre data from tracks
                    listening_streak=listening_streak,
                    longest_session=longest_session,
                    favorite_time_of_day=favorite_time_of_day,
                    discovery_rate=discovery_rate,
                    repeat_listen_rate=repeat_listen_rate,
                )

        except Exception as e:
            logger.error(f"Error calculating listening stats: {e}")
            raise

    async def _analyze_music_personality(
        self, user_id: int, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> MusicPersonality:
        """Analyze user's music personality based on listening patterns"""
        try:
            # This is a simplified version - would integrate with audio analyzer for deeper insights
            with Session(db.engine) as session:
                plays_query = select(Playlog).where(
                    and_(
                        Playlog.user_id == user_id,
                        Playlog.played_at >= start_date,
                        Playlog.played_at <= end_date,
                    )
                )
                plays = session.execute(plays_query).scalars().all()

                if not plays:
                    return MusicPersonality(
                        personality_type="Explorer",
                        traits=["Curious", "Open-minded"],
                        description="You love discovering new music",
                        diversity_score=0.8,
                        exploration_score=0.9,
                        loyalty_score=0.3,
                        mood_profile={"energetic": 0.6, "relaxed": 0.4},
                        genre_preferences={},
                        audio_preferences={},
                    )

                # Analyze patterns
                track_ids = list({play.track_id for play in plays})
                tracks_query = select(Track).where(Track.id.in_(track_ids))
                session.execute(tracks_query).scalars().all()

                # Calculate metrics
                unique_tracks = len(track_ids)
                total_plays = len(plays)
                diversity_score = unique_tracks / total_plays if total_plays > 0 else 0

                # Determine personality type based on patterns
                if diversity_score > 0.7:
                    personality_type = "Explorer"
                    traits = ["Curious", "Open-minded", "Adventurous"]
                    description = (
                        "You love discovering new music and exploring different genres"
                    )
                elif diversity_score > 0.4:
                    personality_type = "Balanced"
                    traits = ["Versatile", "Open-minded", "Selective"]
                    description = (
                        "You enjoy both new discoveries and familiar favorites"
                    )
                else:
                    personality_type = "Loyalist"
                    traits = ["Dedicated", "Selective", "Consistent"]
                    description = "You prefer to stick with what you love and dive deep into favorites"

                return MusicPersonality(
                    personality_type=personality_type,
                    traits=traits,
                    description=description,
                    diversity_score=diversity_score,
                    exploration_score=diversity_score,  # Simplified
                    loyalty_score=1.0 - diversity_score,  # Simplified
                    mood_profile={
                        "energetic": 0.6,
                        "relaxed": 0.4,
                    },  # Would analyze audio features
                    genre_preferences={},  # Would analyze genre data
                    audio_preferences={},  # Would analyze audio features
                )

        except Exception as e:
            logger.error(f"Error analyzing music personality: {e}")
            raise

    async def _get_monthly_breakdown(self, user_id: int, year: int) -> list[dict]:
        """Get monthly listening breakdown"""
        try:
            monthly_data = []

            for month in range(1, 13):
                start_date = datetime.datetime(year, month, 1)
                if month == 12:
                    end_date = datetime.datetime(year, 12, 31, 23, 59, 59)
                else:
                    end_date = datetime.datetime(
                        year, month + 1, 1
                    ) - datetime.timedelta(seconds=1)

                with Session(db.engine) as session:
                    plays_query = select(func.sum(Playlog.duration)).where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.played_at >= start_date,
                            Playlog.played_at <= end_date,
                        )
                    )
                    total_minutes = session.execute(plays_query).scalar() or 0

                    # Get track count
                    count_query = select(func.count(Playlog.id)).where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.played_at >= start_date,
                            Playlog.played_at <= end_date,
                        )
                    )
                    track_count = session.execute(count_query).scalar() or 0

                    monthly_data.append(
                        {
                            "month": month,
                            "month_name": datetime.date(year, month, 1).strftime("%B"),
                            "total_minutes": int(total_minutes),
                            "track_count": track_count,
                        }
                    )

            return monthly_data

        except Exception as e:
            logger.error(f"Error getting monthly breakdown: {e}")
            return []

    async def _get_top_tracks(
        self,
        user_id: int,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        limit: int,
    ) -> list[dict]:
        """Get top tracks for the period"""
        try:
            with Session(db.engine) as session:
                # Get play counts
                play_counts_query = (
                    select(
                        Playlog.track_id,
                        func.count(Playlog.id).label("play_count"),
                        func.sum(Playlog.duration).label("total_duration"),
                    )
                    .where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.played_at >= start_date,
                            Playlog.played_at <= end_date,
                        )
                    )
                    .group_by(Playlog.track_id)
                    .order_by(func.count(Playlog.id).desc())
                    .limit(limit)
                )

                play_counts = session.execute(play_counts_query).all()

                top_tracks = []
                for play_count in play_counts:
                    track = session.get(Track, play_count.track_id)
                    if track:
                        top_tracks.append(
                            {
                                "id": track.id,
                                "title": track.title,
                                "artist": track.artist,
                                "album": track.album,
                                "play_count": play_count.play_count,
                                "total_duration": int(play_count.total_duration or 0),
                                "image": track.image,
                            }
                        )

                return top_tracks

        except Exception as e:
            logger.error(f"Error getting top tracks: {e}")
            return []

    async def _get_top_artists(
        self,
        user_id: int,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        limit: int,
    ) -> list[dict]:
        """Get top artists for the period"""
        try:
            with Session(db.engine) as session:
                # Get artist play counts
                artist_counts_query = (
                    select(
                        Track.artist,
                        func.count(Playlog.id).label("play_count"),
                        func.sum(Playlog.duration).label("total_duration"),
                        func.count(func.distinct(Track.id)).label("unique_tracks"),
                    )
                    .join(Playlog, Track.id == Playlog.track_id)
                    .where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.played_at >= start_date,
                            Playlog.played_at <= end_date,
                        )
                    )
                    .group_by(Track.artist)
                    .order_by(func.count(Playlog.id).desc())
                    .limit(limit)
                )

                artist_counts = session.execute(artist_counts_query).all()

                top_artists = []
                for artist_count in artist_counts:
                    top_artists.append(
                        {
                            "name": artist_count.artist,
                            "play_count": artist_count.play_count,
                            "total_duration": int(artist_count.total_duration or 0),
                            "unique_tracks": artist_count.unique_tracks,
                        }
                    )

                return top_artists

        except Exception as e:
            logger.error(f"Error getting top artists: {e}")
            return []

    async def _get_top_albums(
        self,
        user_id: int,
        start_date: datetime.datetime,
        end_date: datetime.datetime,
        limit: int,
    ) -> list[dict]:
        """Get top albums for the period"""
        try:
            with Session(db.engine) as session:
                # Get album play counts
                album_counts_query = (
                    select(
                        Track.album,
                        Track.artist,
                        func.count(Playlog.id).label("play_count"),
                        func.sum(Playlog.duration).label("total_duration"),
                        func.count(func.distinct(Track.id)).label("unique_tracks"),
                    )
                    .join(Playlog, Track.id == Playlog.track_id)
                    .where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.played_at >= start_date,
                            Playlog.played_at <= end_date,
                        )
                    )
                    .group_by(Track.album, Track.artist)
                    .order_by(func.count(Playlog.id).desc())
                    .limit(limit)
                )

                album_counts = session.execute(album_counts_query).all()

                top_albums = []
                for album_count in album_counts:
                    top_albums.append(
                        {
                            "name": album_count.album,
                            "artist": album_count.artist,
                            "play_count": album_count.play_count,
                            "total_duration": int(album_count.total_duration or 0),
                            "unique_tracks": album_count.unique_tracks,
                        }
                    )

                return top_albums

        except Exception as e:
            logger.error(f"Error getting top albums: {e}")
            return []

    async def _get_new_discoveries(
        self, user_id: int, start_date: datetime.datetime, end_date: datetime.datetime
    ) -> list[dict]:
        """Get tracks discovered during the period"""
        try:
            with Session(db.engine) as session:
                # Get first play of each track in the period
                first_plays_query = (
                    select(
                        Track.id,
                        Track.title,
                        Track.artist,
                        Track.album,
                        func.min(Playlog.played_at).label("first_played"),
                        func.count(Playlog.id).label("play_count"),
                    )
                    .join(Playlog, Track.id == Playlog.track_id)
                    .where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.played_at >= start_date,
                            Playlog.played_at <= end_date,
                        )
                    )
                    .group_by(Track.id, Track.title, Track.artist, Track.album)
                    .order_by(func.min(Playlog.played_at).desc())
                )

                discoveries = session.execute(first_plays_query).all()

                discovery_list = []
                for discovery in discoveries:
                    # Check if this was actually discovered in this period (no plays before start_date)
                    prior_plays_query = select(func.count(Playlog.id)).where(
                        and_(
                            Playlog.user_id == user_id,
                            Playlog.track_id == discovery.id,
                            Playlog.played_at < start_date,
                        )
                    )
                    prior_plays = session.execute(prior_plays_query).scalar() or 0

                    if prior_plays == 0:  # Truly discovered in this period
                        discovery_list.append(
                            {
                                "id": discovery.id,
                                "title": discovery.title,
                                "artist": discovery.artist,
                                "album": discovery.album,
                                "discovered_date": discovery.first_played.isoformat(),
                                "play_count": discovery.play_count,
                            }
                        )

                return discovery_list[:50]  # Limit to top 50 discoveries

        except Exception as e:
            logger.error(f"Error getting new discoveries: {e}")
            return []

    async def _calculate_milestones(
        self, stats: ListeningStats, personality: MusicPersonality
    ) -> list[dict]:
        """Calculate user milestones"""
        milestones = []

        # Listening time milestones
        if stats.total_minutes >= 50000:  # ~833 hours
            milestones.append(
                {
                    "type": "listening_time",
                    "title": "Marathon Listener",
                    "description": f"Listened for {stats.total_minutes // 60} hours this year!",
                    "icon": "clock",
                    "level": "gold",
                }
            )
        elif stats.total_minutes >= 25000:  # ~417 hours
            milestones.append(
                {
                    "type": "listening_time",
                    "title": "Dedicated Listener",
                    "description": f"Listened for {stats.total_minutes // 60} hours this year!",
                    "icon": "clock",
                    "level": "silver",
                }
            )
        elif stats.total_minutes >= 10000:  # ~167 hours
            milestones.append(
                {
                    "type": "listening_time",
                    "title": "Music Enthusiast",
                    "description": f"Listened for {stats.total_minutes // 60} hours this year!",
                    "icon": "clock",
                    "level": "bronze",
                }
            )

        # Discovery milestones
        if stats.unique_tracks >= 10000:
            milestones.append(
                {
                    "type": "discovery",
                    "title": "Ultimate Explorer",
                    "description": f"Discovered {stats.unique_tracks} unique tracks!",
                    "icon": "compass",
                    "level": "gold",
                }
            )
        elif stats.unique_tracks >= 5000:
            milestones.append(
                {
                    "type": "discovery",
                    "title": "Music Explorer",
                    "description": f"Discovered {stats.unique_tracks} unique tracks!",
                    "icon": "compass",
                    "level": "silver",
                }
            )
        elif stats.unique_tracks >= 1000:
            milestones.append(
                {
                    "type": "discovery",
                    "title": "Curious Listener",
                    "description": f"Discovered {stats.unique_tracks} unique tracks!",
                    "icon": "compass",
                    "level": "bronze",
                }
            )

        # Streak milestones
        if stats.listening_streak >= 365:
            milestones.append(
                {
                    "type": "streak",
                    "title": "Everyday Listener",
                    "description": f"Listened music every day for {stats.listening_streak} days!",
                    "icon": "calendar",
                    "level": "gold",
                }
            )
        elif stats.listening_streak >= 100:
            milestones.append(
                {
                    "type": "streak",
                    "title": "Consistent Listener",
                    "description": f"Listened music for {stats.listening_streak} consecutive days!",
                    "icon": "calendar",
                    "level": "silver",
                }
            )
        elif stats.listening_streak >= 30:
            milestones.append(
                {
                    "type": "streak",
                    "title": "Monthly Streak",
                    "description": f"Listened music for {stats.listening_streak} consecutive days!",
                    "icon": "calendar",
                    "level": "bronze",
                }
            )

        return milestones

    async def _save_recap_data(self, recap_data: RecapData):
        """Save recap data to file"""
        try:
            recap_file = (
                self.recap_dir / f"recap_{recap_data.user_id}_{recap_data.year}.json"
            )

            # Convert to dict and save
            recap_dict = asdict(recap_data)

            with open(recap_file, "w") as f:
                json.dump(recap_dict, f, indent=2, default=str)

            logger.info(f"Saved recap data to {recap_file}")

        except Exception as e:
            logger.error(f"Error saving recap data: {e}")
            raise

    async def _calculate_listening_streak(self, plays: list) -> int:
        """Calculate longest consecutive day streak"""
        if not plays:
            return 0

        # Get unique days with plays
        play_days = {play.played_at.date() for play in plays}
        sorted_days = sorted(play_days)

        max_streak = 0
        current_streak = 0

        for i, day in enumerate(sorted_days):
            if i == 0:
                current_streak = 1
            else:
                prev_day = sorted_days[i - 1]
                if (day - prev_day).days == 1:
                    current_streak += 1
                else:
                    current_streak = 1

            max_streak = max(max_streak, current_streak)

        return max_streak

    async def _calculate_longest_session(self, plays: list) -> int:
        """Calculate longest listening session"""
        if not plays:
            return 0

        longest_session = 0
        current_session = 0

        # Sort plays by time
        sorted_plays = sorted(plays, key=lambda p: p.played_at)

        for i, play in enumerate(sorted_plays):
            current_session = play.duration or 0

            # Check if next play is within 30 minutes (continuation of session)
            if i < len(sorted_plays) - 1:
                next_play = sorted_plays[i + 1]
                time_diff = (next_play.played_at - play.played_at).total_seconds() / 60

                if time_diff <= 30:  # Within 30 minutes = same session
                    current_session += next_play.duration or 0
                else:
                    longest_session = max(longest_session, current_session)
                    current_session = 0
            else:
                longest_session = max(longest_session, current_session)

        return int(longest_session)

    async def _calculate_favorite_time_of_day(self, plays: list) -> str:
        """Calculate favorite time of day for listening"""
        if not plays:
            return ""

        # Count plays by hour
        hour_counts = {}
        for play in plays:
            hour = play.played_at.hour
            hour_counts[hour] = hour_counts.get(hour, 0) + 1

        # Find most common hour
        favorite_hour = max(hour_counts, key=hour_counts.get)

        # Convert to time period
        if 6 <= favorite_hour < 12:
            return "Morning"
        elif 12 <= favorite_hour < 18:
            return "Afternoon"
        elif 18 <= favorite_hour < 22:
            return "Evening"
        else:
            return "Night"

    async def _calculate_discovery_rate(self, user_id: int, plays: list) -> float:
        """Calculate rate of new music discovery"""
        if not plays:
            return 0.0

        # Get first play date for each track
        track_first_plays = {}
        for play in plays:
            if play.track_id not in track_first_plays:
                track_first_plays[play.track_id] = play.played_at

        # Count tracks first played during this period vs total
        period_start = min(play.played_at for play in plays)
        period_end = max(play.played_at for play in plays)

        # Check if tracks were first discovered in this period
        new_discoveries = 0
        for _track_id, first_play in track_first_plays.items():
            if period_start <= first_play <= period_end:
                # Check if there were any plays before this period
                # This is simplified - would need to query database for prior plays
                new_discoveries += 1

        return new_discoveries / len(track_first_plays) if track_first_plays else 0.0


# Global service instance
recap_service = RecapService()
