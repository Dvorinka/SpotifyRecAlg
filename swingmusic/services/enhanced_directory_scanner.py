"""
Enhanced Directory Scanner for SwingMusic
Handles multiple music directories with parallel scanning, permission validation, and error handling
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from swingmusic import logger


@dataclass
class ScanResult:
    """Result of directory scanning operation"""

    directory: str
    success: bool
    files_found: int
    folders_found: int
    errors: list[str]
    scan_time: float
    permissions_ok: bool


@dataclass
class FileInfo:
    """Information about a scanned file"""

    path: str
    size: int
    modified_time: float
    is_audio: bool
    extension: str


@dataclass
class DirectoryStats:
    """Statistics for a scanned directory"""

    total_files: int
    audio_files: int
    total_size: int
    last_scan_time: float
    scan_duration: float
    errors: list[str]


class PermissionValidator:
    """Validates directory permissions for scanning"""

    @staticmethod
    async def validate_directory(directory: str) -> tuple[bool, list[str]]:
        """Validate if directory can be accessed and scanned"""
        errors = []

        try:
            path = Path(directory)

            # Check if directory exists
            if not path.exists():
                errors.append(f"Directory does not exist: {directory}")
                return False, errors

            # Check if it's actually a directory
            if not path.is_dir():
                errors.append(f"Path is not a directory: {directory}")
                return False, errors

            # Check read permissions
            if not os.access(directory, os.R_OK):
                errors.append(f"No read permission for directory: {directory}")
                return False, errors

            # Check execute permissions (needed for directory traversal)
            if not os.access(directory, os.X_OK):
                errors.append(f"No execute permission for directory: {directory}")
                return False, errors

            # Try to list directory contents
            try:
                list(path.iterdir())
            except PermissionError as e:
                errors.append(f"Cannot list directory contents: {directory} - {str(e)}")
                return False, errors

            # Check a subdirectory to ensure traversal works
            try:
                subdirs = [p for p in path.iterdir() if p.is_dir()]
                if subdirs:
                    test_subdir = subdirs[0]
                    if os.access(test_subdir, os.R_OK | os.X_OK):
                        return True, errors
                    else:
                        errors.append(f"Cannot access subdirectories in: {directory}")
                        return False, errors
            except Exception as e:
                errors.append(
                    f"Error checking subdirectory access: {directory} - {str(e)}"
                )
                return False, errors

            return True, errors

        except Exception as e:
            errors.append(
                f"Unexpected error validating directory {directory}: {str(e)}"
            )
            return False, errors


class ParallelScanner:
    """Parallel directory scanner with performance optimization"""

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self.audio_extensions = {
            ".flac",
            ".mp3",
            ".wav",
            ".aac",
            ".m4a",
            ".ogg",
            ".wma",
            ".alac",
            ".aiff",
            ".aif",
            ".dsd",
            ".dsf",
            ".dff",
        }

    async def scan_with_progress(
        self, directory: str, progress_callback=None
    ) -> ScanResult:
        """Scan directory with progress reporting"""
        start_time = time.time()
        errors = []
        files_found = 0
        folders_found = 0

        try:
            path = Path(directory)

            # Use ThreadPoolExecutor for parallel file processing
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Collect all files and directories
                all_items = list(path.rglob("*"))
                total_items = len(all_items)

                # Process items in batches
                batch_size = 100
                processed = 0

                for i in range(0, total_items, batch_size):
                    batch = all_items[i : i + batch_size]

                    # Process batch in parallel
                    futures = []
                    for item in batch:
                        future = executor.submit(self._process_item, item)
                        futures.append((future, item))

                    # Collect results
                    for future, item in futures:
                        try:
                            is_audio, is_dir = future.result(timeout=5)
                            if is_dir:
                                folders_found += 1
                            elif is_audio:
                                files_found += 1
                        except Exception as e:
                            errors.append(f"Error processing {item}: {str(e)}")

                    processed += len(batch)

                    # Report progress
                    if progress_callback:
                        progress = (processed / total_items) * 100
                        progress_callback(directory, progress, processed, total_items)

            scan_time = time.time() - start_time

            return ScanResult(
                directory=directory,
                success=len(errors) == 0,
                files_found=files_found,
                folders_found=folders_found,
                errors=errors,
                scan_time=scan_time,
                permissions_ok=True,
            )

        except Exception as e:
            scan_time = time.time() - start_time
            errors.append(f"Failed to scan directory {directory}: {str(e)}")

            return ScanResult(
                directory=directory,
                success=False,
                files_found=0,
                folders_found=0,
                errors=errors,
                scan_time=scan_time,
                permissions_ok=False,
            )

    def _process_item(self, item: Path) -> tuple[bool, bool]:
        """Process a single file or directory"""
        try:
            if item.is_dir():
                return False, True
            elif item.is_file():
                is_audio = item.suffix.lower() in self.audio_extensions
                return is_audio, False
            else:
                return False, False
        except Exception:
            return False, False


class DirectoryCache:
    """Caches directory scan results to improve performance"""

    def __init__(self, cache_ttl: int = 3600):  # 1 hour TTL
        self.cache = {}
        self.cache_ttl = cache_ttl

    def get(self, directory: str) -> DirectoryStats | None:
        """Get cached directory stats"""
        cached = self.cache.get(directory)
        if cached and (time.time() - cached.last_scan_time) < self.cache_ttl:
            return cached
        return None

    def set(self, directory: str, stats: DirectoryStats):
        """Cache directory stats"""
        self.cache[directory] = stats

    def invalidate(self, directory: str):
        """Invalidate cache for specific directory"""
        self.cache.pop(directory, None)

    def clear(self):
        """Clear all cache"""
        self.cache.clear()


class DirectoryWatcher(FileSystemEventHandler):
    """Watches directory changes for automatic rescanning"""

    def __init__(self, directory: str, callback):
        self.directory = directory
        self.callback = callback
        self.debounce_timer = None
        self.debounce_delay = 5  # 5 seconds debounce

    def on_created(self, event):
        """Handle file/directory creation"""
        if not event.is_directory:
            self._schedule_rescan()

    def on_deleted(self, event):
        """Handle file/directory deletion"""
        self._schedule_rescan()

    def on_moved(self, event):
        """Handle file/directory moves"""
        self._schedule_rescan()

    def _schedule_rescan(self):
        """Schedule a rescan with debouncing"""
        if self.debounce_timer:
            self.debounce_timer.cancel()

        self.debounce_timer = threading.Timer(self.debounce_delay, self._trigger_rescan)
        self.debounce_timer.start()

    def _trigger_rescan(self):
        """Trigger the rescan callback"""
        try:
            self.callback(self.directory)
        except Exception as e:
            logger.error(f"Error in directory watcher callback: {e}")


class EnhancedDirectoryScanner:
    """Enhanced directory scanner with multiple improvements"""

    def __init__(self, max_workers: int = 4):
        self.permission_validator = PermissionValidator()
        self.parallel_scanner = ParallelScanner(max_workers)
        self.cache = DirectoryCache()
        self.watchers = {}  # directory -> observer
        self.scan_history = {}

    async def scan_multiple_directories(
        self, directories: list[str], progress_callback=None
    ) -> dict[str, ScanResult]:
        """Efficiently scan multiple music directories in parallel"""
        logger.info(f"Starting scan of {len(directories)} directories")

        # Validate permissions first
        validation_tasks = []
        for directory in directories:
            task = self.permission_validator.validate_directory(directory)
            validation_tasks.append((directory, task))

        # Collect validation results
        valid_directories = []
        validation_results = {}

        for directory, task in validation_tasks:
            permissions_ok, errors = await task
            validation_results[directory] = (permissions_ok, errors)

            if permissions_ok:
                valid_directories.append(directory)
            else:
                logger.error(f"Directory validation failed for {directory}: {errors}")

        # Scan valid directories in parallel
        scan_tasks = []
        for directory in valid_directories:
            task = self.parallel_scanner.scan_with_progress(
                directory, progress_callback
            )
            scan_tasks.append((directory, task))

        # Collect scan results
        results = {}
        for directory, task in scan_tasks:
            result = await task
            results[directory] = result

            # Cache successful results
            if result.success:
                stats = DirectoryStats(
                    total_files=result.files_found + result.folders_found,
                    audio_files=result.files_found,
                    total_size=0,  # Would need additional calculation
                    last_scan_time=time.time(),
                    scan_duration=result.scan_time,
                    errors=result.errors,
                )
                self.cache.set(directory, stats)

            # Store in history
            self.scan_history[directory] = {"last_scan": time.time(), "result": result}

        # Add validation failures to results
        for directory, (permissions_ok, errors) in validation_results.items():
            if not permissions_ok:
                results[directory] = ScanResult(
                    directory=directory,
                    success=False,
                    files_found=0,
                    folders_found=0,
                    errors=errors,
                    scan_time=0,
                    permissions_ok=False,
                )

        logger.info(f"Completed scan of {len(results)} directories")
        return results

    async def scan_directory_async(
        self, directory: str, progress_callback=None
    ) -> ScanResult:
        """Async directory scanning with progress tracking"""
        # Check cache first
        cached_stats = self.cache.get(directory)
        if cached_stats:
            logger.info(f"Using cached results for {directory}")
            return ScanResult(
                directory=directory,
                success=True,
                files_found=cached_stats.audio_files,
                folders_found=cached_stats.total_files - cached_stats.audio_files,
                errors=cached_stats.errors,
                scan_time=cached_stats.scan_duration,
                permissions_ok=True,
            )

        # Validate permissions
        permissions_ok, errors = await self.permission_validator.validate_directory(
            directory
        )
        if not permissions_ok:
            return ScanResult(
                directory=directory,
                success=False,
                files_found=0,
                folders_found=0,
                errors=errors,
                scan_time=0,
                permissions_ok=False,
            )

        # Perform scan
        result = await self.parallel_scanner.scan_with_progress(
            directory, progress_callback
        )

        # Cache successful results
        if result.success:
            stats = DirectoryStats(
                total_files=result.files_found + result.folders_found,
                audio_files=result.files_found,
                total_size=0,
                last_scan_time=time.time(),
                scan_duration=result.scan_time,
                errors=result.errors,
            )
            self.cache.set(directory, stats)

        return result

    def start_watching(self, directory: str, callback):
        """Start watching a directory for changes"""
        if directory in self.watchers:
            return  # Already watching

        try:
            observer = Observer()
            handler = DirectoryWatcher(directory, callback)
            observer.schedule(handler, directory, recursive=True)
            observer.start()
            self.watchers[directory] = observer
            logger.info(f"Started watching directory: {directory}")
        except Exception as e:
            logger.error(f"Failed to start watching {directory}: {e}")

    def stop_watching(self, directory: str):
        """Stop watching a directory"""
        if directory in self.watchers:
            observer = self.watchers.pop(directory)
            observer.stop()
            observer.join()
            logger.info(f"Stopped watching directory: {directory}")

    def stop_all_watching(self):
        """Stop watching all directories"""
        for directory in list(self.watchers.keys()):
            self.stop_watching(directory)

    def get_scan_stats(self) -> dict[str, Any]:
        """Get scanning statistics"""
        return {
            "cached_directories": len(self.cache.cache),
            "watched_directories": len(self.watchers),
            "scan_history": len(self.scan_history),
            "last_scans": {
                directory: history["last_scan"]
                for directory, history in self.scan_history.items()
            },
        }


# Global enhanced directory scanner instance
enhanced_directory_scanner = EnhancedDirectoryScanner()
