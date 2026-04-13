"""
Enhanced Album Grouper for SwingMusic
Handles proper album grouping with various artists, compilations, and metadata normalization
"""

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from swingmusic import logger
from swingmusic.db.sqlite.utils import get_db_connection


@dataclass
class AlbumGroupingKey:
    """Key for album grouping with normalization"""

    normalized_artist: str
    normalized_album: str
    year: str | None
    is_compilation: bool
    album_type: str  # album, single, compilation, etc.


@dataclass
class AlbumInfo:
    """Enhanced album information"""

    album_id: str
    title: str
    artists: list[str]
    primary_artist: str
    year: str | None
    album_type: str
    is_compilation: bool
    track_count: int
    total_duration: int
    image_url: str | None
    folder_path: str
    grouping_key: str


class MetadataNormalizer:
    """Normalizes metadata for consistent grouping"""

    # Common variations that should be normalized
    ARTIST_VARIATIONS = {
        "various artists": ["various artists", "va", "various", "multiple artists"],
        "soundtrack": [
            "soundtrack",
            "ost",
            "original soundtrack",
            "original sound track",
        ],
        "various": ["various", "various artists", "va"],
    }

    # Words to remove for better matching
    STOP_WORDS = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "for",
        "nor",
        "so",
        "yet",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "with",
        "about",
        "as",
    }

    # Patterns to clean up
    CLEANUP_PATTERNS = [
        r"\[.*?\]",  # Remove brackets and content
        r"\(.*?\)",  # Remove parentheses and content
        r"\{.*?\}",  # Remove braces and content
        r"<.*?>",  # Remove angle brackets and content
        r" feat\. .*",  # Remove featuring info
        r" ft\. .*",  # Remove featuring info
        r" featuring .*",  # Remove featuring info
    ]

    @classmethod
    def normalize_string(cls, text: str) -> str:
        """Normalize string for comparison"""
        if not text:
            return ""

        # Convert to lowercase and normalize unicode
        text = unicodedata.normalize("NFKD", text.lower())

        # Remove accents and diacritics
        text = "".join(c for c in text if not unicodedata.combining(c))

        # Apply cleanup patterns
        for pattern in cls.CLEANUP_PATTERNS:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE)

        # Remove extra whitespace and punctuation
        text = re.sub(r"[^\w\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # Remove stop words (optional for album names)
        # words = text.split()
        # text = ' '.join(word for word in words if word not in cls.STOP_WORDS)

        return text

    @classmethod
    def normalize_artist(cls, artist: str) -> str:
        """Normalize artist name for grouping"""
        if not artist:
            return ""

        normalized = cls.normalize_string(artist)

        # Handle common variations
        for standard, variations in cls.ARTIST_VARIATIONS.items():
            if normalized in variations:
                return standard

        return normalized

    @classmethod
    def normalize_album(cls, album: str) -> str:
        """Normalize album name for grouping"""
        return cls.normalize_string(album)

    @classmethod
    def extract_year(cls, date_str: str) -> str | None:
        """Extract year from date string"""
        if not date_str:
            return None

        # Look for 4-digit year patterns
        year_match = re.search(r"\b(19|20)\d{2}\b", date_str)
        if year_match:
            return year_match.group()

        return None

    @classmethod
    def is_compilation(cls, artists: list[str], albumartist: str = None) -> bool:
        """Determine if album is a compilation"""
        if not artists:
            return False

        # Check if albumartist is "Various Artists"
        if albumartist:
            normalized_albumartist = cls.normalize_artist(albumartist)
            if normalized_albumartist in ["various artists", "va", "various"]:
                return True

        # Check if there are many different artists
        unique_artists = {cls.normalize_artist(artist) for artist in artists}

        # If more than 3 unique artists, likely a compilation
        if len(unique_artists) > 3:
            return True

        # Check for common compilation indicators
        album_lower = " ".join(artists).lower()
        compilation_indicators = [
            "various artists",
            "soundtrack",
            "ost",
            "compilation",
            "various",
            "multiple artists",
            "collection",
            "greatest hits",
        ]

        return any(indicator in album_lower for indicator in compilation_indicators)


class ArtistAliasResolver:
    """Resolves artist aliases to canonical names"""

    def __init__(self):
        self.aliases: dict[str, str] = {}
        self._load_common_aliases()

    def _load_common_aliases(self):
        """Load common artist aliases"""
        # Common artist name variations
        common_aliases = {
            "taylor swift": ["t. swift", "taylor", "swift"],
            "the beatles": ["beatles", "the fab four"],
            "led zeppelin": ["zeppelin", "led zep"],
            "pink floyd": ["floyd"],
            "the rolling stones": ["rolling stones", "stones"],
            "bob dylan": ["dylan", "bobby dylan"],
            "david bowie": ["bowie", "ziggy stardust"],
            # Add more as needed
        }

        for canonical, aliases in common_aliases.items():
            for alias in aliases:
                self.aliases[MetadataNormalizer.normalize_string(alias)] = canonical

    def resolve_alias(self, artist: str) -> str:
        """Resolve artist alias to canonical name"""
        normalized = MetadataNormalizer.normalize_string(artist)
        return self.aliases.get(normalized, artist)

    def add_alias(self, canonical: str, alias: str):
        """Add a new artist alias"""
        normalized_alias = MetadataNormalizer.normalize_string(alias)
        self.aliases[normalized_alias] = canonical


class AlbumGrouper:
    """Enhanced album grouping with proper normalization"""

    def __init__(self):
        self.metadata_normalizer = MetadataNormalizer()
        self.alias_resolver = ArtistAliasResolver()
        self.grouping_cache: dict[str, AlbumGroupingKey] = {}

    def normalize_album_artist(self, track_metadata: dict[str, any]) -> str:
        """Normalize album artist for proper grouping"""
        # Try albumartist first
        albumartist = track_metadata.get("albumartist")
        if albumartist:
            normalized = self.metadata_normalizer.normalize_artist(albumartist)
            resolved = self.alias_resolver.resolve_alias(normalized)
            return resolved

        # Fall back to artist
        artist = track_metadata.get("artist")
        if artist:
            normalized = self.metadata_normalizer.normalize_artist(artist)
            resolved = self.alias_resolver.resolve_alias(normalized)
            return resolved

        return "Unknown Artist"

    def create_grouping_key(self, track_metadata: dict[str, any]) -> AlbumGroupingKey:
        """Create consistent grouping key for albums"""
        # Extract and normalize artist
        artists = self._extract_artists(track_metadata)
        primary_artist = self.normalize_album_artist(track_metadata)

        # Normalize album name
        album_name = track_metadata.get("album", "")
        normalized_album = self.metadata_normalizer.normalize_album(album_name)

        # Extract year
        release_date = track_metadata.get("date") or track_metadata.get("year")
        year = (
            self.metadata_normalizer.extract_year(str(release_date))
            if release_date
            else None
        )

        # Determine if compilation
        is_compilation = self.metadata_normalizer.is_compilation(
            artists, track_metadata.get("albumartist")
        )

        # Determine album type
        album_type = track_metadata.get("albumtype", "album")
        if is_compilation:
            album_type = "compilation"

        return AlbumGroupingKey(
            normalized_artist=primary_artist,
            normalized_album=normalized_album,
            year=year,
            is_compilation=is_compilation,
            album_type=album_type,
        )

    def create_grouping_key_string(self, track_metadata: dict[str, any]) -> str:
        """Create string-based grouping key for database storage"""
        key = self.create_grouping_key(track_metadata)

        # Include year for different editions but allow fallback
        year_part = f"::{key.year}" if key.year else ""

        # Mark compilations specially
        compilation_part = "::COMP" if key.is_compilation else ""

        return f"{key.normalized_artist}::{key.normalized_album}{year_part}{compilation_part}"

    def _extract_artists(self, track_metadata: dict[str, any]) -> list[str]:
        """Extract list of artists from track metadata"""
        artists = []

        # Try artists field (array)
        if "artists" in track_metadata:
            if isinstance(track_metadata["artists"], list):
                artists.extend(track_metadata["artists"])
            else:
                artists.append(str(track_metadata["artists"]))

        # Try artist field
        if "artist" in track_metadata:
            artist_str = track_metadata["artist"]
            if isinstance(artist_str, list):
                artists.extend(artist_str)
            else:
                # Split common separators
                for sep in [",", ";", "&", " and ", " ft ", " feat "]:
                    if sep in artist_str:
                        artists.extend([a.strip() for a in artist_str.split(sep)])
                        break
                else:
                    artists.append(artist_str)

        # Remove duplicates and empty strings
        return list(set(filter(None, artists)))

    def calculate_similarity(self, str1: str, str2: str) -> float:
        """Calculate similarity between two strings"""
        return SequenceMatcher(None, str1, str2).ratio()

    def should_group_together(
        self, key1: AlbumGroupingKey, key2: AlbumGroupingKey
    ) -> bool:
        """Determine if two albums should be grouped together"""
        # Different artists - don't group unless both are compilations
        if key1.normalized_artist != key2.normalized_artist:
            if not (key1.is_compilation and key2.is_compilation):
                return False

        # Check album name similarity
        album_similarity = self.calculate_similarity(
            key1.normalized_album, key2.normalized_album
        )
        if album_similarity < 0.8:  # 80% similarity threshold
            return False

        # If years are available, they should be close or identical
        if key1.year and key2.year and key1.year != key2.year:
            # Allow grouping if years are close (e.g., reissues)
            year_diff = abs(int(key1.year) - int(key2.year))
            if year_diff > 5:  # More than 5 years difference
                return False

        return True

    def group_albums_from_database(self) -> dict[str, list[dict[str, any]]]:
        """Group albums from database tracks"""
        try:
            with get_db_connection() as conn:
                # Get all tracks with album information
                query = """
                SELECT
                    t.trackhash,
                    t.title,
                    t.artist,
                    t.albumartist,
                    t.album,
                    t.date,
                    t.year,
                    t.albumtype,
                    t.image,
                    t.folderpath,
                    t.duration
                FROM tracks t
                WHERE t.album IS NOT NULL AND t.album != ''
                ORDER BY t.albumartist, t.album, t.date, t.tracknumber
                """

                cursor = conn.execute(query)
                tracks = cursor.fetchall()

                # Group tracks by album key
                album_groups: dict[str, list[dict[str, any]]] = {}

                for track in tracks:
                    track_dict = dict(track)

                    # Create grouping key
                    grouping_key = self.create_grouping_key_string(track_dict)

                    # Add to group
                    if grouping_key not in album_groups:
                        album_groups[grouping_key] = []

                    album_groups[grouping_key].append(track_dict)

                return album_groups

        except Exception as e:
            logger.error(f"Error grouping albums from database: {e}")
            return {}

    def create_album_info(
        self, grouping_key: str, tracks: list[dict[str, any]]
    ) -> AlbumInfo:
        """Create album info from grouped tracks"""
        if not tracks:
            raise ValueError("No tracks provided")

        first_track = tracks[0]
        key = self.create_grouping_key(first_track)

        # Extract unique artists
        all_artists = set()
        for track in tracks:
            artists = self._extract_artists(track)
            all_artists.update(artists)

        # Calculate total duration
        total_duration = sum(track.get("duration", 0) for track in tracks)

        # Get image from first track (could be enhanced to find best image)
        image_url = first_track.get("image")

        return AlbumInfo(
            album_id=grouping_key,
            title=first_track.get("album", ""),
            artists=list(all_artists),
            primary_artist=key.normalized_artist,
            year=key.year,
            album_type=key.album_type,
            is_compilation=key.is_compilation,
            track_count=len(tracks),
            total_duration=total_duration,
            image_url=image_url,
            folder_path=first_track.get("folderpath", ""),
            grouping_key=grouping_key,
        )

    def fix_album_grouping_in_database(self) -> int:
        """Fix album grouping in database and return number of fixes"""
        fixes_made = 0

        try:
            with get_db_connection() as conn:
                # Get all tracks
                cursor = conn.execute("""
                    SELECT trackhash, artist, albumartist, album, date, year, albumtype
                    FROM tracks
                    WHERE album IS NOT NULL AND album != ''
                """)

                tracks = cursor.fetchall()

                for track in tracks:
                    track_dict = dict(track)

                    # Create proper grouping key
                    self.create_grouping_key_string(track_dict)

                    # Check if we need to update albumartist
                    proper_albumartist = self.normalize_album_artist(track_dict)
                    current_albumartist = track_dict.get("albumartist", "")

                    if proper_albumartist != current_albumartist:
                        cursor = conn.execute(
                            """
                            UPDATE tracks
                            SET albumartist = ?
                            WHERE trackhash = ?
                        """,
                            (proper_albumartist, track_dict["trackhash"]),
                        )

                        fixes_made += 1
                        logger.info(
                            f"Fixed albumartist for {track_dict['trackhash']}: '{current_albumartist}' -> '{proper_albumartist}'"
                        )

                conn.commit()

        except Exception as e:
            logger.error(f"Error fixing album grouping: {e}")

        return fixes_made


# Global album grouper instance
album_grouper = AlbumGrouper()
