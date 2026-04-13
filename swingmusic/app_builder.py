import datetime as dt
import importlib
import logging
import os
import pathlib
from dataclasses import dataclass
from typing import Literal

from flask import Response, jsonify, request
from flask_compress import Compress
from flask_cors import CORS
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    get_jwt,
    get_jwt_identity,
    set_access_cookies,
    verify_jwt_in_request,
)
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from flask_limiter.util import get_remote_address
from flask_openapi3 import Info, OpenAPI

from swingmusic.config import UserConfig
from swingmusic.db.userdata import UserTable
from swingmusic.services.setup_state import get_setup_status, is_setup_complete
from swingmusic.settings import Metadata, Paths
from swingmusic.utils.paths import get_client_files_extensions

log = logging.getLogger(__name__)
# # # # # # # # # # # # # # # # # #
# Grouped configuration function  #
# # # # # # # # # # # # # # # # # #


def config_app(web):

    # CORS - configurable via environment variable
    cors_origins = os.getenv("SWINGMUSIC_CORS_ORIGINS", "*")
    if cors_origins != "*":
        # Parse comma-separated list of origins
        cors_origins = [
            origin.strip() for origin in cors_origins.split(",") if origin.strip()
        ]
    CORS(web, origins=cors_origins, supports_credentials=True)

    # RESPONSE COMPRESSION
    # Only compress JSON responses
    Compress(web)
    web.config["COMPRESS_MIMETYPES"] = [
        "application/json",
    ]


def config_jwt(web):
    # JWT CONFIGS
    web.config["JWT_VERIFY_SUB"] = False
    web.config["JWT_SECRET_KEY"] = UserConfig().serverId
    web.config["JWT_TOKEN_LOCATION"] = ["cookies", "headers"]
    # Enable CSRF protection for cookie-based auth
    web.config["JWT_COOKIE_CSRF_PROTECT"] = True
    web.config["JWT_CSRF_IN_COOKIES"] = True
    web.config["JWT_CSRF_HEADER_NAME"] = "X-CSRF-TOKEN"
    web.config["JWT_SESSION_COOKIE"] = False

    jwt_expiry = int(dt.timedelta(days=30).total_seconds())
    web.config["JWT_ACCESS_TOKEN_EXPIRES"] = jwt_expiry

    jwt = JWTManager(web)

    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        identity = jwt_data["sub"]
        userid = identity["id"]
        user = UserTable.get_by_id(userid)

        if user:
            return user.todict()


# Rate limiter instance - configured in build()
limiter: Limiter | None = None


def get_limiter() -> Limiter:
    """Get the rate limiter instance."""
    global limiter
    if limiter is None:
        raise RuntimeError("Limiter not initialized. Call build() first.")
    return limiter


@dataclass(frozen=True)
class ApiRegistration:
    module_path: str
    symbol: str
    register_as: Literal["api", "blueprint", "callable"]
    required: bool = True
    feature_flag: str | None = None
    enabled_by_default: bool = True


_BOOT_REGISTRATION_STATE: dict[str, list[str]] = {
    "registered": [],
    "failed": [],
}


def _feature_enabled(flag: str | None, default: bool = True) -> bool:
    if flag is None:
        return True

    value = os.getenv(flag)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


CORE_API_REGISTRATIONS: list[ApiRegistration] = [
    ApiRegistration("swingmusic.api.auth", "api", "api", required=True),
    ApiRegistration("swingmusic.api.setup", "api", "api", required=True),
    ApiRegistration("swingmusic.api.album", "api", "api", required=True),
    ApiRegistration("swingmusic.api.artist", "api", "api", required=True),
    ApiRegistration("swingmusic.api.stream", "api", "api", required=True),
    ApiRegistration("swingmusic.api.search", "api", "api", required=True),
    ApiRegistration("swingmusic.api.folder", "api", "api", required=True),
    ApiRegistration("swingmusic.api.playlist", "api", "api", required=True),
    ApiRegistration("swingmusic.api.favorites", "api", "api", required=True),
    ApiRegistration("swingmusic.api.imgserver", "api", "api", required=True),
    ApiRegistration("swingmusic.api.settings", "api", "api", required=True),
    ApiRegistration("swingmusic.api.colors", "api", "api", required=True),
    ApiRegistration("swingmusic.api.lyrics", "api", "api", required=True),
    ApiRegistration("swingmusic.api.backup_and_restore", "api", "api", required=False),
    ApiRegistration("swingmusic.api.collections", "api", "api", required=True),
    ApiRegistration("swingmusic.api.scrobble", "api", "api", required=True),
    ApiRegistration("swingmusic.api.home", "api", "api", required=True),
    ApiRegistration("swingmusic.api.getall", "api", "api", required=True),
    ApiRegistration("swingmusic.api.spotify", "spotify_bp", "api", required=False),
    ApiRegistration(
        "swingmusic.api.spotify_settings", "spotify_settings_bp", "api", required=False
    ),
    ApiRegistration("swingmusic.api.upload", "api", "api", required=False),
    ApiRegistration("swingmusic.api.downloads", "api", "api", required=True),
    ApiRegistration(
        "swingmusic.api.music_catalog", "music_catalog_bp", "blueprint", required=True
    ),
    ApiRegistration("swingmusic.api.plugins", "api", "api", required=False),
    ApiRegistration("swingmusic.api.plugins.lyrics", "api", "api", required=False),
    ApiRegistration("swingmusic.api.plugins.mixes", "api", "api", required=False),
    ApiRegistration("swingmusic.api.dragonfly", "api", "api", required=False),
    ApiRegistration("swingmusic.api.recently_played", "api", "api", required=False),
]


