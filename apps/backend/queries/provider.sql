-- name: GetProviderCache :one
select provider, item_type, item_id, market, payload, fetched_at, expires_at, coalesce(last_error, '') as last_error
from provider_cache
where provider = $1 and item_type = $2 and item_id = $3 and market = $4;

-- name: UpsertProviderCache :exec
insert into provider_cache (provider, item_type, item_id, market, payload, fetched_at, expires_at, last_error)
values ($1, $2, $3, $4, $5::jsonb, $6, $7, nullif($8, ''))
on conflict (provider, item_type, item_id, market) do update set
    payload = excluded.payload,
    fetched_at = excluded.fetched_at,
    expires_at = excluded.expires_at,
    last_error = excluded.last_error;

-- name: ProviderCacheStats :one
select count(*)::bigint as entries,
       count(*) filter (where expires_at > now())::bigint as fresh_entries,
       count(*) filter (where expires_at <= now())::bigint as stale_entries
from provider_cache;

-- name: CreateImportJob :exec
insert into import_jobs (id, provider, source_type, source_value, market, status, imported_tracks, updated_tracks, skipped, warnings, started_at)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11);

-- name: FinishImportJob :exec
update import_jobs
set status = $2,
    imported_tracks = $3,
    updated_tracks = $4,
    skipped = $5,
    warnings = $6::jsonb,
    finished_at = $7
where id = $1;

-- name: UpsertTrackEnrichment :exec
insert into track_enrichment (track_id, provider, musicbrainz_recording_id, musicbrainz_artist_id, isrc, payload, updated_at)
values ($1, $2, $3, $4, $5, $6::jsonb, $7)
on conflict (track_id, provider) do update set
    musicbrainz_recording_id = excluded.musicbrainz_recording_id,
    musicbrainz_artist_id = excluded.musicbrainz_artist_id,
    isrc = excluded.isrc,
    payload = excluded.payload,
    updated_at = excluded.updated_at;
