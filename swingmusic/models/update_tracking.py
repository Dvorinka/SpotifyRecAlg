"""
Update Tracking Database Models

This module contains the database models for the artist update tracking system,
including artist follows, release updates, notifications, and user preferences.
"""

import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from swingmusic.db.base import Base


class ArtistFollow(Base):
    """
    Represents a user following an artist for update tracking
    """

    __tablename__ = "artist_follows"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    artist_id = Column(String(100), nullable=False, unique=True)  # Spotify artist ID
    artist_name = Column(String(255), nullable=False)
    follow_level = Column(
        String(20), nullable=False, default="followed"
    )  # 'favorite', 'followed', 'casual'
    auto_download_new_releases = Column(Boolean, default=False)
    preferred_quality = Column(String(20), default="flac")
    notification_preferences = Column(
        JSON, default=dict
    )  # {in_app: true, push: false, email: false}
    follow_date = Column(DateTime, default=datetime.datetime.utcnow)
    last_check_date = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="artist_follows")
    release_updates = relationship("ReleaseUpdate", back_populates="artist_follow")

    def __repr__(self):
        return f"<ArtistFollow(user_id={self.user_id}, artist='{self.artist_name}')>"


class ReleaseUpdate(Base):
    """
    Represents a new release discovered from a followed artist
    """

    __tablename__ = "release_updates"

    id = Column(Integer, primary_key=True)
    release_id = Column(String(100), nullable=False, unique=True)  # Spotify release ID
    artist_id = Column(String(100), nullable=False)  # Spotify artist ID
    artist_name = Column(String(255), nullable=False)
    release_title = Column(String(255), nullable=False)
    release_type = Column(
        String(20), nullable=False
    )  # 'album', 'single', 'ep', 'compilation'
    release_date = Column(Date, nullable=False)
    spotify_url = Column(Text, nullable=False)
    cover_image_url = Column(Text, nullable=True)
    total_tracks = Column(Integer, nullable=False)
    popularity = Column(Integer, default=0)
    explicit = Column(Boolean, default=False)
    discovered_at = Column(DateTime, default=datetime.datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)
    download_status = Column(
        String(20), default="pending"
    )  # 'pending', 'queued', 'downloading', 'completed', 'failed'
    auto_downloaded = Column(Boolean, default=False)
    notification_sent = Column(Boolean, default=False)

    # Relationships
    artist_follow = relationship("ArtistFollow", back_populates="release_updates")
    download_tasks = relationship("DownloadTask", back_populates="release_update")
    notifications = relationship("UpdateNotification", back_populates="release_update")

    def __repr__(self):
        return f"<ReleaseUpdate(title='{self.release_title}', artist='{self.artist_name}')>"


class UpdateNotification(Base):
    """
    Represents notifications sent to users about new releases
    """

    __tablename__ = "update_notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    release_id = Column(
        String(100), ForeignKey("release_updates.release_id"), nullable=False
    )
    notification_type = Column(
        String(50), nullable=False
    )  # 'new_release', 'artist_update', 'back_in_stock'
    sent_at = Column(DateTime, default=datetime.datetime.utcnow)
    opened_at = Column(DateTime, nullable=True)
    action_taken = Column(
        String(50), nullable=True
    )  # 'downloaded', 'played', 'dismissed'

    # Relationships
    user = relationship("User")
    release_update = relationship("ReleaseUpdate", back_populates="notifications")

    def __repr__(self):
        return f"<UpdateNotification(user_id={self.user_id}, type='{self.notification_type}')>"


class UpdateMonitoringPreferences(Base):
    """
    User preferences for update monitoring
    """

    __tablename__ = "update_monitoring_preferences"

    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    enable_artist_monitoring = Column(Boolean, default=True)
    check_frequency = Column(String(20), default="daily")  # 'hourly', 'daily', 'weekly'
    auto_download_favorites = Column(Boolean, default=False)
    auto_download_followed = Column(Boolean, default=False)
    max_auto_downloads_per_week = Column(Integer, default=5)
    quality_preference = Column(String(20), default="flac")
    storage_limit_mb = Column(Integer, default=10240)
    notification_channels = Column(
        JSON, default=dict
    )  # {in_app: true, push: false, email: false, discord: false}
    exclude_explicit = Column(Boolean, default=False)
    preferred_release_types = Column(JSON, default=list)  # ['album', 'ep', 'single']

    # Relationships
    user = relationship("User", back_populates="update_preferences")

    def __repr__(self):
        return f"<UpdateMonitoringPreferences(user_id={self.user_id})>"