OPTIONAL_API_REGISTRATIONS: list[ApiRegistration] = [
    ApiRegistration(
        "swingmusic.api.enhanced_search",
        "register_enhanced_search_api",
        "callable",
        required=False,
        feature_flag="SWINGMUSIC_ENABLE_ENHANCED_SEARCH",
        enabled_by_default=True,
    ),
    ApiRegistration(
        "swingmusic.api.universal_downloader",
        "register_universal_downloader_api",
        "callable",
        required=False,
        feature_flag="SWINGMUSIC_ENABLE_UNIVERSAL_DOWNLOADER",
        enabled_by_default=True,
    ),
    ApiRegistration(
        "swingmusic.api.update_tracking",
        "update_tracking_bp",
        "blueprint",
        required=False,
        feature_flag="SWINGMUSIC_ENABLE_UPDATE_TRACKING",
        enabled_by_default=True,
    ),
    ApiRegistration(
        "swingmusic.api.audio_quality",
        "audio_quality_bp",
        "blueprint",
        required=False,
        feature_flag="SWINGMUSIC_ENABLE_AUDIO_QUALITY",
        enabled_by_default=True,
    ),
    ApiRegistration(
        "swingmusic.api.advanced_ux",
        "advanced_ux_bp",
        "blueprint",
        required=False,
        feature_flag="SWINGMUSIC_ENABLE_ADVANCED_UX",
        enabled_by_default=True,
    ),
    ApiRegistration(
        "swingmusic.api.recap",
        "recap_bp",
        "blueprint",
        required=False,
        feature_flag="SWINGMUSIC_ENABLE_RECAP",
        enabled_by_default=True,
    ),
    ApiRegistration(
        "swingmusic.api.mobile_offline",
        "mobile_offline_bp",
        "blueprint",
        required=False,
        feature_flag="SWINGMUSIC_ENABLE_MOBILE_OFFLINE",
        enabled_by_default=True,
    ),
]


def _register_entry(web: OpenAPI, entry: ApiRegistration):
    if not _feature_enabled(entry.feature_flag, entry.enabled_by_default):
        log.info("Skipping feature-gated API module: %s", entry.module_path)
        return

    try:
        module = importlib.import_module(entry.module_path)
        symbol = getattr(module, entry.symbol)

        if entry.register_as == "api":
            web.register_api(symbol)
        elif entry.register_as == "blueprint":
            web.register_blueprint(symbol)
        elif entry.register_as == "callable":
            symbol(web)
        else:
            raise RuntimeError(f"Unknown register type: {entry.register_as}")

        _BOOT_REGISTRATION_STATE["registered"].append(
            f"{entry.module_path}:{entry.symbol}"
        )
    except Exception as error:
        detail = f"{entry.module_path}:{entry.symbol} ({error})"
        _BOOT_REGISTRATION_STATE["failed"].append(detail)
        log.exception(
            "Failed to register API module %s.%s", entry.module_path, entry.symbol
        )

        strict_boot = _feature_enabled("SWINGMUSIC_STRICT_BOOT", default=False)
        if entry.required and strict_boot:
            raise


def load_endpoints(web: OpenAPI):
    _BOOT_REGISTRATION_STATE["registered"].clear()
    _BOOT_REGISTRATION_STATE["failed"].clear()

    with web.app_context():
        for entry in CORE_API_REGISTRATIONS:
            _register_entry(web, entry)

        for entry in OPTIONAL_API_REGISTRATIONS:
            _register_entry(web, entry)

        # Keep client contracts stable even when optional modules are disabled.
        from swingmusic.api.optional_feature_fallbacks import (
            register_optional_feature_fallbacks,
        )

        register_optional_feature_fallbacks(web)


def run_boot_smoke_checks(web: OpenAPI):
    required_rules = {
        "/auth/login",
        "/auth/bootstrap/status",
        "/setup/status",
        "/api/downloads/jobs",
        "/api/catalog/search",
    }

    current_rules = {rule.rule for rule in web.url_map.iter_rules()}
    missing_rules = sorted(required_rules - current_rules)

    if missing_rules:
        log.error("Boot smoke check failed. Missing routes: %s", missing_rules)
    else:
        log.info("Boot smoke check passed (%s routes).", len(current_rules))

    strict_boot = _feature_enabled("SWINGMUSIC_STRICT_BOOT", default=False)
    if strict_boot and (missing_rules or _BOOT_REGISTRATION_STATE["failed"]):
        raise RuntimeError(
            "Strict boot failed. Missing routes or API module registration failures detected."
        )


