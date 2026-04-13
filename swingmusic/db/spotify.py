"""
Database models for Spotify downloader functionality
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    and_,
    delete,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from swingmusic.db import Base
from swingmusic.db.engine import DbEngine


class SpotifyDownloadTable(Base):
    __tablename__ = "spotify_downloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    spotify_url: Mapped[str] = mapped_column(
        String(500), unique=True, nullable=False, index=True
    )
    spotify_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # track, album, playlist
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist: Mapped[str] = mapped_column(String(500), nullable=False)
    album: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Download settings
    quality: Mapped[str] = mapped_column(String(20), nullable=False, default="flac")
    output_dir: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="tidal")

    # Download status
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, default=None
    )
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Error handling
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)

    # Metadata
    catalog_metadata: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    # Timestamps
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    completed_at: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # User association
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), nullable=True, default=None
    )

    @classmethod
    def create(cls, data: dict):
        """Create a new Spotify download record"""
        if "created_at" not in data:
            data["created_at"] = datetime.now().timestamp()
        if "updated_at" not in data:
            data["updated_at"] = datetime.now().timestamp()

        return cls.insert_one(data)

    @classmethod
    def get_by_id(cls, download_id: int):
        """Get download by ID"""
        result = cls.execute(select(cls).where(cls.id == download_id))
        res = next(result).scalar()
        return res

    @classmethod
    def get_by_spotify_id(cls, spotify_id: str):
        """Get download by Spotify ID"""
        result = cls.execute(select(cls).where(cls.spotify_id == spotify_id))
        res = next(result).scalar()
        return res

    @classmethod
    def get_by_url(cls, spotify_url: str):
        """Get download by Spotify URL"""
        result = cls.execute(select(cls).where(cls.spotify_url == spotify_url))
        res = next(result).scalar()
        return res

    @classmethod
    def get_pending_downloads(cls, limit: int = 50):
        """Get pending downloads"""
        result = cls.execute(
            select(cls)
            .where(cls.status == "pending")
            .order_by(cls.created_at)
            .limit(limit)
        )
        return list(next(result).scalars())

    @classmethod
    def get_active_downloads(cls):
        """Get currently active downloads"""
        result = cls.execute(
            select(cls)
            .where(cls.status.in_(["downloading", "processing"]))
            .order_by(cls.started_at)
        )
        return list(next(result).scalars())

    @classmethod
    def get_download_history(
        cls, user_id: int | None = None, limit: int = 100, offset: int = 0
    ):
        """Get download history with pagination"""
        query = select(cls).where(cls.status.in_(["completed", "failed", "cancelled"]))

        if user_id:
            query = query.where(cls.user_id == user_id)

        query = query.order_by(cls.created_at.desc()).offset(offset).limit(limit)
        result = cls.execute(query)
        return list(next(result).scalars())

    @classmethod
    def update_status(cls, download_id: int, status: str, **kwargs):
        """Update download status and related fields"""
        update_data = {"status": status, "updated_at": datetime.now().timestamp()}
        update_data.update(kwargs)

        return cls.execute(
            update(cls).where(cls.id == download_id).values(update_data), commit=True
        )

    @classmethod
    def update_progress(cls, download_id: int, progress: int):
        """Update download progress"""
        return cls.execute(
            update(cls)
            .where(cls.id == download_id)
            .values({"progress": progress, "updated_at": datetime.now().timestamp()}),
            commit=True,
        )

    @classmethod
    def increment_retry(cls, download_id: int):
        """Increment retry count"""
        return cls.execute(
            update(cls)
            .where(cls.id == download_id)
            .values(
                {
                    "retry_count": cls.retry_count + 1,
                    "updated_at": datetime.now().timestamp(),
                }
            ),
            commit=True,
        )

    @classmethod
    def delete_completed(cls, older_than_days: int = 30):
        """Delete completed downloads older than specified days"""
        cutoff_time = datetime.now().timestamp() - (older_than_days * 24 * 60 * 60)

        return cls.execute(
            delete(cls).where(
                and_(
                    cls.status.in_(["completed", "failed", "cancelled"]),
                    cls.completed_at < cutoff_time,
                )
            ),
            commit=True,
        )

    @classmethod
    def get_statistics(cls):
        """Get download statistics"""
        result = cls.execute(
            select(
                cls.status,
                func.count(cls.id).label("count"),
                func.avg(cls.duration_ms).label("avg_duration"),
            ).group_by(cls.status)
        )

        stats = {}
        for row in next(result):
            stats[row.status] = {
                "count": row.count,
                "avg_duration_ms": row.avg_duration,
            }

        return stats


class SpotifyDownloadSourceTable(Base):
    __tablename__ = "spotify_download_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    @classmethod
    def get_active_sources(cls):
        """Get all active download sources ordered by priority"""
        result = cls.execute(select(cls).where(cls.is_active).order_by(cls.priority))
        return list(next(result).scalars())

    @classmethod
    def get_by_name(cls, name: str):
        """Get source by name"""
        result = cls.execute(select(cls).where(cls.name == name))
        res = next(result).scalar()
        return res

    @classmethod
    def update_source(cls, name: str, **kwargs):
        """Update source configuration"""
        kwargs["updated_at"] = datetime.now().timestamp()
        return cls.execute(
            update(cls).where(cls.name == name).values(kwargs), commit=True
        )


class SpotifyDownloadQueueTable(Base):
    __tablename__ = "spotify_download_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    download_id: Mapped[int] = mapped_column(
        ForeignKey("spotify_downloads.id"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # Relationship to download
    download = relationship("SpotifyDownloadTable", backref="queue_items")

    @classmethod
    def add_to_queue(cls, download_id: int, priority: int = 0):
        """Add download to queue"""
        # Get current max position
        result = cls.execute(select(func.max(cls.position)))
        max_position = next(result).scalar() or 0

        data = {
            "download_id": download_id,
            "priority": priority,
            "position": max_position + 1,
            "added_at": datetime.now().timestamp(),
        }

        return cls.insert_one(data)

    @classmethod
    def get_next_item(cls):
        """Get next item from queue"""
        result = cls.execute(
            select(cls)
            .join(SpotifyDownloadTable)
            .where(
                and_(SpotifyDownloadTable.status == "pending", cls.started_at.is_(None))
            )
            .order_by(cls.priority.desc(), cls.position)
            .limit(1)
        )
        res = next(result).scalar()
        return res

    @classmethod
    def remove_from_queue(cls, download_id: int):
        """Remove item from queue"""
        return cls.execute(
            delete(cls).where(cls.download_id == download_id), commit=True
        )

    @classmethod
    def get_queue_length(cls):
        """Get current queue length"""
        result = cls.execute(
            select(func.count(cls.id))
            .join(SpotifyDownloadTable)
            .where(SpotifyDownloadTable.status == "pending")
        )
        return next(result).scalar() or 0


# Create default download sources
def create_default_sources():
    """Create default download sources if they don't exist"""
    default_sources = [
        {
            "name": "tidal",
            "display_name": "Tidal",
            "priority": 1,
            "is_active": True,
            "config": {
                "quality_preference": ["lossless", "high", "normal"],
                "formats": ["flac", "mp3"],
            },
        },
        {
            "name": "qobuz",
            "display_name": "Qobuz",
            "priority": 2,
            "is_active": True,
            "config": {
                "quality_preference": ["lossless", "high", "normal"],
                "formats": ["flac", "mp3"],
            },
        },
        {
            "name": "amazon",
            "display_name": "Amazon Music",
            "priority": 3,
            "is_active": False,  # Disabled by default
            "config": {
                "quality_preference": ["high", "normal"],
                "formats": ["mp3", "aac"],
            },
        },
    ]

    current_time = datetime.now().timestamp()

    for source_data in default_sources:
        source_data["created_at"] = current_time
        source_data["updated_at"] = current_time

        existing = SpotifyDownloadSourceTable.get_by_name(source_data["name"])
        if not existing:
            SpotifyDownloadSourceTable.insert_one(source_data)


