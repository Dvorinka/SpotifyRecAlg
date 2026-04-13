-- +goose Up
create table if not exists tracks (
    id text primary key,
    title text not null,
    artist text not null,
    album text not null default '',
    genres jsonb not null default '[]'::jsonb,
    release_date text not null default '',
    duration_ms integer not null default 0 check (duration_ms >= 0),
    popularity double precision not null default 0 check (popularity >= 0 and popularity <= 1),
    explicit boolean not null default false,
    features jsonb not null,
    external jsonb not null default '{}'::jsonb,
    commercial_boost double precision not null default 0 check (commercial_boost >= 0 and commercial_boost <= 1),
    quality_penalty double precision not null default 0 check (quality_penalty >= 0 and quality_penalty <= 1),
    discovery_allowed boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists interactions (
    id bigserial primary key,
    user_id text not null,
    track_id text not null references tracks(id) on delete cascade,
    type text not null check (type in ('play', 'skip', 'like', 'dislike', 'save', 'hide')),
    weight double precision not null default 0,
    occurred_at timestamptz not null default now(),
    context jsonb not null default '{}'::jsonb,
    completed_ms integer not null default 0 check (completed_ms >= 0)
);

create table if not exists user_controls (
    user_id text primary key,
    allow_explicit boolean not null default true,
    excluded_tracks jsonb not null default '[]'::jsonb,
    excluded_artists jsonb not null default '[]'::jsonb,
    excluded_genres jsonb not null default '[]'::jsonb,
    postponed_tracks jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists tracks_artist_idx on tracks (artist);
create index if not exists tracks_popularity_idx on tracks (popularity desc);
create index if not exists tracks_genres_gin_idx on tracks using gin (genres);
create index if not exists interactions_user_time_idx on interactions (user_id, occurred_at desc);
create index if not exists interactions_track_time_idx on interactions (track_id, occurred_at desc);
create index if not exists interactions_recent_idx on interactions (occurred_at desc);

-- +goose Down
drop table if exists user_controls;
drop table if exists interactions;
drop table if exists tracks;
