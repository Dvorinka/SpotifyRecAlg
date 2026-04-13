package postgres

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/storage/postgres/db"
)

type Store struct {
	pool    *pgxpool.Pool
	queries *db.Queries
}

func New(pool *pgxpool.Pool) *Store {
	return &Store{pool: pool, queries: db.New(pool)}
}

func (s *Store) Ping(ctx context.Context) error {
	return s.pool.Ping(ctx)
}

func (s *Store) UpsertTrack(ctx context.Context, track recommendation.Track) error {
	params, err := upsertTrackParams(track)
	if err != nil {
		return err
	}
	return s.queries.UpsertTrack(ctx, params)
}

func (s *Store) UpsertTracks(ctx context.Context, tracks []recommendation.Track) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	queries := s.queries.WithTx(tx)
	for _, track := range tracks {
		params, err := upsertTrackParams(track)
		if err != nil {
			return err
		}
		if err := queries.UpsertTrack(ctx, params); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func (s *Store) GetTracksByIDs(ctx context.Context, ids []string) ([]recommendation.Track, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	rows, err := s.pool.Query(ctx, `
select id, title, artist, album, genres, release_date, duration_ms, popularity,
    explicit, features, external, created_at, updated_at, commercial_boost, quality_penalty, discovery_allowed
from tracks
where id = any($1)
order by id`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	tracks := make([]recommendation.Track, 0, len(ids))
	for rows.Next() {
		track, err := scanTrack(rows)
		if err != nil {
			return nil, err
		}
		tracks = append(tracks, track)
	}
	return tracks, rows.Err()
}

func upsertTrackParams(track recommendation.Track) (db.UpsertTrackParams, error) {
	features, err := json.Marshal(track.Features)
	if err != nil {
		return db.UpsertTrackParams{}, fmt.Errorf("marshal features: %w", err)
	}
	genres, err := json.Marshal(track.Genres)
	if err != nil {
		return db.UpsertTrackParams{}, fmt.Errorf("marshal genres: %w", err)
	}
	external, err := json.Marshal(track.External)
	if err != nil {
		return db.UpsertTrackParams{}, fmt.Errorf("marshal external ids: %w", err)
	}
	return db.UpsertTrackParams{
		ID:               track.ID,
		Title:            track.Title,
		Artist:           track.Artist,
		Album:            track.Album,
		Column5:          genres,
		ReleaseDate:      track.ReleaseDate,
		DurationMs:       int32(track.DurationMS),
		Popularity:       track.Popularity,
		Explicit:         track.Explicit,
		Column10:         features,
		Column11:         external,
		CommercialBoost:  track.CommercialBoost,
		QualityPenalty:   track.QualityPenalty,
		DiscoveryAllowed: track.DiscoveryAllowed,
	}, nil
}

func (s *Store) RecordInteraction(ctx context.Context, interaction recommendation.Interaction) error {
	if interaction.OccurredAt.IsZero() {
		interaction.OccurredAt = time.Now().UTC()
	}
	contextJSON, err := json.Marshal(interaction.Context)
	if err != nil {
		return fmt.Errorf("marshal interaction context: %w", err)
	}
	return s.queries.RecordInteraction(ctx, db.RecordInteractionParams{
		UserID:      interaction.UserID,
		TrackID:     interaction.TrackID,
		Type:        string(interaction.Type),
		Weight:      interaction.Weight,
		OccurredAt:  pgtype.Timestamptz{Time: interaction.OccurredAt, Valid: true},
		Column6:     contextJSON,
		CompletedMs: int32(interaction.CompletedMS),
	})
}

func (s *Store) GetControls(ctx context.Context, userID string) (recommendation.UserControls, error) {
	row, err := s.queries.GetControls(ctx, userID)
	if errors.Is(err, pgx.ErrNoRows) {
		return recommendation.UserControls{UserID: userID, AllowExplicit: true}, nil
	}
	if err != nil {
		return recommendation.UserControls{}, err
	}
	controls := recommendation.UserControls{UserID: row.UserID, AllowExplicit: row.AllowExplicit}
	if err := unmarshalStringSlice(row.ExcludedTracks, &controls.ExcludedTracks); err != nil {
		return recommendation.UserControls{}, err
	}
	if err := unmarshalStringSlice(row.ExcludedArtists, &controls.ExcludedArtists); err != nil {
		return recommendation.UserControls{}, err
	}
	if err := unmarshalStringSlice(row.ExcludedGenres, &controls.ExcludedGenres); err != nil {
		return recommendation.UserControls{}, err
	}
	if err := unmarshalStringSlice(row.PostponedTracks, &controls.PostponedTracks); err != nil {
		return recommendation.UserControls{}, err
	}
	return controls, nil
}

func (s *Store) UpsertControls(ctx context.Context, controls recommendation.UserControls) error {
	excludedTracks, err := json.Marshal(controls.ExcludedTracks)
	if err != nil {
		return err
	}
	excludedArtists, err := json.Marshal(controls.ExcludedArtists)
	if err != nil {
		return err
	}
	excludedGenres, err := json.Marshal(controls.ExcludedGenres)
	if err != nil {
		return err
	}
	postponedTracks, err := json.Marshal(controls.PostponedTracks)
	if err != nil {
		return err
	}

	return s.queries.UpsertControls(ctx, db.UpsertControlsParams{
		UserID:        controls.UserID,
		AllowExplicit: controls.AllowExplicit,
		Column3:       excludedTracks,
		Column4:       excludedArtists,
		Column5:       excludedGenres,
		Column6:       postponedTracks,
	})
}

func (s *Store) Snapshot(ctx context.Context, userID string) (recommendation.CatalogSnapshot, error) {
	tracks, err := s.listTracks(ctx)
	if err != nil {
		return recommendation.CatalogSnapshot{}, err
	}
	interactions, err := s.listRecentInteractions(ctx)
	if err != nil {
		return recommendation.CatalogSnapshot{}, err
	}
	controls, err := s.GetControls(ctx, userID)
	if err != nil {
		return recommendation.CatalogSnapshot{}, err
	}
	return recommendation.CatalogSnapshot{
		Tracks:       tracks,
		Interactions: interactions,
		Controls:     controls,
	}, nil
}

func (s *Store) listTracks(ctx context.Context) ([]recommendation.Track, error) {
	rows, err := s.queries.ListTracks(ctx)
	if err != nil {
		return nil, err
	}
	tracks := make([]recommendation.Track, 0, len(rows))
	for _, row := range rows {
		track, err := trackFromListRow(row)
		if err != nil {
			return nil, err
		}
		tracks = append(tracks, track)
	}
	return tracks, nil
}

func trackFromListRow(row db.ListTracksRow) (recommendation.Track, error) {
	track := recommendation.Track{
		ID:               row.ID,
		Title:            row.Title,
		Artist:           row.Artist,
		Album:            row.Album,
		ReleaseDate:      row.ReleaseDate,
		DurationMS:       int(row.DurationMs),
		Popularity:       row.Popularity,
		Explicit:         row.Explicit,
		CreatedAt:        row.CreatedAt.Time,
		UpdatedAt:        row.UpdatedAt.Time,
		CommercialBoost:  row.CommercialBoost,
		QualityPenalty:   row.QualityPenalty,
		DiscoveryAllowed: row.DiscoveryAllowed,
	}
	if err := unmarshalStringSlice(row.Genres, &track.Genres); err != nil {
		return recommendation.Track{}, err
	}
	if err := json.Unmarshal(row.Features, &track.Features); err != nil {
		return recommendation.Track{}, err
	}
	if err := unmarshalStringMap(row.External, &track.External); err != nil {
		return recommendation.Track{}, err
	}
	return track, nil
}

type rowScanner interface {
	Scan(dest ...any) error
}

func scanTrack(scanner rowScanner) (recommendation.Track, error) {
	var (
		genres, features, external []byte
		createdAt, updatedAt       pgtype.Timestamptz
		track                      recommendation.Track
	)
	if err := scanner.Scan(
		&track.ID,
		&track.Title,
		&track.Artist,
		&track.Album,
		&genres,
		&track.ReleaseDate,
		&track.DurationMS,
		&track.Popularity,
		&track.Explicit,
		&features,
		&external,
		&createdAt,
		&updatedAt,
		&track.CommercialBoost,
		&track.QualityPenalty,
		&track.DiscoveryAllowed,
	); err != nil {
		return recommendation.Track{}, err
	}
	track.CreatedAt = createdAt.Time
	track.UpdatedAt = updatedAt.Time
	if err := unmarshalStringSlice(genres, &track.Genres); err != nil {
		return recommendation.Track{}, err
	}
	if err := json.Unmarshal(features, &track.Features); err != nil {
		return recommendation.Track{}, err
	}
	if err := unmarshalStringMap(external, &track.External); err != nil {
		return recommendation.Track{}, err
	}
	return track, nil
}

func (s *Store) GetProviderCache(ctx context.Context, providerName, itemType, itemID, market string) (provider.CacheEntry, bool, error) {
	var entry provider.CacheEntry
	err := s.pool.QueryRow(ctx, `
select provider, item_type, item_id, market, payload, fetched_at, expires_at, coalesce(last_error, '')
from provider_cache
where provider = $1 and item_type = $2 and item_id = $3 and market = $4`,
		providerName, itemType, itemID, market,
	).Scan(&entry.Provider, &entry.ItemType, &entry.ItemID, &entry.Market, &entry.Payload, &entry.FetchedAt, &entry.ExpiresAt, &entry.LastError)
	if errors.Is(err, pgx.ErrNoRows) {
		return provider.CacheEntry{}, false, nil
	}
	if err != nil {
		return provider.CacheEntry{}, false, err
	}
	return entry, true, nil
}

func (s *Store) UpsertProviderCache(ctx context.Context, entry provider.CacheEntry) error {
	_, err := s.pool.Exec(ctx, `
insert into provider_cache (provider, item_type, item_id, market, payload, fetched_at, expires_at, last_error)
values ($1, $2, $3, $4, $5::jsonb, $6, $7, nullif($8, ''))
on conflict (provider, item_type, item_id, market) do update set
    payload = excluded.payload,
    fetched_at = excluded.fetched_at,
    expires_at = excluded.expires_at,
    last_error = excluded.last_error`,
		entry.Provider,
		entry.ItemType,
		entry.ItemID,
		entry.Market,
		emptyObjectIfNil(entry.Payload),
		entry.FetchedAt,
		entry.ExpiresAt,
		entry.LastError,
	)
	return err
}

func (s *Store) ProviderCacheStats(ctx context.Context) (provider.CacheStats, error) {
	var stats provider.CacheStats
	err := s.pool.QueryRow(ctx, `
select count(*)::bigint,
       count(*) filter (where expires_at > now())::bigint,
       count(*) filter (where expires_at <= now())::bigint
from provider_cache`,
	).Scan(&stats.Entries, &stats.FreshEntries, &stats.StaleEntries)
	return stats, err
}

func (s *Store) CreateImportJob(ctx context.Context, job provider.ImportJob) error {
	warnings, err := json.Marshal(job.Warnings)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `
insert into import_jobs (id, provider, source_type, source_value, market, status, imported_tracks, updated_tracks, skipped, warnings, started_at)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)`,
		job.ID, job.Provider, job.SourceType, job.SourceValue, job.Market, job.Status,
		job.ImportedTracks, job.UpdatedTracks, job.Skipped, warnings, job.StartedAt,
	)
	return err
}

func (s *Store) FinishImportJob(ctx context.Context, job provider.ImportJob) error {
	warnings, err := json.Marshal(job.Warnings)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `
update import_jobs
set status = $2,
    imported_tracks = $3,
    updated_tracks = $4,
    skipped = $5,
    warnings = $6::jsonb,
    finished_at = $7
where id = $1`,
		job.ID, job.Status, job.ImportedTracks, job.UpdatedTracks, job.Skipped, warnings, job.FinishedAt,
	)
	return err
}

