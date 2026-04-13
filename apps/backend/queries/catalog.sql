-- name: UpsertTrack :exec
insert into tracks (
    id, title, artist, album, genres, release_date, duration_ms, popularity,
    explicit, features, external, commercial_boost, quality_penalty, discovery_allowed
) values (
    $1, $2, $3, $4, $5::jsonb, $6, $7, $8,
    $9, $10::jsonb, $11::jsonb, $12, $13, $14
)
on conflict (id) do update set
    title = excluded.title,
    artist = excluded.artist,
    album = excluded.album,
    genres = excluded.genres,
    release_date = excluded.release_date,
    duration_ms = excluded.duration_ms,
    popularity = excluded.popularity,
    explicit = excluded.explicit,
    features = excluded.features,
    external = excluded.external,
    commercial_boost = excluded.commercial_boost,
    quality_penalty = excluded.quality_penalty,
    discovery_allowed = excluded.discovery_allowed,
    updated_at = now();

-- name: ListTracks :many
select id, title, artist, album, genres, release_date, duration_ms, popularity,
    explicit, features, external, created_at, updated_at, commercial_boost, quality_penalty, discovery_allowed
from tracks
order by id;

-- name: GetTracksByIDs :many
select id, title, artist, album, genres, release_date, duration_ms, popularity,
    explicit, features, external, created_at, updated_at, commercial_boost, quality_penalty, discovery_allowed
from tracks
where id = any($1::text[])
order by id;

-- name: RecordInteraction :exec
insert into interactions (user_id, track_id, type, weight, occurred_at, context, completed_ms)
values ($1, $2, $3, $4, $5, $6::jsonb, $7);

-- name: ListRecentInteractions :many
select user_id, track_id, type, weight, occurred_at, context, completed_ms
from interactions
where occurred_at >= now() - interval '365 days'
order by occurred_at desc
limit 250000;

-- name: GetControls :one
select user_id, allow_explicit, excluded_tracks, excluded_artists, excluded_genres, postponed_tracks
from user_controls
where user_id = $1;

-- name: UpsertControls :exec
insert into user_controls (user_id, allow_explicit, excluded_tracks, excluded_artists, excluded_genres, postponed_tracks)
values ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb)
on conflict (user_id) do update set
    allow_explicit = excluded.allow_explicit,
    excluded_tracks = excluded.excluded_tracks,
    excluded_artists = excluded.excluded_artists,
    excluded_genres = excluded.excluded_genres,
    postponed_tracks = excluded.postponed_tracks,
    updated_at = now();