class DownloadTask(Base):
    """
    Represents download tasks created from release updates
    """

    __tablename__ = "download_tasks"

    id = Column(Integer, primary_key=True)
    release_id = Column(
        String(100), ForeignKey("release_updates.release_id"), nullable=False
    )
    track_id = Column(String(100), nullable=False)  # Spotify track ID
    track_title = Column(String(255), nullable=False)
    artist_name = Column(String(255), nullable=False)
    album_name = Column(String(255), nullable=False)
    spotify_url = Column(Text, nullable=False)
    quality_preference = Column(String(20), default="flac")
    status = Column(
        String(20), default="pending"
    )  # 'pending', 'queued', 'downloading', 'completed', 'failed'
    priority = Column(String(20), default="normal")  # 'low', 'normal', 'high', 'urgent'
    progress = Column(Integer, default=0)  # 0-100
    file_path = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    auto_downloaded = Column(Boolean, default=False)
    added_to_library = Column(Boolean, default=False)

    # Relationships
    release_update = relationship("ReleaseUpdate", back_populates="download_tasks")

    def __repr__(self):
        return f"<DownloadTask(track='{self.track_title}', status='{self.status}')>"


class ArtistFollowHistory(Base):
    """
    Historical tracking of artist follows for analytics
    """

    __tablename__ = "artist_follow_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    artist_id = Column(String(100), nullable=False)
    artist_name = Column(String(255), nullable=False)
    action = Column(String(20), nullable=False)  # 'follow', 'unfollow', 'level_change'
    old_level = Column(String(20), nullable=True)
    new_level = Column(String(20), nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    user = relationship("User")

    def __repr__(self):
        return f"<ArtistFollowHistory(user_id={self.user_id}, action='{self.action}')>"


class ReleaseUpdateHistory(Base):
    """
    Historical tracking of release updates for analytics
    """

    __tablename__ = "release_update_history"

    id = Column(Integer, primary_key=True)
    release_id = Column(String(100), nullable=False)
    artist_id = Column(String(100), nullable=False)
    artist_name = Column(String(255), nullable=False)
    release_title = Column(String(255), nullable=False)
    release_type = Column(String(20), nullable=False)
    action = Column(
        String(20), nullable=False
    )  # 'discovered', 'downloaded', 'notification_sent', 'completed'
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    metadata = Column(JSON, nullable=True)  # Additional data about the action

    def __repr__(self):
        return f"<ReleaseUpdateHistory(release='{self.release_title}', action='{self.action}')>"


class UpdateTrackingStats(Base):
    """
    Aggregated statistics for update tracking
    """

    __tablename__ = "update_tracking_stats"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    stat_date = Column(Date, nullable=False)
    total_followed_artists = Column(Integer, default=0)
    new_releases_discovered = Column(Integer, default=0)
    auto_downloads_completed = Column(Integer, default=0)
    manual_downloads_completed = Column(Integer, default=0)
    notifications_sent = Column(Integer, default=0)
    notifications_opened = Column(Integer, default=0)
    storage_used_mb = Column(Integer, default=0)

    # Relationships
    user = relationship("User")

    def __repr__(self):
        return f"<UpdateTrackingStats(user_id={self.user_id}, date={self.stat_date})>"


# Update the User model to include the new relationships
# This would need to be added to the User model in user.py:
#
# from swingmusic.models.update_tracking import ArtistFollow, UpdateMonitoringPreferences
#
# class User(Base):
#     # ... existing fields ...
#
#     # Update tracking relationships
#     artist_follows = relationship("ArtistFollow", back_populates="user")
#     update_preferences = relationship("UpdateMonitoringPreferences", back_populates="user", uselist=False)