func (s *Store) UpsertTrackEnrichment(ctx context.Context, enrichment provider.TrackEnrichment) error {
	_, err := s.pool.Exec(ctx, `
insert into track_enrichment (track_id, provider, musicbrainz_recording_id, musicbrainz_artist_id, isrc, payload, updated_at)
values ($1, $2, $3, $4, $5, $6::jsonb, $7)
on conflict (track_id, provider) do update set
    musicbrainz_recording_id = excluded.musicbrainz_recording_id,
    musicbrainz_artist_id = excluded.musicbrainz_artist_id,
    isrc = excluded.isrc,
    payload = excluded.payload,
    updated_at = excluded.updated_at`,
		enrichment.TrackID,
		enrichment.Provider,
		enrichment.MusicBrainzRecordingID,
		enrichment.MusicBrainzArtistID,
		enrichment.ISRC,
		emptyObjectIfNil(enrichment.Payload),
		enrichment.UpdatedAt,
	)
	return err
}

func emptyObjectIfNil(payload []byte) []byte {
	if len(payload) == 0 {
		return []byte(`{}`)
	}
	return payload
}

func (s *Store) listRecentInteractions(ctx context.Context) ([]recommendation.Interaction, error) {
	rows, err := s.queries.ListRecentInteractions(ctx)
	if err != nil {
		return nil, err
	}
	interactions := make([]recommendation.Interaction, 0, len(rows))
	for _, row := range rows {
		interaction := recommendation.Interaction{
			UserID:      row.UserID,
			TrackID:     row.TrackID,
			Type:        recommendation.InteractionType(row.Type),
			Weight:      row.Weight,
			OccurredAt:  row.OccurredAt.Time,
			CompletedMS: int(row.CompletedMs),
		}
		if len(row.Context) > 0 {
			if err := json.Unmarshal(row.Context, &interaction.Context); err != nil {
				return nil, err
			}
		}
		interactions = append(interactions, interaction)
	}
	return interactions, nil
}

func unmarshalStringSlice(raw []byte, out *[]string) error {
	if len(raw) == 0 {
		*out = nil
		return nil
	}
	return json.Unmarshal(raw, out)
}

func unmarshalStringMap(raw []byte, out *map[string]string) error {
	if len(raw) == 0 || string(raw) == "null" {
		*out = nil
		return nil
	}
	return json.Unmarshal(raw, out)
}