# Add execute method (assuming it exists in the base class)
# This would need to be implemented based on the existing database pattern
for table_class in [
    SpotifyDownloadTable,
    SpotifyDownloadSourceTable,
    SpotifyDownloadQueueTable,
]:
    if not hasattr(table_class, "execute"):

        @classmethod
        def execute_method(cls, query, commit=False):
            engine = DbEngine()
            with engine.session() as session:
                result = session.execute(query)
                if commit:
                    session.commit()
                return result

        table_class.execute = execute_method
        table_class.insert_one = lambda data: table_class.execute(
            insert(table_class).values(data), commit=True
        )


class GlobalCatalogCacheTable(Base):
    __tablename__ = "global_catalog_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    spotify_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # track, album, artist, playlist, search, artist_top_tracks, etc.
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(500), nullable=True)
    album: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    popularity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String(50), nullable=True)
    explicit: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=None
    )  # Full metadata JSON
    cached_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    @classmethod
    def create(cls, data: dict):
        """Create a new catalog cache entry"""
        if "cached_at" not in data:
            data["cached_at"] = datetime.now().timestamp()

        return cls.insert_one(data)

    @classmethod
    def get_by_spotify_id(cls, spotify_id: str, item_type: str = None):
        """Get cached item by Spotify ID and optionally type"""
        query = select(cls).where(cls.spotify_id == spotify_id)

        if item_type:
            query = query.where(cls.item_type == item_type)

        query = query.where(cls.expires_at > datetime.now().timestamp())
        query = query.order_by(cls.cached_at.desc())

        result = cls.execute(query)
        res = next(result).scalar()
        return res

    @classmethod
    def get_expired_entries(cls):
        """Get all expired cache entries"""
        result = cls.execute(
            select(cls).where(cls.expires_at <= datetime.now().timestamp())
        )
        return list(next(result).scalars())

    @classmethod
    def delete_expired(cls):
        """Delete all expired cache entries"""
        return cls.execute(
            delete(cls).where(cls.expires_at <= datetime.now().timestamp()), commit=True
        )

    @classmethod
    def search_cached(cls, query: str, item_types: list = None, limit: int = 20):
        """Search cached items by title or artist"""
        query_filter = select(cls).where(
            and_(
                cls.expires_at > datetime.now().timestamp(),
                or_(cls.title.contains(query), cls.artist.contains(query)),
            )
        )

        if item_types:
            query_filter = query_filter.where(cls.item_type.in_(item_types))

        query_filter = query_filter.order_by(cls.popularity.desc()).limit(limit)

        result = cls.execute(query_filter)
        return list(next(result).scalars())

    @classmethod
    def get_cache_stats(cls):
        """Get cache statistics"""
        result = cls.execute(
            select(
                cls.item_type,
                func.count(cls.id).label("count"),
                func.avg(cls.popularity).label("avg_popularity"),
            )
            .where(cls.expires_at > datetime.now().timestamp())
            .group_by(cls.item_type)
        )

        stats = {}
        for row in next(result):
            stats[row.item_type] = {
                "count": row.count,
                "avg_popularity": row.avg_popularity,
            }

        return stats


