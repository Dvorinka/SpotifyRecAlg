import os
from importlib import metadata as importlib_metadata

try:
    __version__ = importlib_metadata.version("swingmusic")
except importlib_metadata.PackageNotFoundError:
    # fallback to version.txt
    version_file = os.path.join(os.path.dirname(__file__), "..", "..", "version.txt")
    with open(version_file) as f:
        __version__ = f.read().strip()
