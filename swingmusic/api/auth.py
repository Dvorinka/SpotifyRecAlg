import os
import secrets
import sqlite3
import threading
import time
from functools import wraps

from flask import current_app, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    current_user,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
)
from flask_openapi3 import APIBlueprint, Tag
from pydantic import BaseModel, Field

from swingmusic.config import UserConfig

# DragonflyDB integration for fast session caching
from swingmusic.db.dragonfly_extended_client import get_user_session_service
from swingmusic.db.production import UserRootDirOwnershipTable
from swingmusic.db.userdata import UserTable
from swingmusic.services.production_readiness import (
    accept_invite_token,
    create_invite_token,
    default_user_root_dir,
    get_bootstrap_status,
)
from swingmusic.services.setup_state import bootstrap_setup, get_setup_status
from swingmusic.store.homepage import HomepageStore
from swingmusic.utils.auth import check_password, hash_password

bp_tag = Tag(name="Auth", description="Authentication stuff")
api = APIBlueprint("auth", __name__, url_prefix="/auth", abp_tags=[bp_tag])


def get_limiter():
    """Get the rate limiter from app context."""
    # Prefer the global limiter initialized in app_builder.build().
    # flask-limiter v4 may store a set in current_app.extensions["limiter"],
    # so resolve defensively across versions.
    try:
        from swingmusic.app_builder import limiter as app_limiter

        if app_limiter is not None and hasattr(app_limiter, "limit"):
            return app_limiter
    except Exception:
        pass

    ext = current_app.extensions.get("limiter")
    if ext is None:
        return None

    if hasattr(ext, "limit"):
        return ext

    if isinstance(ext, set):
        for candidate in ext:
            if hasattr(candidate, "limit"):
                return candidate

    return None


