from __future__ import annotations

import secrets
import time
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    and_,
    delete,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column

from swingmusic.db import Base


class LibraryFileTable(Base):
    __tablename__ = "library_file"

    id: Mapped[int] = mapped_column(primary_key=True)
    trackhash: Mapped[str] = mapped_column(String(), unique=True, index=True)
    filepath: Mapped[str] = mapped_column(String(), unique=True, index=True)
    codec: Mapped[str] = mapped_column(String(), default="unknown")
    quality: Mapped[str] = mapped_column(String(), default="unknown")
    bitrate: Mapped[int] = mapped_column(Integer(), default=0)
    source: Mapped[str] = mapped_column(String(), default="local")
    checksum: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)

    @classmethod
    def get_by_trackhash(cls, trackhash: str):
        result = cls.execute(select(cls).where(cls.trackhash == trackhash))
        return next(result).scalar()

    @classmethod
    def upsert_from_local_track(
        cls,
        *,
        trackhash: str,
        filepath: str,
        bitrate: int,
        codec: str,
        quality: str,
        source: str = "local",
    ):
        now = int(time.time())
        row = cls.get_by_trackhash(trackhash)

        if row:
            next(
                cls.execute(
                    update(cls)
                    .where(cls.id == row.id)
                    .values(
                        filepath=filepath,
                        bitrate=bitrate,
                        codec=codec,
                        quality=quality,
                        source=source,
                        updated_at=now,
                    ),
                    commit=True,
                )
            )
            return cls.get_by_trackhash(trackhash)

        cls.insert_one(
            {
                "trackhash": trackhash,
                "filepath": filepath,
                "bitrate": bitrate,
                "codec": codec,
                "quality": quality,
                "source": source,
                "created_at": now,
                "updated_at": now,
                "extra": {},
            }
        )
        return cls.get_by_trackhash(trackhash)