class UserCatalogPreferencesTable(Base):
    __tablename__ = "user_catalog_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"), nullable=False, unique=True
    )
    show_explicit: Mapped[bool] = mapped_column(Boolean, default=True)
    default_quality: Mapped[str] = mapped_column(String(20), default="flac")
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False)
    show_suggestions: Mapped[bool] = mapped_column(Boolean, default=True)
    preferred_genres: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    excluded_genres: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    max_search_results: Mapped[int] = mapped_column(Integer, default=20)
    max_top_tracks: Mapped[int] = mapped_column(Integer, default=15)
    max_albums_per_artist: Mapped[int] = mapped_column(Integer, default=20)
    max_trending_results: Mapped[int] = mapped_column(Integer, default=20)
    max_recommendations: Mapped[int] = mapped_column(Integer, default=20)
    preferred_markets: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None
    )
    cache_ttl_preference: Mapped[int] = mapped_column(Integer, default=3600)  # 1 hour
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    @classmethod
    def get_or_create(cls, user_id: int):
        """Get user preferences or create with defaults"""
        result = cls.execute(select(cls).where(cls.user_id == user_id))
        existing = next(result).scalar()

        if existing:
            return existing

        # Create with defaults
        current_time = datetime.now().timestamp()
        default_prefs = {
            "user_id": user_id,
            "show_explicit": True,
            "default_quality": "flac",
            "auto_download": False,
            "show_suggestions": True,
            "max_search_results": 20,
            "max_top_tracks": 15,
            "max_albums_per_artist": 20,
            "max_trending_results": 20,
            "max_recommendations": 20,
            "preferred_markets": ["US"],
            "cache_ttl_preference": 3600,
            "created_at": current_time,
            "updated_at": current_time,
        }

        cls.insert_one(default_prefs)
        result = cls.execute(select(cls).where(cls.user_id == user_id))
        return next(result).scalar()

    @classmethod
    def update_preferences(cls, user_id: int, preferences: dict):
        """Update user catalog preferences"""
        preferences["updated_at"] = datetime.now().timestamp()

        return cls.execute(
            update(cls).where(cls.user_id == user_id).values(preferences), commit=True
        )

    def save(self):
        """Save current preferences state"""
        self.updated_at = datetime.now().timestamp()

        return self.execute(
            update(self.__class__)
            .where(self.__class__.id == self.id)
            .values(
                {
                    "show_explicit": self.show_explicit,
                    "default_quality": self.default_quality,
                    "auto_download": self.auto_download,
                    "show_suggestions": self.show_suggestions,
                    "preferred_genres": self.preferred_genres,
                    "excluded_genres": self.excluded_genres,
                    "max_search_results": self.max_search_results,
                    "max_top_tracks": self.max_top_tracks,
                    "max_albums_per_artist": self.max_albums_per_artist,
                    "max_trending_results": self.max_trending_results,
                    "max_recommendations": self.max_recommendations,
                    "preferred_markets": self.preferred_markets,
                    "cache_ttl_preference": self.cache_ttl_preference,
                    "updated_at": self.updated_at,
                }
            ),
            commit=True,
        )


