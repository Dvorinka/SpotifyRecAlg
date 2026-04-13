import hashlib
import hmac
import os

from flask import has_app_context, has_request_context
from flask_jwt_extended import current_user

from swingmusic.config import UserConfig
from swingmusic.logger import log


def hash_password(password: str) -> str:
    """
    Hashes the given password using sha256 algorithm and the user id as salt.

    :param password: The password to hash.

    :return: The hashed password.
    """
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        UserConfig().serverId.encode("utf-8"),
        100000,
    ).hex()


def check_password(password: str, hashed: str) -> bool:
    """
    This function checks if the given password matches the hashed password.

    :param password: The password to check.
    :param hashed: The hashed password.

    :return: Whether the password matches.
    """

    return hmac.compare_digest(hash_password(password), hashed)


def get_current_userid() -> int:
    """
    Get the current session user.
    """
    fallback_userid = int(os.getenv("SWINGMUSIC_DEFAULT_USER_ID", "1"))

    # Background workers and startup code can run outside Flask contexts.
    # In those paths, we intentionally use a deterministic fallback user id.
    if not has_app_context() or not has_request_context():
        return fallback_userid

    try:
        return int(current_user["id"])
    except Exception as e:
        if log:
            log.error("get_current_userid: Unable to resolve request user id")
            log.error(e)
        return fallback_userid