class DownloadJobTable(Base):
    __tablename__ = "download_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    userid: Mapped[int] = mapped_column(
        Integer(), ForeignKey("user.id", ondelete="cascade"), index=True
    )
    trackhash: Mapped[str | None] = mapped_column(
        String(), nullable=True, index=True, default=None
    )
    title: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    artist: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    album: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    item_type: Mapped[str] = mapped_column(String(), default="track")
    source_url: Mapped[str | None] = mapped_column(
        String(), nullable=True, index=True, default=None
    )
    source: Mapped[str] = mapped_column(String(), default="spotify", index=True)
    provider: Mapped[str] = mapped_column(String(), default="spotify")
    codec: Mapped[str] = mapped_column(String(), default="mp3")
    quality: Mapped[str] = mapped_column(String(), default="high")
    target_path: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    state: Mapped[str] = mapped_column(String(), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float(), default=0.0)
    error: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    retry_count: Mapped[int] = mapped_column(Integer(), default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    started_at: Mapped[int | None] = mapped_column(
        Integer(), nullable=True, default=None
    )
    finished_at: Mapped[int | None] = mapped_column(
        Integer(), nullable=True, default=None
    )

    @classmethod
    def enqueue(cls, payload: dict[str, Any]):
        now = int(time.time())
        values = {
            "created_at": now,
            "updated_at": now,
            "state": "queued",
            "progress": 0.0,
            **payload,
        }
        result = cls.insert_one(values)
        return result.lastrowid

    @classmethod
    def get_by_id(cls, job_id: int):
        result = cls.execute(select(cls).where(cls.id == job_id))
        return next(result).scalar()

    @classmethod
    def get_queued_job(cls):
        result = cls.execute(
            select(cls)
            .where(cls.state == "queued")
            .order_by(cls.created_at.asc())
            .limit(1)
        )
        return next(result).scalar()

    @classmethod
    def update_job(cls, job_id: int, values: dict[str, Any]):
        values = {**values, "updated_at": int(time.time())}
        return next(
            cls.execute(update(cls).where(cls.id == job_id).values(values), commit=True)
        )

    @classmethod
    def list_for_user(cls, userid: int, states: list[str] | set[str] | None = None):
        query = select(cls).where(cls.userid == userid).order_by(cls.created_at.desc())
        if states:
            query = query.where(cls.state.in_(list(states)))

        result = cls.execute(query)
        return list(next(result).scalars())

    @classmethod
    def delete_for_user(
        cls, userid: int, states: list[str] | set[str] | None = None
    ) -> int:
        statement = delete(cls).where(cls.userid == userid)
        if states:
            statement = statement.where(cls.state.in_(list(states)))

        result = next(cls.execute(statement, commit=True))
        return int(result.rowcount or 0)


class TrackedPlaylistTable(Base):
    __tablename__ = "tracked_playlist"
    __table_args__ = (
        UniqueConstraint(
            "userid",
            "service",
            "playlist_id",
            name="uq_tracked_playlist_user_service",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    userid: Mapped[int] = mapped_column(
        Integer(), ForeignKey("user.id", ondelete="cascade"), index=True
    )
    source_url: Mapped[str] = mapped_column(String(), index=True)
    playlist_id: Mapped[str] = mapped_column(String(), index=True)
    service: Mapped[str] = mapped_column(String(), default="spotify", index=True)
    title: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    owner_name: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    quality: Mapped[str] = mapped_column(String(), default="lossless")
    codec: Mapped[str] = mapped_column(String(), default="flac")
    auto_sync: Mapped[bool] = mapped_column(Boolean(), default=True, index=True)
    sync_interval_seconds: Mapped[int] = mapped_column(Integer(), default=900)
    next_sync_at: Mapped[int] = mapped_column(
        Integer(), default=lambda: int(time.time())
    )
    last_sync_at: Mapped[int | None] = mapped_column(
        Integer(), nullable=True, default=None
    )
    status: Mapped[str] = mapped_column(String(), default="active", index=True)
    snapshot_track_ids: Mapped[list[str]] = mapped_column(JSON(), default_factory=list)
    snapshot_hash: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    last_result: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)
    last_error: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)

    @classmethod
    def get_by_id(cls, tracked_id: int, userid: int | None = None):
        statement = select(cls).where(cls.id == tracked_id)
        if userid is not None:
            statement = statement.where(cls.userid == userid)
        result = cls.execute(statement)
        return next(result).scalar()

    @classmethod
    def get_by_source(cls, *, userid: int, service: str, playlist_id: str):
        result = cls.execute(
            select(cls).where(
                and_(
                    cls.userid == userid,
                    cls.service == service,
                    cls.playlist_id == playlist_id,
                )
            )
        )
        return next(result).scalar()

    @classmethod
    def list_for_user(cls, userid: int, include_deleted: bool = False):
        statement = (
            select(cls).where(cls.userid == userid).order_by(cls.created_at.desc())
        )
        if not include_deleted:
            statement = statement.where(cls.status != "deleted")
        result = cls.execute(statement)
        return list(next(result).scalars())

    @classmethod
    def upsert(
        cls,
        *,
        userid: int,
        service: str,
        playlist_id: str,
        source_url: str,
        values: dict[str, Any] | None = None,
    ):
        now = int(time.time())
        row = cls.get_by_source(userid=userid, service=service, playlist_id=playlist_id)
        payload: dict[str, Any] = {
            "userid": userid,
            "service": service,
            "playlist_id": playlist_id,
            "source_url": source_url,
        }
        if values:
            payload.update(values)

        if row:
            next(
                cls.execute(
                    update(cls)
                    .where(cls.id == row.id)
                    .values(
                        {
                            **payload,
                            "updated_at": now,
                        }
                    ),
                    commit=True,
                )
            )
            return cls.get_by_id(row.id)

        cls.insert_one(
            {
                **payload,
                "status": payload.get("status", "active"),
                "auto_sync": bool(payload.get("auto_sync", True)),
                "sync_interval_seconds": int(payload.get("sync_interval_seconds", 900)),
                "next_sync_at": int(payload.get("next_sync_at", now)),
                "created_at": now,
                "updated_at": now,
                "snapshot_track_ids": payload.get("snapshot_track_ids", []),
                "last_result": payload.get("last_result", {}),
                "extra": payload.get("extra", {}),
            }
        )
        return cls.get_by_source(
            userid=userid, service=service, playlist_id=playlist_id
        )

    @classmethod
    def update_row(cls, tracked_id: int, values: dict[str, Any]):
        next(
            cls.execute(
                update(cls)
                .where(cls.id == tracked_id)
                .values({**values, "updated_at": int(time.time())}),
                commit=True,
            )
        )
        return cls.get_by_id(tracked_id)

    @classmethod
    def due_for_sync(cls, *, now_ts: int | None = None, limit: int = 50):
        now_ts = int(now_ts or time.time())
        result = cls.execute(
            select(cls)
            .where(cls.auto_sync.is_(True))
            .where(cls.status.in_(["active", "failed", "syncing"]))
            .where(cls.next_sync_at <= now_ts)
            .order_by(cls.next_sync_at.asc())
            .limit(limit)
        )
        return list(next(result).scalars())


class UserLibraryTrackTable(Base):
    __tablename__ = "user_library_track"
    __table_args__ = (
        UniqueConstraint("userid", "trackhash", name="uq_user_track_projection"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    userid: Mapped[int] = mapped_column(
        Integer(), ForeignKey("user.id", ondelete="cascade"), index=True
    )
    trackhash: Mapped[str] = mapped_column(String(), index=True)
    file_id: Mapped[int] = mapped_column(
        Integer(),
        ForeignKey("library_file.id", ondelete="set null"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(), default="missing", index=True)
    source_url: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    download_job_id: Mapped[int | None] = mapped_column(
        Integer(),
        ForeignKey("download_job.id", ondelete="set null"),
        nullable=True,
        default=None,
    )
    error: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)

    @classmethod
    def get_user_track(cls, userid: int, trackhash: str):
        result = cls.execute(
            select(cls).where(and_(cls.userid == userid, cls.trackhash == trackhash))
        )
        return next(result).scalar()

    @classmethod
    def upsert_status(
        cls,
        *,
        userid: int,
        trackhash: str,
        status: str,
        file_id: int | None = None,
        download_job_id: int | None = None,
        source_url: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        now = int(time.time())
        row = cls.get_user_track(userid, trackhash)

        values: dict[str, Any] = {
            "status": status,
            "updated_at": now,
            "file_id": file_id,
            "download_job_id": download_job_id,
            "source_url": source_url,
            "error": error,
        }

        if extra is not None:
            values["extra"] = extra

        if row:
            next(
                cls.execute(
                    update(cls).where(cls.id == row.id).values(values), commit=True
                )
            )
            return cls.get_user_track(userid, trackhash)

        cls.insert_one(
            {
                "userid": userid,
                "trackhash": trackhash,
                "status": status,
                "file_id": file_id,
                "download_job_id": download_job_id,
                "source_url": source_url,
                "error": error,
                "created_at": now,
                "updated_at": now,
                "extra": extra or {},
            }
        )
        return cls.get_user_track(userid, trackhash)

    @classmethod
    def get_status_map(cls, userid: int, trackhashes: set[str] | list[str]):
        if not trackhashes:
            return {}

        result = cls.execute(
            select(cls).where(
                and_(cls.userid == userid, cls.trackhash.in_(set(trackhashes)))
            )
        )
        rows = list(next(result).scalars())
        return {row.trackhash: row for row in rows}


class UserRootDirOwnershipTable(Base):
    __tablename__ = "user_root_dir_ownership"
    __table_args__ = (UniqueConstraint("userid", "path", name="uq_user_root_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    userid: Mapped[int] = mapped_column(
        Integer(), ForeignKey("user.id", ondelete="cascade"), index=True
    )
    path: Mapped[str] = mapped_column(String(), index=True)
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))

    @classmethod
    def assign_paths(cls, userid: int, paths: list[str]):
        existing_result = cls.execute(select(cls.path).where(cls.userid == userid))
        existing = {row[0] for row in next(existing_result).all()}

        for path in paths:
            if path in existing:
                continue
            cls.insert_one(
                {"userid": userid, "path": path, "created_at": int(time.time())}
            )

    @classmethod
    def get_paths(cls, userid: int) -> list[str]:
        result = cls.execute(select(cls.path).where(cls.userid == userid))
        paths = [row for row in next(result).scalars().all() if row]
        return list(dict.fromkeys(paths))

    @classmethod
    def replace_paths(cls, userid: int, paths: list[str]):
        cleaned = [path.strip() for path in paths if path and path.strip()]
        cleaned = list(dict.fromkeys(cleaned))

        next(cls.execute(delete(cls).where(cls.userid == userid), commit=True))
        if not cleaned:
            return

        now = int(time.time())
        cls.insert_many(
            [{"userid": userid, "path": path, "created_at": now} for path in cleaned]
        )


class SetupStateTable(Base):
    __tablename__ = "setup_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_userid: Mapped[int | None] = mapped_column(
        Integer(),
        ForeignKey("user.id", ondelete="set null"),
        nullable=True,
        default=None,
    )
    primary_music_dir: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    setup_completed: Mapped[bool] = mapped_column(Boolean(), default=False)
    index_state: Mapped[str] = mapped_column(String(), default="idle")
    index_progress: Mapped[float] = mapped_column(Float(), default=0.0)
    index_message: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)

    @classmethod
    def get_singleton(cls):
        result = cls.execute(select(cls).where(cls.id == 1))
        return next(result).scalar()

    @classmethod
    def ensure_singleton(cls):
        row = cls.get_singleton()
        if row:
            return row

        cls.insert_one(
            {
                "id": 1,
                "setup_completed": False,
                "index_state": "idle",
                "index_progress": 0.0,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
                "extra": {},
            }
        )
        return cls.get_singleton()

    @classmethod
    def update_state(cls, values: dict[str, Any]):
        cls.ensure_singleton()
        next(
            cls.execute(
                update(cls)
                .where(cls.id == 1)
                .values(
                    {
                        **values,
                        "updated_at": int(time.time()),
                    }
                ),
                commit=True,
            )
        )
        return cls.get_singleton()

    @classmethod
    def mark_index_progress(
        cls,
        *,
        state: str,
        progress: float,
        message: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        values: dict[str, Any] = {
            "index_state": state,
            "index_progress": max(0.0, min(float(progress), 100.0)),
            "index_message": message,
        }
        if extra is not None:
            values["extra"] = extra
        return cls.update_state(values)


class LyricsStatusTable(Base):
    __tablename__ = "lyrics_status"

    id: Mapped[int] = mapped_column(primary_key=True)
    trackhash: Mapped[str] = mapped_column(String(), unique=True, index=True)
    filepath: Mapped[str | None] = mapped_column(
        String(), nullable=True, index=True, default=None
    )
    status: Mapped[str] = mapped_column(String(), default="pending", index=True)
    source: Mapped[str | None] = mapped_column(String(), nullable=True, default=None)
    has_embedded: Mapped[bool] = mapped_column(Boolean(), default=False)
    has_lrc: Mapped[bool] = mapped_column(Boolean(), default=False)
    last_error: Mapped[str | None] = mapped_column(
        String(), nullable=True, default=None
    )
    attempts: Mapped[int] = mapped_column(Integer(), default=0)
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    updated_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)

    @classmethod
    def get_by_trackhash(cls, trackhash: str):
        result = cls.execute(select(cls).where(cls.trackhash == trackhash))
        return next(result).scalar()

    @classmethod
    def upsert(
        cls,
        *,
        trackhash: str,
        filepath: str | None = None,
        status: str,
        source: str | None = None,
        has_embedded: bool | None = None,
        has_lrc: bool | None = None,
        last_error: str | None = None,
        extra: dict[str, Any] | None = None,
        increment_attempt: bool = False,
    ):
        now = int(time.time())
        row = cls.get_by_trackhash(trackhash)
        values: dict[str, Any] = {
            "status": status,
            "source": source,
            "last_error": last_error,
            "updated_at": now,
        }

        if filepath is not None:
            values["filepath"] = filepath
        if has_embedded is not None:
            values["has_embedded"] = bool(has_embedded)
        if has_lrc is not None:
            values["has_lrc"] = bool(has_lrc)
        if extra is not None:
            values["extra"] = extra

        if row:
            if increment_attempt:
                values["attempts"] = int(row.attempts or 0) + 1
            next(
                cls.execute(
                    update(cls).where(cls.id == row.id).values(values), commit=True
                )
            )
            return cls.get_by_trackhash(trackhash)

        cls.insert_one(
            {
                "trackhash": trackhash,
                "filepath": filepath,
                "status": status,
                "source": source,
                "has_embedded": bool(has_embedded)
                if has_embedded is not None
                else False,
                "has_lrc": bool(has_lrc) if has_lrc is not None else False,
                "last_error": last_error,
                "attempts": 1 if increment_attempt else 0,
                "created_at": now,
                "updated_at": now,
                "extra": extra or {},
            }
        )
        return cls.get_by_trackhash(trackhash)


class InviteTokenTable(Base):
    __tablename__ = "invite_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(), unique=True, index=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer(),
        ForeignKey("user.id", ondelete="set null"),
        nullable=True,
        default=None,
    )
    used_by: Mapped[int | None] = mapped_column(
        Integer(),
        ForeignKey("user.id", ondelete="set null"),
        nullable=True,
        default=None,
    )
    roles: Mapped[list[str]] = mapped_column(JSON(), default_factory=lambda: ["user"])
    active: Mapped[bool] = mapped_column(Boolean(), default=True)
    expires_at: Mapped[int | None] = mapped_column(
        Integer(), nullable=True, default=None
    )
    created_at: Mapped[int] = mapped_column(Integer(), default=lambda: int(time.time()))
    used_at: Mapped[int | None] = mapped_column(Integer(), nullable=True, default=None)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON(), default_factory=dict)

    @classmethod
    def create_token(
        cls,
        *,
        created_by: int | None,
        roles: list[str] | None = None,
        expires_in_seconds: int = 7 * 24 * 3600,
        extra: dict[str, Any] | None = None,
    ):
        token = secrets.token_urlsafe(24)
        now = int(time.time())
        expires_at = now + expires_in_seconds if expires_in_seconds > 0 else None

        cls.insert_one(
            {
                "token": token,
                "created_by": created_by,
                "roles": roles or ["user"],
                "active": True,
                "expires_at": expires_at,
                "created_at": now,
                "extra": extra or {},
            }
        )

        result = cls.execute(select(cls).where(cls.token == token))
        return next(result).scalar()

    @classmethod
    def get_valid_token(cls, token: str):
        now = int(time.time())
        result = cls.execute(select(cls).where(cls.token == token))
        row = next(result).scalar()

        if not row or not row.active:
            return None

        if row.expires_at is not None and row.expires_at < now:
            cls.consume_token(token, used_by=None, deactivate_only=True)
            return None

        return row

    @classmethod
    def consume_token(
        cls, token: str, used_by: int | None, deactivate_only: bool = False
    ):
        values: dict[str, Any] = {"active": False, "used_at": int(time.time())}
        if not deactivate_only:
            values["used_by"] = used_by

        next(
            cls.execute(
                update(cls).where(cls.token == token).values(values), commit=True
            )
        )
