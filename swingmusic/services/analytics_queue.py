"""
Analytics Event Queue using DragonflyDB.

Provides a high-performance event queue for analytics events using DragonflyDB
lists. Events are queued for batch processing, reducing database load and
enabling real-time analytics aggregation.
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from swingmusic.db.dragonfly_client import get_dragonfly_client

logger = logging.getLogger(__name__)

# Maximum queue size before forced flush
MAX_QUEUE_SIZE = 10000

# Event expiry (7 days)
EVENT_TTL = 7 * 24 * 60 * 60


class EventType(StrEnum):
    """Types of analytics events."""

    # Playback events
    TRACK_PLAY = "track_play"
    TRACK_SKIP = "track_skip"
    TRACK_COMPLETE = "track_complete"
    ALBUM_PLAY = "album_play"
    ARTIST_PLAY = "artist_play"

    # User interaction events
    FAVORITE_ADD = "favorite_add"
    FAVORITE_REMOVE = "favorite_remove"
    PLAYLIST_CREATE = "playlist_create"
    PLAYLIST_ADD_TRACK = "playlist_add_track"
    PLAYLIST_REMOVE_TRACK = "playlist_remove_track"

    # Search events
    SEARCH_QUERY = "search_query"
    SEARCH_RESULT_CLICK = "search_result_click"

    # Download events
    DOWNLOAD_START = "download_start"
    DOWNLOAD_COMPLETE = "download_complete"
    DOWNLOAD_FAIL = "download_fail"

    # Library events
    LIBRARY_SCAN = "library_scan"
    LIBRARY_UPDATE = "library_update"

    # Session events
    SESSION_START = "session_start"
    SESSION_END = "session_end"


@dataclass
class AnalyticsEvent:
    """Represents a single analytics event."""

    event_type: EventType
    timestamp: int
    userid: int
    data: dict[str, Any]
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "userid": self.userid,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalyticsEvent":
        return cls(
            event_id=data["event_id"],
            event_type=EventType(data["event_type"]),
            timestamp=data["timestamp"],
            userid=data["userid"],
            data=data["data"],
        )


class AnalyticsEventQueue:
    """
    Manages analytics events using DragonflyDB lists.

    Events are pushed to a Redis list and can be processed in batches
    by a background worker. This decouples event collection from
    event processing, improving application responsiveness.
    """

    def __init__(self, max_queue_size: int = MAX_QUEUE_SIZE):
        self.max_queue_size = max_queue_size
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = get_dragonfly_client()
        return self._client

    def _get_queue_key(self) -> str:
        """Get the main event queue key."""
        return "analytics:events:queue"

    def _get_processing_key(self) -> str:
        """Get the processing list key (for reliable queue pattern)."""
        return "analytics:events:processing"

    def _get_stats_key(self, event_type: EventType) -> str:
        """Get the key for event type statistics."""
        return f"analytics:stats:{event_type.value}"

    def _get_hourly_key(self, event_type: EventType, hour: int) -> str:
        """Get the key for hourly event counts."""
        return f"analytics:hourly:{event_type.value}:{hour}"

    def enqueue(self, event: AnalyticsEvent) -> bool:
        """
        Add an event to the queue.

        Args:
            event: The analytics event to enqueue

        Returns:
            True if successful, False otherwise
        """
        if not self.client.is_available():
            return False

        try:
            queue_key = self._get_queue_key()

            # Push event to queue (RPUSH for FIFO processing)
            self.client.client.rpush(queue_key, json.dumps(event.to_dict()))

            # Increment event type counter
            stats_key = self._get_stats_key(event.event_type)
            self.client.client.incr(stats_key)

            # Increment hourly counter
            hour = event.timestamp // 3600
            hourly_key = self._get_hourly_key(event.event_type, hour)
            self.client.client.incr(hourly_key)
            self.client.client.expire(hourly_key, EVENT_TTL)

            return True

        except Exception as e:
            logger.error(f"Failed to enqueue analytics event: {e}")
            return False

    def enqueue_batch(self, events: list[AnalyticsEvent]) -> int:
        """
        Add multiple events to the queue atomically.

        Returns:
            Number of events successfully enqueued
        """
        if not self.client.is_available():
            return 0

        try:
            queue_key = self._get_queue_key()
            pipe = self.client.client.pipeline()

            for event in events:
                pipe.rpush(queue_key, json.dumps(event.to_dict()))

                # Increment counters
                stats_key = self._get_stats_key(event.event_type)
                pipe.incr(stats_key)

                hour = event.timestamp // 3600
                hourly_key = self._get_hourly_key(event.event_type, hour)
                pipe.incr(hourly_key)
                pipe.expire(hourly_key, EVENT_TTL)

            pipe.execute()
            return len(events)

        except Exception as e:
            logger.error(f"Failed to enqueue batch: {e}")
            return 0

    def dequeue(self, batch_size: int = 100) -> list[AnalyticsEvent]:
        """
        Get a batch of events from the queue for processing.

        Uses the reliable queue pattern: events are moved to a processing
        list before being returned. They should be acknowledged after
        successful processing.

        Args:
            batch_size: Maximum number of events to dequeue

        Returns:
            List of analytics events
        """
        if not self.client.is_available():
            return []

        try:
            queue_key = self._get_queue_key()
            processing_key = self._get_processing_key()

            events = []

            for _ in range(batch_size):
                # RPOPLPUSH: atomically pop from queue and push to processing
                result = self.client.client.rpoplpush(queue_key, processing_key)

                if not result:
                    break

                try:
                    event = AnalyticsEvent.from_dict(json.loads(result))
                    events.append(event)
                except json.JSONDecodeError:
                    # Invalid event, remove from processing
                    self.client.client.lrem(processing_key, 1, result)
                    continue

            return events

        except Exception as e:
            logger.error(f"Failed to dequeue events: {e}")
            return []

    def acknowledge(self, event_ids: list[str]) -> int:
        """
        Acknowledge processed events, removing them from the processing list.

        Args:
            event_ids: List of event IDs to acknowledge

        Returns:
            Number of events acknowledged
        """
        if not self.client.is_available():
            return 0

        try:
            processing_key = self._get_processing_key()

            # Get all events in processing list
            all_events = self.client.client.lrange(processing_key, 0, -1)

            acknowledged = 0
            for event_json in all_events:
                try:
                    event_data = json.loads(event_json)
                    if event_data.get("event_id") in event_ids:
                        self.client.client.lrem(processing_key, 1, event_json)
                        acknowledged += 1
                except json.JSONDecodeError:
                    continue

            return acknowledged

        except Exception as e:
            logger.error(f"Failed to acknowledge events: {e}")
            return 0

    def requeue_unprocessed(self, timeout_seconds: int = 300) -> int:
        """
        Re-queue events that have been in processing for too long.

        This handles the case where a worker crashes while processing events.

        Args:
            timeout_seconds: Time after which processing events are considered stale

        Returns:
            Number of events re-queued
        """
        if not self.client.is_available():
            return 0

        try:
            queue_key = self._get_queue_key()
            processing_key = self._get_processing_key()

            # Move all processing events back to queue
            # In production, you'd check timestamps for timeout
            requeued = 0
            while True:
                result = self.client.client.rpoplpush(processing_key, queue_key)
                if not result:
                    break
                requeued += 1

            return requeued

        except Exception as e:
            logger.error(f"Failed to requeue unprocessed events: {e}")
            return 0

    def get_queue_size(self) -> int:
        """Get the number of events in the queue."""
        if not self.client.is_available():
            return 0

        try:
            return self.client.client.llen(self._get_queue_key())
        except Exception:
            return 0

    def get_event_count(self, event_type: EventType) -> int:
        """Get the total count for an event type."""
        if not self.client.is_available():
            return 0

        try:
            key = self._get_stats_key(event_type)
            value = self.client.get(key)
            return int(value) if value else 0
        except Exception:
            return 0

    def get_hourly_counts(
        self, event_type: EventType, start_hour: int, end_hour: int
    ) -> dict[int, int]:
        """
        Get hourly event counts for a time range.

        Args:
            event_type: The event type to query
            start_hour: Start hour (Unix timestamp / 3600)
            end_hour: End hour (Unix timestamp / 3600)

        Returns:
            Dict mapping hour to event count
        """
        if not self.client.is_available():
            return {}

        try:
            counts = {}
            for hour in range(start_hour, end_hour + 1):
                key = self._get_hourly_key(event_type, hour)
                value = self.client.get(key)
                counts[hour] = int(value) if value else 0

            return counts

        except Exception as e:
            logger.error(f"Failed to get hourly counts: {e}")
            return {}

    def clear_queue(self) -> bool:
        """Clear all events from the queue."""
        if not self.client.is_available():
            return False

        try:
            self.client.client.delete(self._get_queue_key())
            self.client.client.delete(self._get_processing_key())
            return True
        except Exception:
            return False


# Helper functions for common events
def track_played(
    userid: int,
    trackhash: str,
    duration: int,
    source: str,
) -> AnalyticsEvent:
    """Create a track play event."""
    return AnalyticsEvent(
        event_type=EventType.TRACK_PLAY,
        timestamp=int(time.time()),
        userid=userid,
        data={
            "trackhash": trackhash,
            "duration": duration,
            "source": source,
        },
    )


def track_skipped(
    userid: int,
    trackhash: str,
    position: int,
) -> AnalyticsEvent:
    """Create a track skip event."""
    return AnalyticsEvent(
        event_type=EventType.TRACK_SKIP,
        timestamp=int(time.time()),
        userid=userid,
        data={
            "trackhash": trackhash,
            "position": position,
        },
    )


def favorite_toggled(
    userid: int,
    item_type: str,
    itemhash: str,
    added: bool,
) -> AnalyticsEvent:
    """Create a favorite toggle event."""
    return AnalyticsEvent(
        event_type=EventType.FAVORITE_ADD if added else EventType.FAVORITE_REMOVE,
        timestamp=int(time.time()),
        userid=userid,
        data={
            "item_type": item_type,
            "itemhash": itemhash,
        },
    )


def search_performed(
    userid: int,
    query: str,
    results_count: int,
    filters: dict[str, Any] | None = None,
) -> AnalyticsEvent:
    """Create a search event."""
    return AnalyticsEvent(
        event_type=EventType.SEARCH_QUERY,
        timestamp=int(time.time()),
        userid=userid,
        data={
            "query": query,
            "results_count": results_count,
            "filters": filters or {},
        },
    )


# Global instance
analytics_queue = AnalyticsEventQueue()


def get_analytics_queue() -> AnalyticsEventQueue:
    """Get the global analytics queue instance."""
    return analytics_queue
