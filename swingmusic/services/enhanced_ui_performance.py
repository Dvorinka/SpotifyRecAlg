"""
Enhanced UI Performance Service for SwingMusic
Provides virtual scrolling, lazy loading, and performance optimizations for large libraries
"""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from swingmusic import logger
from swingmusic.db.sqlite.utils import get_db_connection


class ItemType(Enum):
    TRACK = "track"
    ALBUM = "album"
    ARTIST = "artist"
    PLAYLIST = "playlist"
    FOLDER = "folder"


@dataclass
class VirtualItem:
    """Item in a virtual list"""

    id: str
    item_type: ItemType
    title: str
    subtitle: str
    image_url: str | None
    data: dict[str, Any]
    index: int
    height: int = 60
    loaded: bool = False
    visible: bool = False


@dataclass
class ViewportConfig:
    """Viewport configuration for virtual scrolling"""

    item_height: int = 60
    viewport_height: int = 600
    buffer_size: int = 10
    overscan: int = 5


@dataclass
class PerformanceMetrics:
    """Performance metrics for UI operations"""

    render_time: float
    item_count: int
    visible_items: int
    memory_usage: int
    scroll_fps: float


class VirtualScrollManager:
    """Manages virtual scrolling for large lists"""

    def __init__(self, config: ViewportConfig):
        self.config = config
        self.items: list[VirtualItem] = []
        self.visible_start = 0
        self.visible_end = 0
        self.scroll_top = 0
        self.last_render_time = 0
        self.render_callbacks: list[Callable] = []

    def set_items(self, items: list[VirtualItem]):
        """Set the items for virtual scrolling"""
        self.items = items
        self._update_visible_range()

    def update_scroll_position(self, scroll_top: int):
        """Update scroll position and recalculate visible items"""
        self.scroll_top = scroll_top
        self._update_visible_range()

    def _update_visible_range(self):
        """Calculate which items should be visible"""
        if not self.items:
            self.visible_start = 0
            self.visible_end = 0
            return

        start_index = max(
            0, self.scroll_top // self.config.item_height - self.config.overscan
        )
        end_index = min(
            len(self.items),
            ((self.scroll_top + self.config.viewport_height) // self.config.item_height)
            + self.config.overscan,
        )

        self.visible_start = start_index
        self.visible_end = end_index

        # Update item visibility
        for i, item in enumerate(self.items):
            item.visible = start_index <= i < end_index

    def get_visible_items(self) -> list[VirtualItem]:
        """Get currently visible items"""
        return self.items[self.visible_start : self.visible_end]

    def get_total_height(self) -> int:
        """Get total height of all items"""
        return len(self.items) * self.config.item_height

    def get_offset_y(self) -> int:
        """Get Y offset for visible items"""
        return self.visible_start * self.config.item_height

    def add_render_callback(self, callback: Callable):
        """Add callback for render events"""
        self.render_callbacks.append(callback)

    def trigger_render(self):
        """Trigger render with performance tracking"""
        start_time = time.time()

        # Notify callbacks
        for callback in self.render_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error(f"Error in render callback: {e}")

        self.last_render_time = time.time() - start_time


class LazyImageLoader:
    """Manages lazy loading of images with intersection observer simulation"""

    def __init__(self, max_concurrent: int = 6):
        self.max_concurrent = max_concurrent
        self.loading_queue: list[tuple[str, Callable]] = []
        self.loading_images: set[str] = set()
        self.loaded_images: dict[str, str] = {}
        self.failed_images: set[str] = set()

    def load_image(self, image_url: str, callback: Callable[[str], None]):
        """Load an image with callback"""
        if image_url in self.loaded_images:
            callback(self.loaded_images[image_url])
            return

        if image_url in self.failed_images:
            callback("")  # Return empty string for failed images
            return

        if image_url in self.loading_images:
            # Already loading, add to queue
            self.loading_queue.append((image_url, callback))
            return

        self._start_loading(image_url, callback)

    def _start_loading(self, image_url: str, callback: Callable[[str], None]):
        """Start loading an image"""
        if len(self.loading_images) >= self.max_concurrent:
            self.loading_queue.append((image_url, callback))
            return

        self.loading_images.add(image_url)

        # Simulate image loading (in real implementation, use actual image loading)
        asyncio.create_task(self._load_image_async(image_url, callback))

    async def _load_image_async(self, image_url: str, callback: Callable[[str], None]):
        """Async image loading simulation"""
        try:
            # Simulate network delay
            await asyncio.sleep(0.1)

            # In real implementation, load actual image data
            # For now, just return the URL as "loaded"
            self.loaded_images[image_url] = image_url

            # Remove from loading set
            self.loading_images.discard(image_url)

            # Call callback
            callback(image_url)

            # Process next in queue
            if self.loading_queue:
                next_url, next_callback = self.loading_queue.pop(0)
                self._start_loading(next_url, next_callback)

        except Exception as e:
            logger.error(f"Error loading image {image_url}: {e}")
            self.loading_images.discard(image_url)
            self.failed_images.add(image_url)
            callback("")

    def preload_images(self, image_urls: list[str]):
        """Preload a list of images"""
        for url in image_urls:
            if url not in self.loaded_images and url not in self.failed_images:
                self.load_image(url, lambda _: None)


class PerformanceOptimizer:
    """Optimizes UI performance for large datasets"""

    def __init__(self):
        self.metrics: list[PerformanceMetrics] = []
        self.debounce_timers: dict[str, float] = {}
        self.throttle_intervals: dict[str, float] = {}

    def debounce(self, key: str, func: Callable, delay: float = 0.1):
        """Debounce function calls"""
        current_time = time.time()

        if key in self.debounce_timers:
            if current_time - self.debounce_timers[key] < delay:
                return

        self.debounce_timers[key] = current_time
        asyncio.create_task(self._debounce_async(key, func, delay))

    async def _debounce_async(self, key: str, func: Callable, delay: float):
        """Async debounce implementation"""
        await asyncio.sleep(delay)

        # Check if still the latest call
        if key in self.debounce_timers:
            try:
                func()
            except Exception as e:
                logger.error(f"Error in debounced function: {e}")

    def throttle(self, key: str, func: Callable, interval: float = 0.016):  # 60fps
        """Throttle function calls"""
        current_time = time.time()

        if key in self.throttle_intervals:
            if current_time - self.throttle_intervals[key] < interval:
                return

        self.throttle_intervals[key] = current_time
        try:
            func()
        except Exception as e:
            logger.error(f"Error in throttled function: {e}")

    def measure_performance(self, operation: str, func: Callable) -> Any:
        """Measure performance of an operation"""
        start_time = time.time()
        start_memory = self._get_memory_usage()

        try:
            result = func()
            end_time = time.time()
            end_memory = self._get_memory_usage()

            metrics = PerformanceMetrics(
                render_time=end_time - start_time,
                item_count=0,  # Would be context-specific
                visible_items=0,
                memory_usage=end_memory - start_memory,
                scroll_fps=1.0 / (end_time - start_time)
                if end_time > start_time
                else 0,
            )

            self.metrics.append(metrics)
            logger.debug(
                f"Performance metrics for {operation}: {metrics.render_time:.3f}s"
            )

            return result

        except Exception as e:
            logger.error(f"Error in performance measurement for {operation}: {e}")
            raise

    def _get_memory_usage(self) -> int:
        """Get current memory usage (simplified)"""
        try:
            import psutil

            return psutil.Process().memory_info().rss
        except ImportError:
            return 0

    def get_average_performance(self) -> PerformanceMetrics | None:
        """Get average performance metrics"""
        if not self.metrics:
            return None

        avg_render_time = sum(m.render_time for m in self.metrics) / len(self.metrics)
        avg_memory = sum(m.memory_usage for m in self.metrics) / len(self.metrics)
        avg_fps = sum(m.scroll_fps for m in self.metrics) / len(self.metrics)

        return PerformanceMetrics(
            render_time=avg_render_time,
            item_count=sum(m.item_count for m in self.metrics),
            visible_items=sum(m.visible_items for m in self.metrics),
            memory_usage=int(avg_memory),
            scroll_fps=avg_fps,
        )


class EnhancedUIManager:
    """Enhanced UI manager with performance optimizations"""

    def __init__(self):
        self.virtual_scroll = VirtualScrollManager(ViewportConfig())
        self.image_loader = LazyImageLoader()
        self.performance_optimizer = PerformanceOptimizer()
        self.cached_data: dict[str, Any] = {}
        self.cache_ttl = 300  # 5 minutes

    async def get_tracks_paginated(
        self, offset: int = 0, limit: int = 50, filters: dict[str, Any] = None
    ) -> dict[str, Any]:
        """Get tracks with pagination and caching"""
        cache_key = f"tracks_{offset}_{limit}_{json.dumps(filters or {})}"

        # Check cache
        if cache_key in self.cached_data:
            cached_time, cached_data = self.cached_data[cache_key]
            if time.time() - cached_time < self.cache_ttl:
                return cached_data

        # Fetch from database
        try:
            with get_db_connection() as conn:
                query = """
                SELECT t.trackhash, t.title, t.artists, t.album, t.duration,
                       t.bitrate, t.image, t.folderpath, t.filename
                FROM tracks t
                """

                conditions = []
                params = []

                if filters:
                    if "artist" in filters:
                        conditions.append("t.artists LIKE ?")
                        params.append(f"%{filters['artist']}%")

                    if "album" in filters:
                        conditions.append("t.album LIKE ?")
                        params.append(f"%{filters['album']}%")

                    if "genre" in filters:
                        # Would need genre table join
                        pass

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                query += " ORDER BY t.artists, t.album, t.tracknumber LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                cursor = conn.execute(query, params)
                tracks = cursor.fetchall()

                # Get total count
                count_query = "SELECT COUNT(*) FROM tracks t"
                if conditions:
                    count_query += " WHERE " + " AND ".join(conditions)

                cursor = conn.execute(count_query, params[:-2])  # Exclude limit/offset
                total_count = cursor.fetchone()[0]

                result = {
                    "tracks": [dict(track) for track in tracks],
                    "total": total_count,
                    "offset": offset,
                    "limit": limit,
                }

                # Cache result
                self.cached_data[cache_key] = (time.time(), result)

                return result

        except Exception as e:
            logger.error(f"Error fetching tracks: {e}")
            return {"tracks": [], "total": 0, "offset": offset, "limit": limit}

    def create_virtual_items(self, tracks: list[dict[str, Any]]) -> list[VirtualItem]:
        """Create virtual items from track data"""
        items = []

        for i, track in enumerate(tracks):
            item = VirtualItem(
                id=track["trackhash"],
                item_type=ItemType.TRACK,
                title=track["title"],
                subtitle=f"{track['artists']} • {track['album']}",
                image_url=track.get("image"),
                data=track,
                index=i,
            )
            items.append(item)

        return items

    def optimize_scroll_performance(self, scroll_callback: Callable):
        """Optimize scroll performance with throttling"""

        def optimized_scroll(scroll_top: int):
            self.performance_optimizer.throttle(
                "scroll",
                lambda: self._handle_scroll(scroll_top, scroll_callback),
                0.016,  # 60fps
            )

        return optimized_scroll

    def _handle_scroll(self, scroll_top: int, callback: Callable):
        """Handle scroll with virtual scrolling"""
        self.virtual_scroll.update_scroll_position(scroll_top)
        callback()

    def preload_nearby_images(self, visible_items: list[VirtualItem]):
        """Preload images for visible and nearby items"""
        image_urls = []

        for item in visible_items:
            if item.image_url:
                image_urls.append(item.image_url)

        # Add nearby items for smoother scrolling
        start = max(0, self.virtual_scroll.visible_start - 5)
        end = min(len(self.virtual_scroll.items), self.virtual_scroll.visible_end + 5)

        for item in self.virtual_scroll.items[start:end]:
            if item.image_url and item.image_url not in image_urls:
                image_urls.append(item.image_url)

        self.image_loader.preload_images(image_urls)

    def clear_cache(self):
        """Clear all caches"""
        self.cached_data.clear()
        self.image_loader.loaded_images.clear()
        self.image_loader.failed_images.clear()

    def get_performance_report(self) -> dict[str, Any]:
        """Get performance report"""
        avg_metrics = self.performance_optimizer.get_average_performance()

        return {
            "average_render_time": avg_metrics.render_time if avg_metrics else 0,
            "average_fps": avg_metrics.scroll_fps if avg_metrics else 0,
            "memory_usage": avg_metrics.memory_usage if avg_metrics else 0,
            "cached_items": len(self.cached_data),
            "loaded_images": len(self.image_loader.loaded_images),
            "failed_images": len(self.image_loader.failed_images),
            "virtual_items": len(self.virtual_scroll.items),
            "visible_items": len(self.virtual_scroll.get_visible_items()),
        }


# Global enhanced UI manager instance
enhanced_ui_manager = EnhancedUIManager()
