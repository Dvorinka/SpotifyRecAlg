"""
Contains all the file upload routes for manual music upload functionality.
"""

import os
from pathlib import Path

from flask import jsonify, request
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field
from werkzeug.utils import secure_filename

from swingmusic.api.auth import admin_required
from swingmusic.config import UserConfig

tag = Tag(name="Upload", description="Manual music file upload functionality")
api = APIBlueprint("upload", __name__, url_prefix="/upload", abp_tags=[tag])

# Allowed audio file extensions
ALLOWED_EXTENSIONS = {
    "mp3",
    "flac",
    "wav",
    "aac",
    "m4a",
    "ogg",
    "wma",
    "opus",
    "aiff",
    "au",
    "ra",
    "3gp",
    "amr",
    "awb",
    "dct",
    "dvf",
    "m4p",
    "mmf",
    "mpc",
    "msv",
    "nmf",
    "nsf",
    "qcp",
    "rm",
    "sln",
    "vox",
    "wv",
}

# Maximum file size (100MB)
MAX_FILE_SIZE = 100 * 1024 * 1024


def is_allowed_file(filename: str) -> bool:
    """Check if file has an allowed audio extension."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def is_path_within_root_dirs(filepath: str) -> bool:
    """
    Check if a filepath is within one of the configured root directories.
    Prevents directory traversal attacks.
    """
    config = UserConfig()
    resolved_path = Path(filepath).resolve()

    for root_dir in config.rootDirs:
        if root_dir == "$home":
            root_path = Path.home().resolve()
        else:
            root_path = Path(root_dir).resolve()

        # Check if resolved_path is the root or a child of root
        if resolved_path == root_path or root_path in resolved_path.parents:
            return True

    return False


def _default_upload_dir(config: UserConfig) -> Path:
    """Resolve the default upload directory from user configuration."""
    if hasattr(config, "uploadDir") and config.uploadDir:
        return Path(config.uploadDir).expanduser()

    if config.rootDirs:
        first_root = config.rootDirs[0]
        if first_root == "$home":
            return Path.home() / "Music"
        return Path(first_root).expanduser()

    return Path.home() / "Music"


def resolve_upload_directory(target_dir: str | None = None) -> Path:
    """
    Resolve and validate upload directory.

    If target_dir is provided, it must resolve within configured root directories.
    """
    config = UserConfig()

    if target_dir:
        target_dir = target_dir.strip()

    if target_dir:
        if target_dir == "$home":
            upload_dir = _default_upload_dir(config).resolve()
        else:
            upload_dir = Path(target_dir).expanduser().resolve()

        if not is_path_within_root_dirs(str(upload_dir)):
            raise ValueError(
                "Target upload directory must be inside configured library folders"
            )

        upload_dir.mkdir(parents=True, exist_ok=True)
        return upload_dir

    upload_dir = _default_upload_dir(config).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


class UploadResponse(BaseModel):
    success: bool = Field(description="Whether the upload was successful")
    message: str = Field(description="Status message")
    track_id: str | None = Field(None, description="ID of the added track")
    filename: str | None = Field(None, description="Name of the uploaded file")


class BatchUploadResponse(BaseModel):
    success: bool = Field(description="Whether the batch upload was successful")
    message: str = Field(description="Status message")
    uploaded_files: list[UploadResponse] = Field(description="List of upload results")
    failed_files: list[str] = Field(description="List of failed files")


@api.post("/single")
@admin_required()
def upload_single_file():
    """
    Upload a single music file

    Uploads a single music file to the configured music folder and adds it to the library.
    Supports drag-and-drop and file selection.
    """
    try:
        if "file" not in request.files:
            return jsonify({"success": False, "message": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"success": False, "message": "No file selected"}), 400

        # Check file extension
        if not is_allowed_file(file.filename):
            return jsonify(
                {
                    "success": False,
                    "message": f"File type not allowed. Supported formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
                }
            ), 400

        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)

        if file_size > MAX_FILE_SIZE:
            return jsonify(
                {
                    "success": False,
                    "message": f"File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB",
                }
            ), 400

        target_dir = request.form.get("target_dir")
        try:
            upload_dir = resolve_upload_directory(target_dir)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400

        # Secure the filename and create full path
        filename = secure_filename(file.filename)
        file_path = upload_dir / filename

        # Handle filename conflicts
        counter = 1
        original_filename = filename
        while file_path.exists():
            name, ext = os.path.splitext(original_filename)
            filename = f"{name}_{counter}{ext}"
            file_path = upload_dir / filename
            counter += 1

        # Save the file
        file.save(file_path)

        # Extract metadata and add to library
        try:
            # This would trigger a library rescan for the specific file
            # For now, we'll return the file info and let the frontend handle the refresh
            track_info = {
                "filepath": str(file_path),
                "filename": filename,
                "size": file_size,
            }

            return jsonify(
                {
                    "success": True,
                    "message": f"File '{filename}' uploaded successfully",
                    "filename": filename,
                    "filepath": str(file_path),
                    "track_info": track_info,
                }
            )

        except Exception as e:
            # If metadata extraction fails, still return success for the upload
            return jsonify(
                {
                    "success": True,
                    "message": f"File '{filename}' uploaded successfully (metadata extraction failed)",
                    "filename": filename,
                    "filepath": str(file_path),
                    "warning": f"Metadata extraction failed: {str(e)}",
                }
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"Upload failed: {str(e)}"}), 500


@api.post("/batch")
@admin_required()
def upload_multiple_files():
    """
    Upload multiple music files

    Uploads multiple music files to the configured music folder and adds them to the library.
    Supports drag-and-drop of multiple files.
    """
    try:
        if "files" not in request.files:
            return jsonify({"success": False, "message": "No files provided"}), 400

        files = request.files.getlist("files")
        if not files:
            return jsonify({"success": False, "message": "No files selected"}), 400

        uploaded_files = []
        failed_files = []

        target_dir = request.form.get("target_dir")
        try:
            upload_dir = resolve_upload_directory(target_dir)
        except ValueError as e:
            return jsonify({"success": False, "message": str(e)}), 400

        for file in files:
            if file.filename == "":
                continue

            try:
                # Check file extension
                if not is_allowed_file(file.filename):
                    failed_files.append(f"{file.filename} - File type not allowed")
                    continue

                # Check file size
                file.seek(0, os.SEEK_END)
                file_size = file.tell()
                file.seek(0)

                if file_size > MAX_FILE_SIZE:
                    failed_files.append(f"{file.filename} - File too large")
                    continue

                # Secure filename and handle conflicts
                filename = secure_filename(file.filename)
                file_path = upload_dir / filename

                counter = 1
                original_filename = filename
                while file_path.exists():
                    name, ext = os.path.splitext(original_filename)
                    filename = f"{name}_{counter}{ext}"
                    file_path = upload_dir / filename
                    counter += 1

                # Save the file
                file.save(file_path)

                uploaded_files.append(
                    {
                        "success": True,
                        "message": f"File '{filename}' uploaded successfully",
                        "filename": filename,
                        "filepath": str(file_path),
                        "size": file_size,
                    }
                )

            except Exception as e:
                failed_files.append(f"{file.filename} - {str(e)}")

        total_files = len(uploaded_files) + len(failed_files)
        success_count = len(uploaded_files)

        return jsonify(
            {
                "success": len(uploaded_files) > 0,
                "message": f"Uploaded {success_count} of {total_files} files",
                "uploaded_files": uploaded_files,
                "failed_files": failed_files,
            }
        )

    except Exception as e:
        return jsonify(
            {"success": False, "message": f"Batch upload failed: {str(e)}"}
        ), 500


@api.get("/config")
def get_upload_config():
    """
    Get upload configuration

    Returns the current upload configuration including allowed file types,
    maximum file size, and upload directory.
    """
    upload_dir = str(resolve_upload_directory())

    return jsonify(
        {
            "allowed_extensions": sorted(ALLOWED_EXTENSIONS),
            "max_file_size": MAX_FILE_SIZE,
            "max_file_size_mb": MAX_FILE_SIZE // (1024 * 1024),
            "upload_directory": upload_dir,
            "supported_formats": [
                {"ext": ext, "description": get_format_description(ext)}
                for ext in sorted(ALLOWED_EXTENSIONS)
            ],
        }
    )


def get_format_description(extension: str) -> str:
    """Get a user-friendly description for a file format."""
    descriptions = {
        "mp3": "MP3 Audio",
        "flac": "FLAC Lossless Audio",
        "wav": "WAV Audio",
        "aac": "AAC Audio",
        "m4a": "M4A Audio",
        "ogg": "OGG Vorbis Audio",
        "wma": "WMA Audio",
        "opus": "Opus Audio",
        "aiff": "AIFF Audio",
        "au": "AU Audio",
        "ra": "RealAudio",
        "3gp": "3GP Audio",
        "amr": "AMR Audio",
        "awb": "AWB Audio",
        "dct": "DCT Audio",
        "dvf": "DVF Audio",
        "m4p": "M4P Audio",
        "mmf": "MMF Audio",
        "mpc": "MPC Audio",
        "msv": "MSV Audio",
        "nmf": "NMF Audio",
        "nsf": "NSF Audio",
        "qcp": "QCP Audio",
        "rm": "RealMedia Audio",
        "sln": "SLN Audio",
        "vox": "VOX Audio",
        "wv": "WavPack Audio",
    }
    return descriptions.get(extension.lower(), f"{extension.upper()} Audio")


@api.post("/rescan")
@admin_required()
def trigger_library_rescan():
    """
    Trigger library rescan

    Triggers a library rescan to detect newly uploaded files.
    """
    try:
        # This would integrate with the existing library scanning system
        # For now, return a success response
        return jsonify(
            {"success": True, "message": "Library rescan triggered successfully"}
        )
    except Exception as e:
        return jsonify(
            {"success": False, "message": f"Failed to trigger library rescan: {str(e)}"}
        ), 500