def rate_limit(limit: str):
    """
    Decorator to apply rate limiting to an endpoint.
    Falls back gracefully if limiter is not available.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            limiter = get_limiter()
            if limiter:
                # Apply rate limit using the limiter's decorator
                return limiter.limit(limit)(fn)(*args, **kwargs)
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def admin_required():
    """
    Decorator to require admin role
    """

    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            if "admin" not in current_user["roles"]:
                return {"msg": "Only admins can do that!"}, 403
            return fn(*args, **kwargs)

        return decorator

    return wrapper


def create_new_token(user: dict):
    """
    Create a new token response
    """
    access_token = create_access_token(identity=user)
    max_age: int = current_app.config.get("JWT_ACCESS_TOKEN_EXPIRES")

    return {
        "msg": f"Logged in as {user['username']}",
        "accesstoken": access_token,
        "refreshtoken": create_refresh_token(identity=user),
        "maxage": max_age,
        "password_change_required": user.get("password_change_required", False),
    }


class PairTokenStore:
    def __init__(self, *, ttl_seconds: int = 300, max_codes: int = 2048):
        self.ttl_seconds = max(30, ttl_seconds)
        self.max_codes = max(128, max_codes)
        self._codes: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _cleanup_locked(self):
        now = time.time()
        expired = [
            code
            for code, payload in self._codes.items()
            if payload.get("expires_at", 0) <= now
        ]
        for code in expired:
            self._codes.pop(code, None)

        if len(self._codes) <= self.max_codes:
            return

        ordered = sorted(
            self._codes.items(),
            key=lambda item: item[1].get("created_at", 0),
        )
        drop_count = len(self._codes) - self.max_codes
        for code, _ in ordered[:drop_count]:
            self._codes.pop(code, None)

    def issue(self, token_payload: dict, user_identity: dict | None = None):
        code_alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        with self._lock:
            self._cleanup_locked()

            code = None
            for _ in range(32):
                candidate = "".join(secrets.choice(code_alphabet) for _ in range(6))
                if candidate not in self._codes:
                    code = candidate
                    break

            if not code:
                raise RuntimeError("Unable to allocate a unique pairing code")

            now = time.time()
            expires_at = now + self.ttl_seconds
            self._codes[code] = {
                "created_at": now,
                "expires_at": expires_at,
                "payload": token_payload,
                "user_id": (
                    int(user_identity["id"])
                    if isinstance(user_identity, dict) and user_identity.get("id")
                    else None
                ),
            }

            return code, int(expires_at)

    def consume(self, raw_code: str | None):
        code = (raw_code or "").strip().upper()
        if not code:
            return None

        with self._lock:
            self._cleanup_locked()
            payload = self._codes.pop(code, None)
            if not payload:
                return None

            if payload.get("expires_at", 0) <= time.time():
                return None

            return payload.get("payload")


pair_token_store = PairTokenStore(
    ttl_seconds=int(os.getenv("SWINGMUSIC_PAIR_CODE_TTL_SECONDS", "300")),
    max_codes=int(os.getenv("SWINGMUSIC_PAIR_CODE_MAX_ACTIVE", "2048")),
)


class LoginBody(BaseModel):
    username: str = Field(description="The username", example="user0")
    password: str = Field(description="The password", example="password0")


@api.post("/login")
@rate_limit("30 per minute")
def login(body: LoginBody):
    """
    Authenticate using username and password
    """

    user = UserTable.get_by_username(body.username)

    if user is None:
        return {"msg": "User not found"}, 404

    password_ok = check_password(body.password, user.password)

    if not password_ok:
        return {"msg": "Hehe! invalid password"}, 401

    res = create_new_token(user.todict())
    token = res["accesstoken"]
    age = res["maxage"]
    res = jsonify(res)
    set_access_cookies(res, token, max_age=age)

    # Cache user session in DragonflyDB for fast lookups
    session_service = get_user_session_service()
    if session_service.cache.client.is_available():
        import contextlib

        with contextlib.suppress(Exception):
            session_service.create_session(
                token,
                user.todict(),
                ttl_hours=max(1, int(age // 3600)),
            )
            session_service.set_user_session(user.id, user.todict(), ttl_seconds=age)

    return res


@api.get("/bootstrap/status")
@jwt_required(optional=True)
def bootstrap_status():
    """
    Returns owner-bootstrap state for first-run provisioning.
    """
    legacy = get_bootstrap_status()
    setup = get_setup_status()
    return {
        **legacy,
        **setup,
    }


class BootstrapOwnerBody(BaseModel):
    username: str = Field(description="Owner username")
    password: str = Field(description="Owner password")
    root_dirs: list[str] = Field(
        default_factory=list, description="Initial root directories"
    )


@api.post("/bootstrap/owner")
@rate_limit("5 per minute")
def bootstrap_owner(body: BootstrapOwnerBody):
    """
    Creates the first owner account when no users exist.
    """
    try:
        owner = bootstrap_setup(
            username=body.username,
            password=body.password,
            root_dirs=body.root_dirs,
        )
    except ValueError as error:
        return {"msg": str(error)}, 400

    res = create_new_token(owner.todict())
    token = res["accesstoken"]
    age = res["maxage"]
    response = jsonify(res)
    set_access_cookies(response, token, max_age=age)
    return response


class InviteCreateBody(BaseModel):
    roles: list[str] = Field(
        default_factory=lambda: ["user"], description="Roles for invited account"
    )
    expires_in_seconds: int = Field(
        default=7 * 24 * 3600, description="Invite validity in seconds"
    )


@api.post("/invite/create")
@admin_required()
def create_invite(body: InviteCreateBody):
    """
    Create an invite token for onboarding additional users.
    """
    invite = create_invite_token(
        created_by=current_user["id"],
        roles=body.roles,
        expires_in_seconds=body.expires_in_seconds,
    )
    return {
        "token": invite.token,
        "expires_at": invite.expires_at,
        "roles": invite.roles,
    }


class InviteAcceptBody(BaseModel):
    token: str = Field(description="Invite token")
    username: str = Field(description="New username")
    password: str = Field(description="New user password")


@api.post("/invite/accept")
@rate_limit("5 per minute")
def accept_invite(body: InviteAcceptBody):
    """
    Accept an invite token and create a user account.
    """
    try:
        user = accept_invite_token(
            token=body.token,
            username=body.username,
            password=body.password,
        )
    except ValueError as error:
        return {"msg": str(error)}, 400

    res = create_new_token(user.todict())
    token = res["accesstoken"]
    age = res["maxage"]
    response = jsonify(res)
    set_access_cookies(response, token, max_age=age)
    return response


@api.get("/getpaircode")
@jwt_required()
def get_pair():
    """
    Get a new pair code to log in to thee Swing Music mobile app
    """
    user_identity = get_jwt_identity()
    if not isinstance(user_identity, dict) or user_identity.get("id") is None:
        return {"msg": "Unauthorized"}, 401

    token_payload = create_new_token(user_identity)
    code, expires_at = pair_token_store.issue(token_payload, user_identity)

    server_url = request.headers.get("Origin", "").strip()
    if not server_url:
        server_url = request.host_url.rstrip("/")
    else:
        server_url = server_url.rstrip("/")

    return {
        "code": code,
        "expires_at": expires_at,
        "ttl_seconds": pair_token_store.ttl_seconds,
        "server_url": server_url,
        # Keep payload contract explicit for mobile/desktop clients.
        # Format: "<server_url>|<pair_code>"
        "qr_payload": f"{server_url}|{code}",
    }


class PairDeviceQuery(BaseModel):
    code: str = Field("", description="The code")


@api.get("/pair")
@jwt_required(optional=True)
@rate_limit("20 per minute")
def pair_with_code(query: PairDeviceQuery):
    """
    Get an access token by sending a pair code. NOTE: A code can only be used once!
    """
    token = pair_token_store.consume(query.code)
    if token:
        return token

    return {"msg": "Invalid or expired code"}, 400


@api.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    """
    Refresh an access token by sending a refresh token in the Authorization header

    >>> Headers:
    >>> Authorization: Bearer <refresh_token>

    Won't work with cookies!!!
    """
    user = get_jwt_identity()
    return create_new_token(user)


class UpdateProfileBody(BaseModel):
    id: int = Field(0, description="The user id")
    email: str = Field("", description="The email")
    username: str = Field("", description="The username", example="user0")
    password: str = Field("", description="The password", example="password0")
    roles: list[str] = Field(None, description="The roles")


@api.put("/profile/update")
def update_profile(body: UpdateProfileBody):
    """
    Update user profile
    """
    user = {
        "id": body.id,
        "username": body.username,
        "password": body.password,
        "roles": body.roles,
    }

    # prevent updating guest
    if current_user["username"] == "guest" or user["username"] == "guest":
        return {"msg": "Cannot update guest user"}, 400

    # if not id, update self
    if not user["id"]:
        user["id"] = current_user["id"]

    if body.roles is not None:
        # only admins can update roles
        if "admin" not in current_user["roles"]:
            return {"msg": "Only admins can update roles"}, 403

        all_users = list(UserTable.get_all())
        if "admin" not in body.roles:
            # check if we're removing the last admin
            admins = [user for user in all_users if "admin" in user.roles]

            if len(admins) == 1 and admins[0].id == user["id"]:
                return {"msg": "Cannot remove the only admin"}, 400

        # guest roles cannot be updated
        _user = [u for u in all_users if u.id == user["id"]][0]
        if "guest" in _user.roles:
            return {"msg": "Cannot update guest user"}, 400

    if user["password"]:
        user["password"] = hash_password(user["password"])

    # remove empty values
    clean_user = {k: v for k, v in user.items() if v}

    # finally, convert roles to json string
    # doing it here to prevent deleting roles from clean user
    # when body.roles is an empty list
    if body.roles is not None:
        clean_user["roles"] = body.roles

    try:
        # return authdb.update_user(clean_user)
        UserTable.update_one(clean_user)
        return UserTable.get_by_id(user["id"]).todict()
    except sqlite3.IntegrityError:
        return {"msg": "Username already exists"}, 400


@api.post("/profile/create")
@admin_required()
def create_user(body: UpdateProfileBody):
    """
    Create a new user
    """
    if not body.username or not body.password:
        return {"msg": "Username and password are required"}, 400

    user = {
        "username": body.username,
        "password": hash_password(body.password),
        "roles": [],
    }

    # check if user already exists
    if UserTable.get_by_username(user["username"]):
        return {"msg": "Username already exists"}, 400

    UserTable.insert_one(user)
    user = UserTable.get_by_username(user["username"])

    if user:
        user_root = default_user_root_dir(user.username)
        os.makedirs(user_root, exist_ok=True)
        UserRootDirOwnershipTable.assign_paths(user.id, [user_root])
        HomepageStore.entries["recently_played"].add_new_user(user.id)
        return user.todict()

    return {
        "msg": "Failed to create user",
    }, 500


@api.post("/profile/guest/create")
@admin_required()
def create_guest_user():
    """
    Create a guest user
    """
    # check if guest user already exists
    guest_user = UserTable.get_by_username("guest")

    if guest_user:
        return {
            "msg": "Guest user already exists",
        }, 400

    UserTable.insert_guest_user()
    user = UserTable.get_by_username("guest")

    if user:
        # Guest user is isolated too, but kept under a deterministic root.
        user_root = default_user_root_dir(user.username)
        os.makedirs(user_root, exist_ok=True)
        UserRootDirOwnershipTable.assign_paths(user.id, [user_root])
        HomepageStore.entries["recently_played"].add_new_user(user.id)

        return {
            "msg": "Guest user created",
        }

    return {
        "msg": "Failed to create guest user",
    }, 500


class DeleteUseBody(BaseModel):
    username: str = Field("", description="The username")


class ChangePasswordBody(BaseModel):
    current_password: str = Field(description="Current password")
    new_password: str = Field(description="New password")


@api.post("/password/change")
@jwt_required()
@rate_limit("5 per minute")
def change_password(body: ChangePasswordBody):
    """
    Change the current user's password. Required when password_change_required is True.
    """
    user_id = current_user["id"]
    user = UserTable.get_by_id(user_id)

    if not user:
        return {"msg": "User not found"}, 404

    # Verify current password
    if not check_password(body.current_password, user.password):
        return {"msg": "Current password is incorrect"}, 401

    # Validate new password
    if len(body.new_password) < 8:
        return {"msg": "Password must be at least 8 characters"}, 400

    if body.current_password == body.new_password:
        return {"msg": "New password must be different from current password"}, 400

    # Update password and clear the change required flag
    updated_user = {
        "id": user_id,
        "password": hash_password(body.new_password),
        "password_change_required": False,
    }
    UserTable.update_one(updated_user)

    return {"msg": "Password changed successfully", "password_change_required": False}


@api.delete("/profile/delete")
@admin_required()
def delete_user(body: DeleteUseBody):
    """
    Delete a user by username
    """
    # prevent admin from deleting themselves
    if body.username == current_user["username"]:
        return {"msg": "Sorry! you cannot delete yourselfu"}, 400

    # prevent deleting the only admin
    users = UserTable.get_all()
    admins = [user for user in users if "admin" in user.roles]
    if len(admins) == 1 and admins[0].username == body.username:
        return {"msg": "Cannot delete the only admin"}, 400

    UserTable.remove_by_username(body.username)
    return {"msg": f"User {body.username} deleted"}


@api.get("/logout")
@jwt_required(optional=True)
def logout():
    """
    Log out and clear the access token cookie
    """
    # Invalidate session in DragonflyDB
    if current_user:
        session_service = get_user_session_service()
        if session_service.cache.client.is_available():
            import contextlib

            with contextlib.suppress(Exception):
                session_service.invalidate_user_session(current_user["id"])

    res = jsonify({"msg": "Logged out"})
    res.delete_cookie("access_token_cookie")
    return res


class GetAllUsersQuery(BaseModel):
    simplified: bool = Field(
        False, description="Whether to return simplified user data"
    )


@api.get("/users")
@jwt_required(optional=True)
def get_all_users(query: GetAllUsersQuery):
    """
    Get all users (if you're an admin, you will also receive accounts settings)
    """
    config = UserConfig()
    settings = {
        "enableGuest": False,
        "usersOnLogin": config.usersOnLogin,
    }

    res = {
        "settings": {},
        "users": [],
    }

    users = list(UserTable.get_all())
    is_admin = current_user and "admin" in current_user["roles"]
    settings["enableGuest"] = [
        user for user in users if user.username == "guest"
    ].__len__() > 0

    # if user is admin, also return settings
    if is_admin:
        res = {
            "settings": settings,
        }

    # if is normal user, return empty response
    elif current_user or (
        not current_user
        and not settings["usersOnLogin"]
        and not settings["enableGuest"]
    ):
        return res

    # remove guest user
    # if not settings["enableGuest"]:
    #     users = [user for user in users if user.username != "guest"]

    if not settings["usersOnLogin"]:
        users = [user for user in users if user.username == "guest"]

    # reverse list to show latest users first
    users = reversed(users)
    # bring admins to the front
    users = sorted(users, key=lambda x: "admin" in x.roles, reverse=True)
    # bring current user to index 0
    if current_user:
        users = sorted(
            users,
            key=lambda x: x.username == current_user["username"],
            reverse=True,
        )

    if query.simplified:
        res["users"] = [user.todict_simplified() for user in users]
    else:
        res["users"] = [user.todict() for user in users]

    return res


@api.get("/user")
@jwt_required(optional=True)
def get_logged_in_user():
    """
    Get logged in user
    """
    if get_jwt_identity() is None:
        return {"authenticated": False}

    user = dict(current_user)
    user["authenticated"] = True
    return user
