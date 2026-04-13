"""Recap generation and persistence using existing scrobble + in-memory stores."""

from __future__ import annotations

import calendar
import datetime as dt
import json
import secrets
from collections import defaultdict
from typing import Any

from sqlalchemy import text

from swingmusic.db.engine import DbEngine
from swingmusic.db.userdata import ScrobbleTable
from swingmusic.utils.stats import (
    get_albums_in_period,
    get_artists_in_period,
    get_tracks_in_period,
)


class RecapStore:
    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self):
        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS recap_cache (
                        user_id INTEGER NOT NULL,
                        year INTEGER NOT NULL,
                        recap_json TEXT NOT NULL,
                        generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, year)
                    )
                    """
                )
            )
            session.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS recap_shares (
                        token TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        year INTEGER NOT NULL,
                        recap_json TEXT NOT NULL,
                        include_personal_data INTEGER NOT NULL DEFAULT 0,
                        expires_at TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )

    @staticmethod
    def _year_bounds(year: int) -> tuple[int, int]:
        start = int(dt.datetime(year, 1, 1, tzinfo=dt.UTC).timestamp())
        end = int(dt.datetime(year + 1, 1, 1, tzinfo=dt.UTC).timestamp()) - 1
        return start, end

    @staticmethod
    def _minutes(seconds: float | int) -> int:
        return int(round(float(seconds or 0) / 60.0))

    @staticmethod
    def _compute_streak(day_values: set[dt.date]) -> int:
        if not day_values:
            return 0

        days = sorted(day_values)
        best = 1
        current = 1

        for prev, curr in zip(days, days[1:], strict=False):
            if (curr - prev).days == 1:
                current += 1
                best = max(best, current)
            else:
                current = 1

        return best

    @staticmethod
    def _build_personality(
        total_tracks: int, unique_tracks: int, top_artists: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if total_tracks <= 0:
            return {
                "personality_type": "Balanced",
                "description": "You kept a steady listening rhythm this year.",
                "traits": ["Steady", "Curious", "Open-minded"],
            }

        diversity = unique_tracks / max(total_tracks, 1)
        top_artist_share = 0.0
        if top_artists:
            top_artist_share = float(top_artists[0].get("play_count") or 0) / max(
                total_tracks, 1
            )

        if diversity >= 0.72:
            return {
                "personality_type": "Explorer",
                "description": "You explored a wide range of music and discovered new sounds often.",
                "traits": ["Curious", "Adventurous", "Varied taste"],
            }

        if top_artist_share >= 0.35:
            return {
                "personality_type": "Loyalist",
                "description": "You go deep on your favorite artists and keep strong repeat favorites.",
                "traits": ["Focused", "Dedicated", "Consistent"],
            }

        return {
            "personality_type": "Balanced",
            "description": "You balance comfort favorites with enough variety to keep it fresh.",
            "traits": ["Versatile", "Balanced", "Mood-driven"],
        }

    @staticmethod
    def _build_milestones(
        total_minutes: int, total_tracks: int, unique_tracks: int
    ) -> list[dict[str, Any]]:
        milestones: list[dict[str, Any]] = []

        def add_minutes(level: str, threshold: int):
            milestones.append(
                {
                    "type": "listening_time",
                    "icon": "clock",
                    "title": "Listening Time",
                    "description": f"Reached {threshold:,} minutes listened",
                    "level": level,
                }
            )

        if total_minutes >= 20000:
            add_minutes("gold", 20000)
        elif total_minutes >= 8000:
            add_minutes("silver", 8000)
        elif total_minutes >= 2000:
            add_minutes("bronze", 2000)

        if total_tracks >= 5000:
            milestones.append(
                {
                    "type": "plays",
                    "icon": "play",
                    "title": "Heavy Rotation",
                    "description": "Played over 5,000 tracks this year",
                    "level": "gold",
                }
            )
        elif total_tracks >= 1500:
            milestones.append(
                {
                    "type": "plays",
                    "icon": "play",
                    "title": "Regular Listener",
                    "description": "Played over 1,500 tracks this year",
                    "level": "silver",
                }
            )

        if unique_tracks >= 1000:
            milestones.append(
                {
                    "type": "discovery",
                    "icon": "compass",
                    "title": "Discovery Mode",
                    "description": "Listened to more than 1,000 unique tracks",
                    "level": "gold",
                }
            )
        elif unique_tracks >= 400:
            milestones.append(
                {
                    "type": "discovery",
                    "icon": "compass",
                    "title": "Explorer",
                    "description": "Listened to more than 400 unique tracks",
                    "level": "silver",
                }
            )

        return milestones

    def _get_cached_recap(self, user_id: int, year: int) -> dict[str, Any] | None:
        with DbEngine.manager() as session:
            row = (
                session.execute(
                    text(
                        """
                    SELECT recap_json
                    FROM recap_cache
                    WHERE user_id = :user_id
                      AND year = :year
                    """
                    ),
                    {"user_id": int(user_id), "year": int(year)},
                )
                .mappings()
                .first()
            )

        if not row:
            return None

        try:
            recap = json.loads(row["recap_json"])
            return recap if isinstance(recap, dict) else None
        except json.JSONDecodeError:
            return None

    def _save_recap(self, user_id: int, year: int, recap: dict[str, Any]):
        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    INSERT INTO recap_cache (user_id, year, recap_json, generated_at)
                    VALUES (:user_id, :year, :recap_json, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, year) DO UPDATE SET
                        recap_json = excluded.recap_json,
                        generated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "user_id": int(user_id),
                    "year": int(year),
                    "recap_json": json.dumps(recap),
                },
            )

    def get_available_years(self, user_id: int) -> list[int]:
        years: set[int] = set()
        try:
            with DbEngine.manager() as session:
                scrobble_rows = session.execute(
                    text(
                        """
                        SELECT DISTINCT CAST(strftime('%Y', datetime(timestamp, 'unixepoch')) AS INTEGER) AS y
                        FROM scrobble
                        WHERE userid = :user_id
                        """
                    ),
                    {"user_id": int(user_id)},
                )

                for row in scrobble_rows:
                    y = row[0]
                    if y:
                        years.add(int(y))

                cache_rows = session.execute(
                    text(
                        """
                        SELECT DISTINCT year
                        FROM recap_cache
                        WHERE user_id = :user_id
                        """
                    ),
                    {"user_id": int(user_id)},
                )
                for row in cache_rows:
                    years.add(int(row[0]))
        except Exception:
            return []

        return sorted(years, reverse=True)

    def generate_recap(self, user_id: int, year: int) -> dict[str, Any] | None:
        try:
            start_ts, end_ts = self._year_bounds(int(year))
            scrobbles = list(
                ScrobbleTable.get_all_in_period(start_ts, end_ts, int(user_id))
            )

            if not scrobbles:
                return None

            tracks, total_tracks, total_duration = get_tracks_in_period(
                start_ts, end_ts, int(user_id)
            )
            tracks = sorted(
                tracks,
                key=lambda t: int(getattr(t, "playduration", 0) or 0),
                reverse=True,
            )

            top_tracks = []
            for track in tracks[:50]:
                artists = getattr(track, "artists", []) or []
                artist_name = (
                    artists[0].get("name")
                    if artists and isinstance(artists[0], dict)
                    else "Unknown Artist"
                )
                top_tracks.append(
                    {
                        "id": track.trackhash,
                        "title": track.title,
                        "artist": artist_name,
                        "album": track.album,
                        "image": track.image,
                        "play_count": int(track.playcount or 0),
                        "total_duration": self._minutes(track.playduration),
                    }
                )

            artist_entries = get_artists_in_period(start_ts, end_ts, int(user_id))
            top_artists = []
            for item in artist_entries[:50]:
                top_artists.append(
                    {
                        "name": item.get("artist", "Unknown Artist"),
                        "play_count": int(item.get("playcount", 0) or 0),
                        "total_duration": self._minutes(item.get("playduration", 0)),
                        "unique_tracks": len(item.get("tracks", {})),
                    }
                )

            albums = get_albums_in_period(start_ts, end_ts, int(user_id))
            albums = sorted(
                albums,
                key=lambda a: int(getattr(a, "playduration", 0) or 0),
                reverse=True,
            )
            top_albums = []
            for album in albums[:30]:
                album_artists = getattr(album, "albumartists", []) or []
                artist_name = (
                    album_artists[0].get("name")
                    if album_artists and isinstance(album_artists[0], dict)
                    else "Unknown Artist"
                )
                top_albums.append(
                    {
                        "name": album.title,
                        "artist": artist_name,
                        "play_count": int(album.playcount or 0),
                        "total_duration": self._minutes(album.playduration),
                        "image": album.image,
                    }
                )

            unique_trackhashes = {entry.trackhash for entry in scrobbles}
            day_values = {
                dt.datetime.fromtimestamp(int(entry.timestamp), tz=dt.UTC).date()
                for entry in scrobbles
            }

            monthly_seconds = defaultdict(int)
            for entry in scrobbles:
                month = dt.datetime.fromtimestamp(int(entry.timestamp), tz=dt.UTC).month
                monthly_seconds[month] += int(entry.duration or 0)

            monthly_breakdown = []
            for month in range(1, 13):
                monthly_breakdown.append(
                    {
                        "month": month,
                        "month_name": calendar.month_name[month],
                        "total_minutes": self._minutes(monthly_seconds[month]),
                    }
                )

            total_minutes = self._minutes(total_duration)
            unique_tracks = len(unique_trackhashes)

            recap = {
                "year": int(year),
                "generated_at": dt.datetime.now(dt.UTC).isoformat(),
                "stats": {
                    "total_minutes": total_minutes,
                    "total_tracks": int(total_tracks),
                    "unique_tracks": unique_tracks,
                    "unique_artists": len({item.get("name") for item in top_artists}),
                    "listening_streak": self._compute_streak(day_values),
                },
                "personality": self._build_personality(
                    int(total_tracks), unique_tracks, top_artists
                ),
                "top_tracks": top_tracks,
                "top_artists": top_artists,
                "top_albums": top_albums,
                "monthly_breakdown": monthly_breakdown,
                "milestones": self._build_milestones(
                    total_minutes, int(total_tracks), unique_tracks
                ),
                "discoveries": {
                    "new_artists": max(0, len(top_artists) - 10),
                    "new_tracks": max(0, unique_tracks - 100),
                },
            }

            self._save_recap(user_id, year, recap)
            return recap
        except Exception:
            return None

    def get_recap(
        self, user_id: int, year: int, generate_if_missing: bool = False
    ) -> dict[str, Any] | None:
        recap = self._get_cached_recap(user_id, year)
        if recap:
            return recap

        if generate_if_missing:
            return self.generate_recap(user_id, year)

        return None

    def get_summary(self, user_id: int, year: int) -> dict[str, Any] | None:
        recap = self.get_recap(user_id, year, generate_if_missing=False)
        if not recap:
            return None

        return {
            "year": recap.get("year", year),
            "stats": recap.get("stats", {}),
            "personality": recap.get("personality", {}),
            "milestones": recap.get("milestones", []),
        }

    def create_share_link(
        self,
        user_id: int,
        year: int,
        include_personal_data: bool,
        expires_in_days: int,
    ) -> dict[str, Any] | None:
        recap = self.get_recap(user_id, year, generate_if_missing=True)
        if not recap:
            return None

        payload = recap
        if not include_personal_data:
            payload = {
                **recap,
                "top_tracks": [
                    {
                        **item,
                        "title": "Hidden",
                        "artist": "Hidden",
                        "album": "Hidden",
                    }
                    for item in recap.get("top_tracks", [])
                ],
            }

        token = secrets.token_urlsafe(24).replace("-", "").replace("_", "")[:32]
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(
            days=max(1, min(3650, int(expires_in_days)))
        )

        with DbEngine.manager(commit=True) as session:
            session.execute(
                text(
                    """
                    INSERT INTO recap_shares (
                        token,
                        user_id,
                        year,
                        recap_json,
                        include_personal_data,
                        expires_at,
                        created_at
                    )
                    VALUES (
                        :token,
                        :user_id,
                        :year,
                        :recap_json,
                        :include_personal_data,
                        :expires_at,
                        CURRENT_TIMESTAMP
                    )
                    """
                ),
                {
                    "token": token,
                    "user_id": int(user_id),
                    "year": int(year),
                    "recap_json": json.dumps(payload),
                    "include_personal_data": 1 if include_personal_data else 0,
                    "expires_at": expires_at.isoformat(),
                },
            )

        return {
            "share_token": token,
            "year": int(year),
            "expires_at": expires_at.isoformat(),
            "include_personal_data": bool(include_personal_data),
        }

    def get_shared_recap(self, token: str) -> dict[str, Any] | None:
        with DbEngine.manager() as session:
            row = (
                session.execute(
                    text(
                        """
                    SELECT year, recap_json, expires_at
                    FROM recap_shares
                    WHERE token = :token
                    """
                    ),
                    {"token": str(token)},
                )
                .mappings()
                .first()
            )

        if not row:
            return None

        try:
            expires_at = dt.datetime.fromisoformat(row["expires_at"])
        except Exception:
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt.UTC)

        if expires_at < dt.datetime.now(dt.UTC):
            return None

        try:
            recap = json.loads(row["recap_json"])
        except json.JSONDecodeError:
            return None

        return {
            "year": int(row["year"]),
            "recap": recap,
            "expires_at": row["expires_at"],
        }

    def compare_years(
        self, user_id: int, year1: int, year2: int
    ) -> dict[str, Any] | None:
        recap1 = self.get_recap(user_id, year1, generate_if_missing=True)
        recap2 = self.get_recap(user_id, year2, generate_if_missing=True)

        if not recap1 or not recap2:
            return None

        stats1 = recap1.get("stats", {})
        stats2 = recap2.get("stats", {})

        minutes1 = int(stats1.get("total_minutes", 0) or 0)
        minutes2 = int(stats2.get("total_minutes", 0) or 0)

        tracks1 = int(stats1.get("total_tracks", 0) or 0)
        tracks2 = int(stats2.get("total_tracks", 0) or 0)

        def pct(old: int, new: int) -> float:
            base = max(abs(old), 1)
            return ((new - old) / base) * 100.0

        return {
            "year1": int(year1),
            "year2": int(year2),
            "listening_time_change": {
                "absolute": minutes2 - minutes1,
                "percentage": pct(minutes1, minutes2),
            },
            "tracks_change": {
                "absolute": tracks2 - tracks1,
                "percentage": pct(tracks1, tracks2),
            },
            "personality_change": {
                "from": recap1.get("personality", {}).get(
                    "personality_type", "Unknown"
                ),
                "to": recap2.get("personality", {}).get("personality_type", "Unknown"),
                "changed": recap1.get("personality", {}).get("personality_type")
                != recap2.get("personality", {}).get("personality_type"),
            },
        }


recap_store = RecapStore()
