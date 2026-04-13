from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class Favorite:
    hash: str
    type: Literal["album", "track", "artist"]
    timestamp: int
    userid: int
    extra: dict[str, Any]

    def __post_init__(self):
        raw_hash = str(self.hash or "")

        # Scoped format: u<userid>:<type>_<hash>
        if raw_hash.startswith("u") and ":" in raw_hash:
            user_prefix, remainder = raw_hash.split(":", 1)
            if user_prefix[1:].isdigit():
                raw_hash = remainder

        # Legacy format: <type>_<hash>
        type_prefix = f"{self.type}_"
        if raw_hash.startswith(type_prefix):
            raw_hash = raw_hash[len(type_prefix) :]

        self.hash = raw_hash
