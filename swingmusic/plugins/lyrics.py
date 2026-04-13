import json
import time
from pathlib import Path

import requests
from unidecode import unidecode

from swingmusic.db.userdata import PluginTable
from swingmusic.plugins import Plugin, plugin_method
from swingmusic.settings import Paths


class LRCProvider:
    """Base class for synced (LRC format) lyrics providers."""

    session = requests.Session()

    def __init__(self) -> None:
        self.session.headers.update(
            {
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                )
            }
        )

    def get_lrc_by_id(self, track_id: str) -> str | None:
        raise NotImplementedError

    def get_lrc(self, title: str, artist: str, album: str = "") -> list[dict]:
        raise NotImplementedError


class LRCLibProvider(LRCProvider):
    """LRCLIB-first provider (SpotiFLAC-style exact->fallback search strategy)."""

    ROOT_URL = "https://lrclib.net/api"

    def __init__(self) -> None:
        super().__init__()
        self._by_id_cache: dict[str, str] = {}

    def _get_json(self, endpoint: str, params: dict) -> dict | list | None:
        try:
            response = self.session.get(
                f"{self.ROOT_URL}/{endpoint}",
                params=params,
                timeout=10,
            )
            if not response.ok:
                return None
            return response.json()
        except Exception:
            return None

    def _entry_to_lrc(self, entry: dict) -> str | None:
        synced = (entry.get("syncedLyrics") or "").strip()
        plain = (entry.get("plainLyrics") or "").strip()

        if synced:
            return synced
        if plain:
            return plain
        return None

    def _to_result(self, entry: dict) -> dict | None:
        lrc = self._entry_to_lrc(entry)
        if not lrc:
            return None

        track_id = str(entry.get("id") or "")
        provider_track_id = f"lrclib:{track_id}" if track_id else f"lrclib:{hash(lrc)}"
        self._by_id_cache[provider_track_id] = lrc

        return {
            "track_id": provider_track_id,
            "title": entry.get("trackName", ""),
            "artist": entry.get("artistName", ""),
            "album": entry.get("albumName", ""),
            "image": None,
            "provider": "lrclib",
            "lrc": lrc,
        }

    def get_lrc_by_id(self, track_id: str) -> str | None:
        return self._by_id_cache.get(track_id)

    def get_lrc(self, title: str, artist: str, album: str = "") -> list[dict]:
        if not title or not artist:
            return []

        results: list[dict] = []

        # 1) Exact lookup including album when available.
        if album:
            exact_with_album = self._get_json(
                "get",
                {
                    "artist_name": artist,
                    "track_name": title,
                    "album_name": album,
                },
            )
            if isinstance(exact_with_album, dict):
                result = self._to_result(exact_with_album)
                if result:
                    results.append(result)

        # 2) Exact lookup without album.
        if not results:
            exact = self._get_json(
                "get",
                {
                    "artist_name": artist,
                    "track_name": title,
                },
            )
            if isinstance(exact, dict):
                result = self._to_result(exact)
                if result:
                    results.append(result)

        # 3) Search fallback.
        if not results:
            search_data = self._get_json(
                "search",
                {
                    "artist_name": artist,
                    "track_name": title,
                },
            )
            if isinstance(search_data, list):
                for entry in search_data:
                    result = self._to_result(entry)
                    if result:
                        results.append(result)

        return results


