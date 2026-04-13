import random
import socket as Socket
import time
from io import BytesIO

import requests
from PIL import Image, UnidentifiedImageError
from requests.exceptions import ConnectionError, ReadTimeout, Timeout

# User agents for rotation to avoid rate limiting
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:90.0) Gecko/20100101 Firefox/90.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
]


def has_connection(host="google.it", port=80, timeout=3):
    """
    # REVIEW Was:
    Host: 8.8.8.8 (google-public-dns-a.google.com)
    OpenPort: 53/tcp
    Service: domain (DNS/TCP)
    """
    try:
        Socket.setdefaulttimeout(timeout)
        Socket.socket(Socket.AF_INET, Socket.SOCK_STREAM).connect((host, port))
        return True
    except OSError:
        return False


def get_ip():
    """
    Get the IP address of the current system.
    Will return address of default outgoing chanel.
    """
    soc = Socket.socket(Socket.AF_INET, Socket.SOCK_DGRAM)
    try:
        soc.connect(("8.8.8.8", 80))
    except OSError:
        return None
    ip_address = str(soc.getsockname()[0])
    soc.close()

    return ip_address


def download_file(
    url: str,
    timeout: int = 10,
    max_retries: int = 2,
    retry_delay: int = 10,
    headers: dict | None = None,
) -> bytes | None:
    """
    Downloads a file from a URL with retry logic.

    :param url: URL to download from
    :param timeout: Request timeout in seconds
    :param max_retries: Maximum number of retry attempts
    :param retry_delay: Delay between retries in seconds
    :param headers: Optional headers to include in the request
    :return: File content as bytes, or None if download failed
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            return response.content
        except (ConnectionError, Timeout, ReadTimeout):
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return None
        except requests.HTTPError:
            return None

    return None


def download_image(
    url: str,
    timeout: int = 10,
    max_retries: int = 2,
    retry_delay: int = 10,
    headers: dict | None = None,
) -> Image.Image | None:
    """
    Downloads an image from a URL and returns a PIL Image object.

    :param url: URL to download image from
    :param timeout: Request timeout in seconds
    :param max_retries: Maximum number of retry attempts
    :param retry_delay: Delay between retries in seconds
    :param headers: Optional headers to include in the request
    :return: PIL Image object, or None if download failed
    """
    content = download_file(url, timeout, max_retries, retry_delay, headers)

    if content is None:
        return None

    try:
        return Image.open(BytesIO(content))
    except UnidentifiedImageError:
        return None


def make_json_request(
    url: str,
    timeout: int = 30,
    max_retries: int = 5,
    retry_delay: int = 10,
    headers: dict | None = None,
    params: dict | None = None,
) -> dict | None:
    """
    Makes a GET request expecting JSON response with retry logic.

    :param url: URL to request
    :param timeout: Request timeout in seconds
    :param max_retries: Maximum number of retry attempts
    :param retry_delay: Delay between retries in seconds
    :param headers: Optional headers to include in the request
    :param params: Optional query parameters
    :return: JSON response as dict, or None if request failed
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(
                url, timeout=timeout, headers=headers, params=params
            )
            response.raise_for_status()
            return response.json()
        except (ConnectionError, Timeout, ReadTimeout):
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return None
        except requests.JSONDecodeError:
            return None
        except requests.HTTPError:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                return None

    return None


def get_random_user_agent() -> str:
    """
    Returns a random user agent string for web requests.

    :return: Random user agent string
    """
    return random.choice(DEFAULT_USER_AGENTS)
