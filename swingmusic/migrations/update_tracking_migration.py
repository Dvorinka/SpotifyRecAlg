"""
Migration for Update Tracking System Tables

This migration creates all the necessary tables for the artist update
tracking system, including follows, releases, notifications, and preferences.
"""

import logging

from swingmusic.db import db
from swingmusic.migrations.base import Migration

logger = logging.getLogger(__name__)


class Migration001UpdateTracking(Migration):
    """
    Create tables for the update tracking system
    """

    @staticmethod
    def migrate():
        """
        Create all update tracking tables
        """
        logger.info("Starting update tracking migration")

        try:
            # Create artist_follows table
            logger.info("Creating artist_follows table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS artist_follows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    artist_id TEXT NOT NULL UNIQUE,
                    artist_name TEXT NOT NULL,
                    follow_level TEXT NOT NULL DEFAULT 'followed',
                    auto_download_new_releases BOOLEAN DEFAULT FALSE,
                    preferred_quality TEXT DEFAULT 'flac',
                    notification_preferences TEXT DEFAULT '{}',
                    follow_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_check_date DATETIME NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """)

            # Create release_updates table
            logger.info("Creating release_updates table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS release_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL UNIQUE,
                    artist_id TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    release_title TEXT NOT NULL,
                    release_type TEXT NOT NULL,
                    release_date DATE NOT NULL,
                    spotify_url TEXT NOT NULL,
                    cover_image_url TEXT NULL,
                    total_tracks INTEGER NOT NULL,
                    popularity INTEGER DEFAULT 0,
                    explicit BOOLEAN DEFAULT FALSE,
                    discovered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    processed_at DATETIME NULL,
                    download_status TEXT DEFAULT 'pending',
                    auto_downloaded BOOLEAN DEFAULT FALSE,
                    notification_sent BOOLEAN DEFAULT FALSE
                )
            """)

            # Create update_notifications table
            logger.info("Creating update_notifications table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS update_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    release_id TEXT NOT NULL,
                    notification_type TEXT NOT NULL,
                    sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    opened_at DATETIME NULL,
                    action_taken TEXT NULL,
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (release_id) REFERENCES release_updates (release_id)
                )
            """)

            # Create update_monitoring_preferences table
            logger.info("Creating update_monitoring_preferences table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS update_monitoring_preferences (
                    user_id INTEGER PRIMARY KEY,
                    enable_artist_monitoring BOOLEAN DEFAULT TRUE,
                    check_frequency TEXT DEFAULT 'daily',
                    auto_download_favorites BOOLEAN DEFAULT FALSE,
                    auto_download_followed BOOLEAN DEFAULT FALSE,
                    max_auto_downloads_per_week INTEGER DEFAULT 5,
                    quality_preference TEXT DEFAULT 'flac',
                    storage_limit_mb INTEGER DEFAULT 10240,
                    notification_channels TEXT DEFAULT '{}',
                    exclude_explicit BOOLEAN DEFAULT FALSE,
                    preferred_release_types TEXT DEFAULT '["album", "ep", "single"]',
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """)

            # Create download_tasks table
            logger.info("Creating download_tasks table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS download_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    track_title TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT NOT NULL,
                    spotify_url TEXT NOT NULL,
                    quality_preference TEXT DEFAULT 'flac',
                    status TEXT DEFAULT 'pending',
                    priority TEXT DEFAULT 'normal',
                    progress INTEGER DEFAULT 0,
                    file_path TEXT NULL,
                    error_message TEXT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME NULL,
                    completed_at DATETIME NULL,
                    auto_downloaded BOOLEAN DEFAULT FALSE,
                    added_to_library BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (release_id) REFERENCES release_updates (release_id)
                )
            """)

            # Create artist_follow_history table
            logger.info("Creating artist_follow_history table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS artist_follow_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    artist_id TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    old_level TEXT NULL,
                    new_level TEXT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            """)

            # Create release_update_history table
            logger.info("Creating release_update_history table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS release_update_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    release_id TEXT NOT NULL,
                    artist_id TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    release_title TEXT NOT NULL,
                    release_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT NULL
                )
            """)

            # Create update_tracking_stats table
            logger.info("Creating update_tracking_stats table")
            db.session.execute("""
                CREATE TABLE IF NOT EXISTS update_tracking_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    stat_date DATE NOT NULL,
                    total_followed_artists INTEGER DEFAULT 0,
                    new_releases_discovered INTEGER DEFAULT 0,
                    auto_downloads_completed INTEGER DEFAULT 0,
                    manual_downloads_completed INTEGER DEFAULT 0,
                    notifications_sent INTEGER DEFAULT 0,
                    notifications_opened INTEGER DEFAULT 0,
                    storage_used_mb INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    UNIQUE(user_id, stat_date)
                )
            """)

            # Create indexes for better performance
            logger.info("Creating indexes")

            # Indexes for artist_follows
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_follows_user_id ON artist_follows(user_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_follows_artist_id ON artist_follows(artist_id)
            """)

            # Indexes for release_updates
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_release_updates_artist_id ON release_updates(artist_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_release_updates_release_date ON release_updates(release_date)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_release_updates_discovered_at ON release_updates(discovered_at)
            """)

            # Indexes for update_notifications
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_update_notifications_user_id ON update_notifications(user_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_update_notifications_release_id ON update_notifications(release_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_update_notifications_sent_at ON update_notifications(sent_at)
            """)

            # Indexes for download_tasks
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_tasks_release_id ON download_tasks(release_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_tasks_status ON download_tasks(status)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_tasks_priority ON download_tasks(priority)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_download_tasks_created_at ON download_tasks(created_at)
            """)

            # Indexes for history tables
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_follow_history_user_id ON artist_follow_history(user_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_artist_follow_history_timestamp ON artist_follow_history(timestamp)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_release_update_history_release_id ON release_update_history(release_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_release_update_history_timestamp ON release_update_history(timestamp)
            """)

            # Indexes for stats
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_update_tracking_stats_user_id ON update_tracking_stats(user_id)
            """)
            db.session.execute("""
                CREATE INDEX IF NOT EXISTS idx_update_tracking_stats_stat_date ON update_tracking_stats(stat_date)
            """)

            # Commit the transaction
            db.session.commit()
            logger.info("Update tracking migration completed successfully")

        except Exception as e:
            logger.error(f"Error during update tracking migration: {e}")
            db.session.rollback()
            raise


class Migration002UpdateTrackingTriggers(Migration):
    """
    Create triggers for update tracking system
    """

    @staticmethod
    def migrate():
        """
        Create triggers for automatic history tracking
        """
        logger.info("Creating update tracking triggers")

        try:
            # Trigger for artist follow history
            db.session.execute("""
                CREATE TRIGGER IF NOT EXISTS artist_follow_history_insert
                AFTER INSERT ON artist_follows
                BEGIN
                    INSERT INTO artist_follow_history
                    (user_id, artist_id, artist_name, action, new_level, timestamp)
                    VALUES
                    (NEW.user_id, NEW.artist_id, NEW.artist_name, 'follow', NEW.follow_level, CURRENT_TIMESTAMP);
                END
            """)

            # Trigger for artist unfollow history
            db.session.execute("""
                CREATE TRIGGER IF NOT EXISTS artist_follow_history_delete
                AFTER DELETE ON artist_follows
                BEGIN
                    INSERT INTO artist_follow_history
                    (user_id, artist_id, artist_name, action, old_level, timestamp)
                    VALUES
                    (OLD.user_id, OLD.artist_id, OLD.artist_name, 'unfollow', OLD.follow_level, CURRENT_TIMESTAMP);
                END
            """)

            # Trigger for artist follow level change
            db.session.execute("""
                CREATE TRIGGER IF NOT EXISTS artist_follow_history_update
                AFTER UPDATE ON artist_follows
                WHEN OLD.follow_level != NEW.follow_level
                BEGIN
                    INSERT INTO artist_follow_history
                    (user_id, artist_id, artist_name, action, old_level, new_level, timestamp)
                    VALUES
                    (NEW.user_id, NEW.artist_id, NEW.artist_name, 'level_change', OLD.follow_level, NEW.follow_level, CURRENT_TIMESTAMP);
                END
            """)

            # Trigger for release update discovery
            db.session.execute("""
                CREATE TRIGGER IF NOT EXISTS release_update_discovered
                AFTER INSERT ON release_updates
                BEGIN
                    INSERT INTO release_update_history
                    (release_id, artist_id, artist_name, release_title, release_type, action, timestamp)
                    VALUES
                    (NEW.release_id, NEW.artist_id, NEW.artist_name, NEW.release_title, NEW.release_type, 'discovered', CURRENT_TIMESTAMP);
                END
            """)

            # Trigger for release update download completion
            db.session.execute("""
                CREATE TRIGGER IF NOT EXISTS release_update_downloaded
                AFTER UPDATE ON release_updates
                WHEN OLD.download_status != 'completed' AND NEW.download_status = 'completed'
                BEGIN
                    INSERT INTO release_update_history
                    (release_id, artist_id, artist_name, release_title, release_type, action, timestamp, metadata)
                    VALUES
                    (NEW.release_id, NEW.artist_id, NEW.artist_name, NEW.release_title, NEW.release_type, 'downloaded', CURRENT_TIMESTAMP,
                     json_object('auto_downloaded', NEW.auto_downloaded));
                END
            """)

            db.session.commit()
            logger.info("Update tracking triggers created successfully")

        except Exception as e:
            logger.error(f"Error creating update tracking triggers: {e}")
            db.session.rollback()
            raise
