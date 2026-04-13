from __future__ import annotations

from sqlalchemy import and_, func, select

from swingmusic.db.engine import DbEngine
from swingmusic.db.userdata import ScrobbleTable, SimilarArtistTable
from swingmusic.models.track import Track
from swingmusic.store.tracks import TrackStore


def _deterministic_float_from_hash(value: str) -> float:
    # Stable pseudo-random signal for deterministic cold-start ranking.
    if not value:
        return 0.0

    try:
        sample = int(value[:8], 16)
    except ValueError:
        sample = sum(ord(ch) for ch in value)

    return (sample % 1000) / 1000.0


def _get_user_track_play_counts(trackhashes: set[str], userid: int) -> dict[str, int]:
    if not trackhashes:
        return {}

    with DbEngine.manager() as conn:
        result = conn.execute(
            select(ScrobbleTable.trackhash, func.count(ScrobbleTable.id).label("plays"))
            .where(
                and_(
                    ScrobbleTable.userid == userid,
                    ScrobbleTable.trackhash.in_(trackhashes),
                )
            )
            .group_by(ScrobbleTable.trackhash)
        )
        rows = result.fetchall()

    return {row.trackhash: int(row.plays) for row in rows}


def rank_tracks_for_user(tracks: list[Track], userid: int) -> list[Track]:
    if not tracks:
        return []

    trackhashes = {track.trackhash for track in tracks}
    play_counts = _get_user_track_play_counts(trackhashes, userid)

    max_bitrate = max((track.bitrate for track in tracks), default=1)
    max_play_count = max(play_counts.values(), default=0)

    # Approximate recency from date tag where present (fallback to deterministic signal).
    dates = [track.date for track in tracks if track.date and track.date > 0]
    min_date = min(dates) if dates else 0
    max_date = max(dates) if dates else 0

    def base_score(track: Track) -> float:
        bitrate_score = (track.bitrate / max_bitrate) if max_bitrate else 0.0

        if max_date > min_date and track.date:
            recency_score = (track.date - min_date) / (max_date - min_date)
        else:
            recency_score = _deterministic_float_from_hash(track.trackhash)

        variety = _deterministic_float_from_hash(track.trackhash[::-1])
        return (0.55 * bitrate_score) + (0.25 * recency_score) + (0.20 * variety)

    def final_score(track: Track) -> float:
        base = base_score(track)

        if max_play_count <= 0:
            return base

        user_signal = play_counts.get(track.trackhash, 0) / max_play_count
        return (0.65 * user_signal) + (0.35 * base)

    return sorted(tracks, key=final_score, reverse=True)


def _dedupe_tracks(tracks: list[Track]) -> list[Track]:
    seen = set()
    deduped: list[Track] = []

    for track in tracks:
        if track.trackhash in seen:
            continue

        seen.add(track.trackhash)
        deduped.append(track)

    return deduped


def build_artist_recommendations(
    artisthash: str, userid: int
) -> dict[str, list[Track]]:
    source_tracks = TrackStore.get_tracks_by_artisthash(artisthash)
    source_tracks = _dedupe_tracks(source_tracks)

    this_is_tracks = rank_tracks_for_user(source_tracks, userid)[:40]

    radio_candidates: list[Track] = []

    similar = SimilarArtistTable.get_by_hash(artisthash)
    if similar:
        for similar_hash in similar.get_artist_hash_set():
            if similar_hash == artisthash:
                continue

            candidate_tracks = TrackStore.get_tracks_by_artisthash(similar_hash)
            candidate_tracks = rank_tracks_for_user(
                _dedupe_tracks(candidate_tracks), userid
            )
            radio_candidates.extend(candidate_tracks[:6])

    if not radio_candidates:
        fallback = TrackStore.get_flat_list()
        fallback = [track for track in fallback if artisthash not in track.artisthashes]
        fallback = rank_tracks_for_user(_dedupe_tracks(fallback), userid)
        radio_candidates = fallback[:90]

    radio_tracks = rank_tracks_for_user(_dedupe_tracks(radio_candidates), userid)[:50]

    return {
        "this_is": this_is_tracks,
        "artist_radio": radio_tracks,
    }