class UniversalDownloadTable(Base):
    __tablename__ = "universal_downloads"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False, index=True)
    service: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # spotify, tidal, apple_music, etc.
    service_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # track, album, playlist, artist
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist: Mapped[str] = mapped_column(String(500), nullable=False)
    album: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_date: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Download settings
    quality: Mapped[str] = mapped_column(String(20), nullable=False, default="high")
    output_dir: Mapped[str] = mapped_column(String(1000), nullable=False, default="")

    # Download status
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(
        String(1000), nullable=True, default=None
    )
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Error handling
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)

    # Metadata
    catalog_metadata: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, default=None
    )

    # Timestamps
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    completed_at: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # User association
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id"), nullable=True, default=None
    )

    @classmethod
    def create(cls, data: dict):
        """Create a new universal download record"""
        if "created_at" not in data:
            data["created_at"] = datetime.now().timestamp()
        if "updated_at" not in data:
            data["updated_at"] = datetime.now().timestamp()

        return cls.insert_one(data)

    @classmethod
    def get_by_id(cls, download_id: int):
        """Get download by ID"""
        result = cls.execute(select(cls).where(cls.id == download_id))
        res = next(result).scalar()
        return res

    @classmethod
    def get_by_service_id(cls, service: str, service_id: str):
        """Get download by service and service ID"""
        result = cls.execute(
            select(cls).where(
                and_(cls.service == service, cls.service_id == service_id)
            )
        )
        res = next(result).scalar()
        return res

    @classmethod
    def get_by_url(cls, url: str):
        """Get download by URL"""
        result = cls.execute(select(cls).where(cls.url == url))
        res = next(result).scalar()
        return res

    @classmethod
    def get_pending_downloads(cls, limit: int = 50):
        """Get pending downloads"""
        result = cls.execute(
            select(cls)
            .where(cls.status == "pending")
            .order_by(cls.created_at)
            .limit(limit)
        )
        return list(next(result).scalars())

    @classmethod
    def get_active_downloads(cls):
        """Get currently active downloads"""
        result = cls.execute(
            select(cls)
            .where(cls.status.in_(["downloading", "processing"]))
            .order_by(cls.started_at)
        )
        return list(next(result).scalars())

    @classmethod
    def get_download_history(
        cls, user_id: int | None = None, limit: int = 100, offset: int = 0
    ):
        """Get download history with pagination"""
        query = select(cls).where(cls.status.in_(["completed", "failed", "cancelled"]))

        if user_id:
            query = query.where(cls.user_id == user_id)

        query = query.order_by(cls.created_at.desc()).offset(offset).limit(limit)
        result = cls.execute(query)
        return list(next(result).scalars())

    @classmethod
    def get_downloads_by_service(cls, service: str, limit: int = 50):
        """Get downloads by service"""
        result = cls.execute(
            select(cls)
            .where(cls.service == service)
            .order_by(cls.created_at.desc())
            .limit(limit)
        )
        return list(next(result).scalars())

    @classmethod
    def update_status(cls, download_id: int, status: str, **kwargs):
        """Update download status and related fields"""
        update_data = {"status": status, "updated_at": datetime.now().timestamp()}
        update_data.update(kwargs)

        return cls.execute(
            update(cls).where(cls.id == download_id).values(update_data), commit=True
        )

    @classmethod
    def update_progress(cls, download_id: int, progress: int):
        """Update download progress"""
        return cls.execute(
            update(cls)
            .where(cls.id == download_id)
            .values({"progress": progress, "updated_at": datetime.now().timestamp()}),
            commit=True,
        )

    @classmethod
    def increment_retry(cls, download_id: int):
        """Increment retry count"""
        return cls.execute(
            update(cls)
            .where(cls.id == download_id)
            .values(
                {
                    "retry_count": cls.retry_count + 1,
                    "updated_at": datetime.now().timestamp(),
                }
            ),
            commit=True,
        )

    @classmethod
    def delete_completed(cls, older_than_days: int = 30):
        """Delete completed downloads older than specified days"""
        cutoff_time = datetime.now().timestamp() - (older_than_days * 24 * 60 * 60)

        return cls.execute(
            delete(cls).where(
                and_(
                    cls.status.in_(["completed", "failed", "cancelled"]),
                    cls.completed_at < cutoff_time,
                )
            ),
            commit=True,
        )

    @classmethod
    def get_statistics(cls):
        """Get download statistics"""
        result = cls.execute(
            select(
                cls.service,
                cls.status,
                func.count(cls.id).label("count"),
                func.avg(cls.duration_ms).label("avg_duration"),
            ).group_by(cls.service, cls.status)
        )

        stats = {}
        for row in next(result):
            service = row.service
            if service not in stats:
                stats[service] = {}
            stats[service][row.status] = {
                "count": row.count,
                "avg_duration_ms": row.avg_duration,
            }

        return stats


