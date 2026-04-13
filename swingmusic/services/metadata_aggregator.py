"""
Enhanced Metadata Aggregation System for Universal Music Downloader
Provides cross-service matching and metadata enrichment without API keys
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CrossServiceMatch:
    """Cross-service song match information"""

    service: str
    service_id: str
    title: str
    artist: str
    url: str
    confidence: float
    isrc: str | None = None
    duration_ms: int | None = None
    release_date: str | None = None
    cover_art: str | None = None


@dataclass
class EnhancedMetadata:
    """Enhanced metadata with cross-service information"""

    primary_metadata: Any
    cross_matches: list[CrossServiceMatch]
    canonical_info: dict[str, Any] | None = None
    confidence_score: float = 0.0
    recommendations: list[str] = None


class MetadataAggregator:
    """Aggregates and enhances metadata from multiple sources"""

    def __init__(self):
        self.canonical_cache = {}
        self.artist_aliases = {}

    def normalize_title(self, title: str) -> str:
        """Normalize song title for better matching"""
        # Remove extra whitespace and convert to lowercase
        normalized = title.strip().lower()

        # Remove common prefixes and suffixes
        prefixes_to_remove = [
            "official video",
            "official audio",
            "lyrics",
            "live",
            "acoustic",
            "remastered",
        ]
        for prefix in prefixes_to_remove:
            normalized = re.sub(rf"\s*{prefix}\s*", "", normalized, flags=re.IGNORECASE)

        # Remove content in parentheses
        normalized = re.sub(r"\s*\([^)]*\)\s*", "", normalized)

        # Remove extra dashes and special characters
        normalized = re.sub(r"\s*[-–—]\s*", " ", normalized)

        return normalized.strip()

    def normalize_artist(self, artist: str) -> str:
        """Normalize artist name for better matching"""
        normalized = artist.strip().lower()

        # Remove "feat." and similar
        normalized = re.sub(r"\s*feat\.\s*", " feat. ", normalized)

        # Handle "vs" collaborations
        normalized = re.sub(r"\s+vs\s+", " vs ", normalized)

        return normalized.strip()

    def calculate_similarity_score(
        self, title1: str, artist1: str, title2: str, artist2: str
    ) -> float:
        """Calculate similarity score between two songs"""
        title_score = 0.0
        artist_score = 0.0

        # Title similarity
        if title1 and title2:
            norm_title1 = self.normalize_title(title1)
            norm_title2 = self.normalize_title(title2)

            if norm_title1 == norm_title2:
                title_score = 1.0
            elif norm_title1 in norm_title2 or norm_title2 in norm_title1:
                title_score = 0.8
            else:
                # Partial match based on words
                words1 = set(norm_title1.split())
                words2 = set(norm_title2.split())
                common_words = words1.intersection(words2)
                title_score = (
                    len(common_words) / max(len(words1), len(words2))
                    if words1 and words2
                    else 0.0
                )

        # Artist similarity
        if artist1 and artist2:
            norm_artist1 = self.normalize_artist(artist1)
            norm_artist2 = self.normalize_artist(artist2)

            if norm_artist1 == norm_artist2:
                artist_score = 1.0
            elif norm_artist1 in norm_artist2 or norm_artist2 in norm_artist1:
                artist_score = 0.8
            else:
                # Partial match based on words
                words1 = set(norm_artist1.split())
                words2 = set(norm_artist2.split())
                common_words = words1.intersection(words2)
                artist_score = (
                    len(common_words) / max(len(words1), len(words2))
                    if words1 and words2
                    else 0.0
                )

        # Combined score (title is more important)
        return title_score * 0.7 + artist_score * 0.3

    def find_cross_service_matches(
        self, primary_metadata: Any, all_services_data: dict[str, Any]
    ) -> list[CrossServiceMatch]:
        """Find matches of the same song across other services"""
        matches = []

        if not primary_metadata:
            return matches

        primary_title = getattr(primary_metadata, "title", "")
        primary_artist = getattr(primary_metadata, "artist", "")
        getattr(primary_metadata, "isrc", None)

        for service, data in all_services_data.items():
            service_attr = getattr(primary_metadata, "service", None)
            if service_attr and service == service_attr.value:
                continue  # Skip: same service

            service_title = getattr(data, "title", "")
            service_artist = getattr(data, "artist", "")
            service_url = getattr(data, "original_url", "")

            # Calculate similarity score
            similarity = self.calculate_similarity_score(
                primary_title, primary_artist, service_title, service_artist
            )

            # Only include matches with reasonable similarity
            if similarity >= 0.6:  # 60% similarity threshold
                match = CrossServiceMatch(
                    service=service,
                    service_id=getattr(data, "service_id", ""),
                    title=service_title,
                    artist=service_artist,
                    url=service_url,
                    confidence=similarity,
                    isrc=getattr(data, "isrc", None),
                    duration_ms=getattr(data, "duration_ms", None),
                    release_date=getattr(data, "release_date", None),
                    cover_art=getattr(data, "image_url", None),
                )
                matches.append(match)

        # Sort by confidence score
        matches.sort(key=lambda x: x.confidence, reverse=True)
        return matches

    def get_canonical_info(self, isrc: str) -> dict[str, Any] | None:
        """Get canonical information from ISRC"""
        if not isrc or len(isrc) != 12:
            return None

        # Parse ISRC: Country-Registration Year-Sequence Number
        country = isrc[:2]
        year = isrc[2:6]
        sequence = isrc[6:]

        return {
            "isrc": isrc,
            "country": country,
            "year": year,
            "sequence": sequence,
            "type": "recording" if sequence.isdigit() else "other",
        }

    def generate_recommendations(
        self, metadata: Any, cross_matches: list[CrossServiceMatch]
    ) -> list[str]:
        """Generate recommendations based on metadata and cross matches"""
        recommendations = []

        # Base recommendations on genre
        genre = getattr(metadata, "genre", "")
        if genre:
            recommendations.append(f"Similar {genre} tracks")

        # Add recommendations from high-confidence cross matches
        high_confidence_matches = [m for m in cross_matches if m.confidence >= 0.8]
        for match in high_confidence_matches[:3]:  # Top 3 matches
            recommendations.append(f"Also available on {match.service}")

        # Add recommendations based on artist
        artist = getattr(metadata, "artist", "")
        if artist:
            recommendations.append(f"More from {artist}")

        return list(set(recommendations))  # Remove duplicates

    def create_enhanced_metadata(
        self, primary_metadata: Any, cross_matches: list[CrossServiceMatch]
    ) -> EnhancedMetadata:
        """Create enhanced metadata object"""
        # Calculate confidence score
        max_confidence = (
            max([m.confidence for m in cross_matches]) if cross_matches else 0.0
        )

        # Get canonical info if ISRC exists
        canonical_info = None
        isrc = getattr(primary_metadata, "isrc", None)
        if isrc:
            canonical_info = self.get_canonical_info(isrc)

        # Generate recommendations
        recommendations = self.generate_recommendations(primary_metadata, cross_matches)

        return EnhancedMetadata(
            primary_metadata=primary_metadata,
            cross_matches=cross_matches,
            canonical_info=canonical_info,
            confidence_score=max_confidence,
            recommendations=recommendations,
        )


class FreeMetadataEnricher:
    """Free metadata enrichment without API keys"""

    def __init__(self):
        self.aggregator = MetadataAggregator()

    def extract_lyrics_snippet(self, title: str, artist: str) -> str:
        """Extract potential lyrics snippet for search enhancement"""
        # This would use web scraping of lyrics sites
        # For now, return empty to avoid copyright issues
        return ""

    def detect_language(self, title: str, artist: str) -> str:
        """Detect likely language from title and artist"""
        # Simple heuristic based on character patterns
        if any(ord(c) > 127 for c in title + artist):
            return "non-english"
        return "english"

    def estimate_mood(self, title: str, artist: str) -> str:
        """Estimate mood from title and artist name"""
        title_lower = title.lower()
        artist_lower = artist.lower()

        mood_keywords = {
            "happy": ["love", "joy", "sun", "summer", "dance", "party"],
            "sad": ["cry", "tears", "rain", "winter", "goodbye", "broken"],
            "energetic": ["rock", "power", "energy", "loud", "fast"],
            "calm": ["peace", "quiet", "soft", "gentle", "acoustic"],
            "dark": ["dark", "death", "black", "night", "shadow"],
        }

        for mood, keywords in mood_keywords.items():
            if any(
                keyword in title_lower or keyword in artist_lower
                for keyword in keywords
            ):
                return mood

        return "neutral"

    def calculate_quality_score(self, metadata: Any) -> float:
        """Calculate metadata quality score"""
        score = 0.0

        # Check for ISRC (high quality indicator)
        if getattr(metadata, "isrc", None):
            score += 0.3

        # Check for release date
        if getattr(metadata, "release_date", None):
            score += 0.2

        # Check for genre information
        if getattr(metadata, "genre", None):
            score += 0.2

        # Check for cover art
        if getattr(metadata, "image_url", None):
            score += 0.1

        # Check for duration
        if getattr(metadata, "duration_ms", None):
            score += 0.1

        # Check for extended metadata
        if getattr(metadata, "metadata", None):
            score += 0.1

        return min(score, 1.0)


# Global instances
metadata_aggregator = MetadataAggregator()
free_enricher = FreeMetadataEnricher()
