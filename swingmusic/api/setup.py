from __future__ import annotations

import os
import pathlib
from pathlib import Path

import psutil
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from swingmusic.services.setup_state import (
    bootstrap_setup,
    configure_primary_directory,
    get_setup_status,
    trigger_initial_index,
)
from swingmusic.utils.wintools import is_windows

bp_tag = Tag(name="Setup", description="First-run setup and onboarding state")
api = APIBlueprint("setup", __name__, url_prefix="/setup", abp_tags=[bp_tag])


class SetupBootstrapBody(BaseModel):
    username: str = Field(description="Owner username for first boot")
    password: str = Field(description="Owner password for first boot")
    root_dirs: list[str] = Field(
        default_factory=list,
        description="Initial primary music directories",
    )


class SetupDirectoryBody(BaseModel):
    root_dirs: list[str] = Field(
        default_factory=list,
        description="Primary music directories to use for indexing",
    )


class SetupIndexStartBody(BaseModel):
    force: bool = Field(
        default=False,
        description="Force queueing a new initial index run",
    )


class SetupDirBrowserBody(BaseModel):
    folder: str = Field(
        "$root",
        description="The folder to list directories from during first-run setup",
    )


def _setup_root_drives(is_win: bool = False):
    drives = [Path(d.mountpoint).as_posix() for d in psutil.disk_partitions(all=True)]

    if is_win:
        return drives

    hidden_roots = (
        "/boot",
        "/tmp",
        "/snap",
        "/var",
        "/sys",
        "/proc",
        "/etc",
        "/run",
        "/dev",
    )
    return [drive for drive in drives if not drive.startswith(hidden_roots)]


@api.get("/status")
def setup_status():
    return get_setup_status()


@api.post("/bootstrap")
def setup_bootstrap(body: SetupBootstrapBody):
    try:
        owner = bootstrap_setup(
            username=body.username,
            password=body.password,
            root_dirs=body.root_dirs,
        )
        return {
            "success": True,
            "owner": {
                "id": owner.id,
                "username": owner.username,
            },
            "setup": get_setup_status(),
        }
    except ValueError as error:
        return {"success": False, "error": str(error)}, 400


@api.post("/directory")
def setup_directory(body: SetupDirectoryBody):
    status = get_setup_status()
    if status["setup_completed"]:
        return {
            "success": False,
            "error": "Setup is already completed.",
            "setup": status,
        }, 400

    if not status["owner_created"]:
        return {
            "success": False,
            "error": "Create the owner account before configuring directories.",
            "setup": status,
        }, 400

    try:
        queued = configure_primary_directory(root_dirs=body.root_dirs)
    except ValueError as error:
        return {"success": False, "error": str(error)}, 400

    return {
        "success": True,
        "queued": queued,
        "setup": get_setup_status(),
    }


@api.get("/index-progress")
def setup_index_progress():
    status = get_setup_status()
    return {
        "index_state": status["index_state"],
        "index_progress": status["index_progress"],
        "index_message": status["index_message"],
        "initial_index_completed": status["initial_index_completed"],
    }


@api.post("/index/start")
def setup_index_start(body: SetupIndexStartBody):
    status = get_setup_status()
    if not status["owner_created"] or not status["directory_configured"]:
        return {
            "queued": False,
            "error": "Owner account and primary music directory are required before indexing.",
            "setup": status,
        }, 400

    queued = trigger_initial_index(force=body.force)
    status = get_setup_status()
    return {
        "queued": queued,
        "setup": status,
    }


@api.post("/dir-browser")
def setup_dir_browser(body: SetupDirBrowserBody):
    status = get_setup_status()
    if status["setup_completed"]:
        return {"folders": [], "error": "Setup is already completed."}, 403

    req_dir = body.folder
    if req_dir == "$root":
        roots = _setup_root_drives(is_win=is_windows())
        if "/music" not in roots and Path("/music").exists():
            roots.insert(0, "/music")

        return {"folders": [{"name": root, "path": root} for root in roots]}

    req_path = pathlib.Path(req_dir).resolve()
    if not req_path.exists() or not req_path.is_dir():
        return {"folders": [], "error": "Invalid directory"}, 400

    dirs = []
    try:
        with os.scandir(req_path) as entries:
            for entry in entries:
                entry_path = pathlib.Path(entry)
                name = entry_path.name

                if name.startswith("$") or name.startswith("."):
                    continue

                if entry_path.is_dir():
                    dirs.append({"name": name, "path": entry_path.resolve().as_posix()})
    except PermissionError:
        return {"folders": []}

    return {"folders": sorted(dirs, key=lambda item: item["name"].lower())}