class MusixmatchProvider(LRCProvider):
    """Musixmatch provider class."""

    ROOT_URL = "https://apic-desktop.musixmatch.com/ws/1.1/"

    def __init__(self) -> None:
        super().__init__()
        self.token = None
        self.session.headers.update(
            {
                "authority": "apic-desktop.musixmatch.com",
                "cookie": "AWSELBCORS=0; AWSELB=0",
            }
        )

    def _get(self, action: str, query: list[tuple]):
        if action != "token.get" and self.token is None:
            self._get_token()

        query.append(("app_id", "web-desktop-app-v1.0"))
        if self.token is not None:
            query.append(("usertoken", self.token))

        t = str(int(time.time() * 1000))
        query.append(("t", t))

        try:
            url = self.ROOT_URL + action
        except TypeError:
            return None

        try:
            response = self.session.get(url, params=query, timeout=10)
        except Exception:
            return None

        if response is not None and response.ok:
            return response

        return None

    def _get_token(self):
        plugin_path = Paths().lyrics_plugins_path
        token_path = plugin_path / "token.json"

        current_time = int(time.time())

        if token_path.exists():
            with token_path.open(mode="r", encoding="utf-8") as token_file:
                cached_token_data: dict = json.load(token_file)

            cached_token = cached_token_data.get("token")
            expiration_time = cached_token_data.get("expiration_time")

            if cached_token and expiration_time and current_time < expiration_time:
                self.token = cached_token
                return

        res = self._get("token.get", [("user_language", "en")])

        if res is None:
            return

        res = res.json()
        if res["message"]["header"]["status_code"] == 401:
            time.sleep(13)
            return self._get_token()

        new_token = res["message"]["body"]["user_token"]
        expiration_time = current_time + 600

        self.token = new_token
        token_data = {"token": new_token, "expiration_time": expiration_time}

        plugin_path.mkdir(parents=True, exist_ok=True)
        with token_path.open("w", encoding="utf-8") as token_file:
            json.dump(token_data, token_file)

    def get_lrc_by_id(self, track_id: str) -> str | None:
        res = self._get(
            "track.subtitle.get",
            [("track_id", track_id), ("subtitle_format", "lrc")],
        )

        try:
            res = res.json()
            body = res["message"]["body"]
        except AttributeError:
            return None

        if not body:
            return None

        return body["subtitle"]["subtitle_body"]

    def get_lrc(self, title: str, artist: str, album: str = "") -> list[dict]:
        res = self._get(
            "track.search",
            [
                ("q_track", title),
                ("q_artist", artist),
                ("page_size", "5"),
                ("page", "1"),
                ("f_has_lyrics", "1"),
                ("s_track_rating", "desc"),
                ("quorum_factor", "1.0"),
            ],
        )

        try:
            body = res.json()["message"]["body"]
        except AttributeError:
            return []

        try:
            tracks = body["track_list"]
        except TypeError:
            return []

        if not tracks:
            decoded = unidecode(artist)
            if decoded == artist:
                return []
            return self.get_lrc(title, decoded, album)

        return [
            {
                "track_id": str(t["track"]["track_id"]),
                "title": t["track"]["track_name"],
                "artist": t["track"]["artist_name"],
                "album": t["track"]["album_name"],
                "image": t["track"]["album_coverart_100x100"],
                "provider": "musixmatch",
            }
            for t in tracks
        ]


class Lyrics(Plugin):
    def __init__(self) -> None:
        plugin = PluginTable.get_by_name("lyrics_finder")
        if not plugin:
            return

        super().__init__(plugin.name, "Lyrics finder")

        self.providers: list[LRCProvider] = [LRCLibProvider(), MusixmatchProvider()]
        self._search_cache: dict[str, str] = {}

        self.set_active(bool(int(plugin.active)))

    @staticmethod
    def _write_lrc(path: str, lrc: str) -> None:
        output = Path(path).with_suffix(".lrc")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(lrc, encoding="utf-8")

    @plugin_method
    def search_lyrics_by_title_and_artist(self, title: str, artist: str):
        album = ""
        results: list[dict] = []
        seen = set()

        for provider in self.providers:
            try:
                provider_results = provider.get_lrc(title, artist, album)
            except TypeError:
                provider_results = provider.get_lrc(title, artist)  # type: ignore[misc]

            for item in provider_results:
                if not item:
                    continue

                dedupe_key = (
                    (item.get("title") or "").strip().lower(),
                    (item.get("artist") or "").strip().lower(),
                    (item.get("album") or "").strip().lower(),
                )
                if dedupe_key in seen:
                    continue

                seen.add(dedupe_key)

                track_id = str(item.get("track_id", "")).strip()
                lrc = item.get("lrc")
                if track_id and lrc:
                    self._search_cache[track_id] = lrc

                results.append(item)

        return results

    @plugin_method
    def download_lyrics(self, trackid: str, path: str):
        lrc = self._search_cache.get(trackid)

        if not lrc:
            for provider in self.providers:
                lrc = provider.get_lrc_by_id(trackid)
                if lrc:
                    break

        if lrc is None:
            return None
        if len(lrc.replace("\n", "").strip()) < 1:
            return None

        self._write_lrc(path, lrc)
        return lrc

    @plugin_method
    def download_lyrics_by_metadata(
        self,
        title: str,
        artist: str,
        path: str,
        album: str = "",
    ):
        if not title or not artist:
            return None

        for provider in self.providers:
            try:
                provider_results = provider.get_lrc(title, artist, album)
            except TypeError:
                provider_results = provider.get_lrc(title, artist)  # type: ignore[misc]

            for item in provider_results:
                lrc = item.get("lrc")
                if not lrc:
                    track_id = str(item.get("track_id", ""))
                    if track_id:
                        lrc = provider.get_lrc_by_id(track_id)

                if not lrc or len(lrc.replace("\n", "").strip()) < 1:
                    continue

                self._write_lrc(path, lrc)
                return lrc

        return None
