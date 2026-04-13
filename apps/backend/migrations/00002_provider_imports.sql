-- +goose Up
create table if not exists provider_cache (
    provider text not null,
    item_type text not null,
    item_id text not null,
    market text not null default '',
    payload jsonb not null default '{}'::jsonb,
    fetched_at timestamptz not null default now(),
    expires_at timestamptz not null,
    last_error text,
    primary key (provider, item_type, item_id, market)
);

create table if not exists import_jobs (
    id text primary key,
    provider text not null,
    source_type text not null,
    source_value text not null,
    market text not null default '',
    status text not null check (status in ('running', 'succeeded', 'failed')),
    imported_tracks integer not null default 0 check (imported_tracks >= 0),
    updated_tracks integer not null default 0 check (updated_tracks >= 0),
    skipped integer not null default 0 check (skipped >= 0),
    warnings jsonb not null default '[]'::jsonb,
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create table if not exists track_enrichment (
    track_id text not null references tracks(id) on delete cascade,
    provider text not null,
    musicbrainz_recording_id text not null default '',
    musicbrainz_artist_id text not null default '',
    isrc text not null default '',
    payload jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    primary key (track_id, provider)
);

create index if not exists provider_cache_expiry_idx on provider_cache (expires_at);
create index if not exists import_jobs_provider_started_idx on import_jobs (provider, started_at desc);
create index if not exists track_enrichment_isrc_idx on track_enrichment (isrc) where isrc <> '';
create index if not exists tracks_external_gin_idx on tracks using gin (external);

-- +goose Down
drop index if exists tracks_external_gin_idx;
drop table if exists track_enrichment;
drop table if exists import_jobs;
drop table if exists provider_cache;