class UniversalDownloadSourceTable(Base):
    __tablename__ = "universal_download_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    service: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )  # spotify, tidal, apple_music, etc.
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supported_types: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None
    )  # track, album, playlist, artist
    features: Mapped[list | None] = mapped_column(
        JSON, nullable=True, default=None
    )  # metadata, download, playlist
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    created_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    @classmethod
    def get_enabled_sources(cls):
        """Get all enabled download sources ordered by priority"""
        result = cls.execute(select(cls).where(cls.enabled).order_by(cls.priority))
        return list(next(result).scalars())

    @classmethod
    def get_by_service(cls, service: str):
        """Get source by service name"""
        result = cls.execute(select(cls).where(cls.service == service))
        res = next(result).scalar()
        return res

    @classmethod
    def update_source(cls, service: str, **kwargs):
        """Update source configuration"""
        kwargs["updated_at"] = datetime.now().timestamp()

        return cls.execute(
            update(cls).where(cls.service == service).values(kwargs), commit=True
        )


class UniversalDownloadQueueTable(Base):
    __tablename__ = "universal_download_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    download_id: Mapped[int] = mapped_column(
        ForeignKey("universal_downloads.id"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # Relationship to download
    download = relationship("UniversalDownloadTable", backref="queue_items")

    @classmethod
    def add_to_queue(cls, download_id: int, priority: int = 0):
        """Add download to queue"""
        # Get current max position
        result = cls.execute(select(func.max(cls.position)))
        max_position = next(result).scalar() or 0

        data = {
            "download_id": download_id,
            "priority": priority,
            "position": max_position + 1,
            "added_at": datetime.now().timestamp(),
        }

        return cls.insert_one(data)

    @classmethod
    def get_next_item(cls):
        """Get next item from queue"""
        result = cls.execute(
            select(cls)
            .join(UniversalDownloadTable)
            .where(
                and_(
                    UniversalDownloadTable.status == "pending", cls.started_at.is_(None)
                )
            )
            .order_by(cls.priority.desc(), cls.position)
            .limit(1)
        )
        res = next(result).scalar()
        return res

    @classmethod
    def remove_from_queue(cls, download_id: int):
        """Remove item from queue"""
        return cls.execute(
            delete(cls).where(cls.download_id == download_id), commit=True
        )

    @classmethod
    def get_queue_length(cls):
        """Get current queue length"""
        result = cls.execute(
            select(func.count(cls.id))
            .join(UniversalDownloadTable)
            .where(UniversalDownloadTable.status == "pending")
        )
        return next(result).scalar() or 0


# Create default universal download sources
def create_default_universal_sources():
    """Create default universal download sources if they don't exist"""
    default_sources = [
        {
            "service": "spotify",
            "display_name": "Spotify",
            "enabled": True,
            "priority": 1,
            "supported_types": ["track", "album", "playlist", "artist"],
            "features": ["metadata", "download", "playlist"],
            "config": {
                "quality_preference": ["lossless", "high", "medium", "low"],
                "formats": ["flac", "mp3", "aac"],
            },
        },
        {
            "service": "tidal",
            "display_name": "Tidal",
            "enabled": True,
            "priority": 2,
            "supported_types": ["track", "album", "playlist", "artist"],
            "features": ["metadata", "download", "playlist"],
            "config": {
                "quality_preference": ["lossless", "high", "medium", "low"],
                "formats": ["flac", "mp3", "aac"],
            },
        },
        {
            "service": "apple_music",
            "display_name": "Apple Music",
            "enabled": True,
            "priority": 3,
            "supported_types": ["track", "album", "playlist", "artist"],
            "features": ["metadata", "download", "playlist"],
            "config": {
                "quality_preference": ["lossless", "high", "medium", "low"],
                "formats": ["flac", "mp3", "aac"],
            },
        },
        {
            "service": "youtube_music",
            "display_name": "YouTube Music",
            "enabled": True,
            "priority": 4,
            "supported_types": ["video", "playlist", "channel"],
            "features": ["metadata", "download"],
            "config": {
                "quality_preference": ["high", "medium", "low"],
                "formats": ["mp3", "webm"],
            },
        },
        {
            "service": "youtube",
            "display_name": "YouTube",
            "enabled": True,
            "priority": 5,
            "supported_types": ["video", "playlist", "channel"],
            "features": ["metadata", "download"],
            "config": {
                "quality_preference": ["high", "medium", "low"],
                "formats": ["mp4", "webm", "mp3"],
            },
        },
        {
            "service": "soundcloud",
            "display_name": "SoundCloud",
            "enabled": True,
            "priority": 6,
            "supported_types": ["track", "playlist", "artist"],
            "features": ["metadata", "download"],
            "config": {
                "quality_preference": ["high", "medium", "low"],
                "formats": ["mp3"],
            },
        },
        {
            "service": "deezer",
            "display_name": "Deezer",
            "enabled": False,  # Disabled by default
            "priority": 7,
            "supported_types": ["track", "album", "playlist", "artist"],
            "features": ["metadata", "download", "playlist"],
            "config": {
                "quality_preference": ["lossless", "high", "medium", "low"],
                "formats": ["flac", "mp3"],
            },
        },
        {
            "service": "bandcamp",
            "display_name": "Bandcamp",
            "enabled": False,  # Disabled by default
            "priority": 8,
            "supported_types": ["track", "album"],
            "features": ["metadata", "download"],
            "config": {
                "quality_preference": ["lossless", "high", "medium", "low"],
                "formats": ["flac", "mp3", "aac"],
            },
        },
    ]

    current_time = datetime.now().timestamp()

    for source_data in default_sources:
        source_data["created_at"] = current_time
        source_data["updated_at"] = current_time

        existing = UniversalDownloadSourceTable.get_by_service(source_data["service"])
        if not existing:
            UniversalDownloadSourceTable.insert_one(source_data)


# Add execute method for new universal tables
for table_class in [
    UniversalDownloadTable,
    UniversalDownloadSourceTable,
    UniversalDownloadQueueTable,
]:
    if not hasattr(table_class, "execute"):

        @classmethod
        def execute_method(cls, query, commit=False):
            engine = DbEngine()
            with engine.session() as session:
                result = session.execute(query)
                if commit:
                    session.commit()
                return result

        table_class.execute = execute_method
        table_class.insert_one = lambda data: table_class.execute(
            insert(table_class).values(data), commit=True
        )