# # # # # # # # # # #
# Create App object #
# # # # # # # # # # #

api_info = Info(
    title="Swing Music",
    version=f"v{Metadata.version}",
    description="The REST API exposed by your Swing Music server",
)

app = OpenAPI(__name__, info=api_info, doc_prefix="/docs")


def check_auth_need() -> bool:
    """
    Check if the current request is for a static file.
    We do not need auth for index or static images of index.

    :return: True if static file else False
    """

    # INFO: Routes that don't need authentication
    urls = {
        "/auth/login",
        "/auth/user",
        "/auth/users",
        "/auth/pair",
        "/auth/logout",
        "/auth/refresh",
        "/auth/bootstrap",
        "/auth/invite/accept",
        "/setup",
        "/docs",
        "/healthz",
    }
    files = {".webp", ".jpg", *get_client_files_extensions()}

    urls = tuple(urls)
    files = tuple(files)

    if request.path == "/" or request.path.endswith(files):
        return True

    # if request path starts with any of the blacklisted routes, don't verify jwt
    return bool(request.path.startswith(urls))


# # # # # # # # # # # # #
# global endpoint logic #
# # # # # # # # # # # # #


@app.route("/<path:path>")
def serve_client_files(path: str):
    """
    Serves the static files in the client folder.
    """

    # Handle potential double /client path (e.g., '/client/some.js' -> '/client/client/some.js')
    # This can occur with certain proxy configurations
    if path.startswith("client/"):
        path = path[7:]  # Remove duplicate 'client/' prefix

    js_or_css = path.endswith(".js") or path.endswith(".css")

    if not js_or_css:
        return app.send_static_file(path)

    # INFO: Safari doesn't support gzip encoding
    # See issue: https://github.com/swingmx/swingmusic/issues/155
    user_agent = request.headers.get("User-Agent", "")
    if "Safari" in user_agent and "Chrome" not in user_agent:
        return app.send_static_file(path)

    if "gzip" in request.headers.get("Accept-Encoding", ""):
        gz_name = path + ".gz"
        gzipped_path = pathlib.Path(app.static_folder or "") / gz_name

        if gzipped_path.exists():
            response = app.make_response(app.send_static_file(gz_name))
            response.headers["Content-Encoding"] = "gzip"
            return response

    return app.send_static_file(path)


@app.route("/")
def serve_client():
    """
    Serves the index.html file at `client/index.html`.
    """
    return app.send_static_file("index.html")


@app.get("/healthz")
def healthz():
    setup = get_setup_status()
    failed = list(_BOOT_REGISTRATION_STATE["failed"])

    status_code = 200
    if failed and _feature_enabled("SWINGMUSIC_STRICT_BOOT", default=False):
        status_code = 503

    return (
        jsonify(
            {
                "ok": status_code == 200,
                "setup_completed": setup.get("setup_completed", False),
                "onboarding_required": setup.get("required", True),
                "registered_modules": list(_BOOT_REGISTRATION_STATE["registered"]),
                "failed_modules": failed,
            }
        ),
        status_code,
    )


@app.errorhandler(RateLimitExceeded)
def handle_rate_limit(error: RateLimitExceeded):
    retry_after = getattr(error, "retry_after", None)
    response = jsonify(
        {
            "msg": "Too many requests. Please wait before trying again.",
            "error": "rate_limited",
            "retry_after": retry_after,
        }
    )
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response, 429


def build() -> OpenAPI:
    """
    Call this function to obtain the final flask/openapi object.

    Do not import app directly as the static_folder can only be set
    when cli args are parsed.

    :return: OpenApi object with all config set
    """

    # set late state config
    app.static_folder = Paths().client_path

    @app.before_request
    def verify_auth():
        """
        Verifies the JWT token before each request.
        """

        if check_auth_need():
            return

        if not is_setup_complete():
            setup = get_setup_status()
            return (
                jsonify(
                    {
                        "error": "setup_incomplete",
                        "msg": "Initial setup must be completed before using product APIs.",
                        "setup": setup,
                    }
                ),
                423,
            )

        verify_jwt_in_request()

    @app.after_request
    def refresh_expiring_jwt(response: Response):
        """
        Refreshes the cookies JWT token after each request.
        """

        # INFO: If the request has an Authorization header, don't refresh the jwt
        # Request is probably from the mobile client or a third party
        if check_auth_need() or request.headers.get("Authorization"):
            return response

        try:
            exp_timestamp = get_jwt()["exp"]
            until = dt.datetime.now(dt.UTC) + dt.timedelta(days=7)

            if until.timestamp() > exp_timestamp:
                access_token = create_access_token(identity=get_jwt_identity())
                set_access_cookies(response, access_token)

            return response
        except (RuntimeError, KeyError):
            return response

    config_app(app)
    config_jwt(app)

    # Initialize rate limiter
    global limiter
    rate_limit = os.getenv("SWINGMUSIC_RATE_LIMIT", "200 per hour;50 per minute")
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=[rate_limit],
        default_limits_exempt_when=check_auth_need,
        storage_uri="memory://",
    )

    load_endpoints(app)
    run_boot_smoke_checks(app)

    return app
