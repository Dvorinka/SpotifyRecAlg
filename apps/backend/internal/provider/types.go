package provider

import (
	"context"
	"time"

	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
)

const (
	ProviderSpotify     = "spotify"
	ProviderMusicBrainz = "musicbrainz"
)

type Source struct {
	Type  string `json:"type" binding:"required"`
	Value string `json:"value" binding:"required"`
}

type ImportRequest struct {
	Source             Source `json:"source" binding:"required"`
	Market             string `json:"market,omitempty"`
	Limit              int    `json:"limit,omitempty"`
	EnrichMusicBrainz  *bool  `json:"enrich_musicbrainz,omitempty"`
	Persist            *bool  `json:"persist,omitempty"`
	AllowMissingFields bool   `json:"allow_missing_features,omitempty"`
}

type SearchRequest struct {
	Query              string `json:"query" binding:"required"`
	Type               string `json:"type,omitempty"`
	Market             string `json:"market,omitempty"`
	Limit              int    `json:"limit,omitempty"`
	Persist            bool   `json:"persist"`
	EnrichMusicBrainz  *bool  `json:"enrich_musicbrainz,omitempty"`
	AllowMissingFields bool   `json:"allow_missing_features,omitempty"`
}

type EnrichRequest struct {
	TrackIDs []string `json:"track_ids" binding:"required"`
	Force    bool     `json:"force"`
}

type ImportResponse struct {
	ImportID       string   `json:"import_id"`
	ImportedTracks int      `json:"imported_tracks"`
	UpdatedTracks  int      `json:"updated_tracks"`
	Skipped        int      `json:"skipped"`
	Warnings       []string `json:"warnings"`
}

type SearchResponse struct {
	Tracks    []recommendation.Track `json:"tracks"`
	Persisted int                    `json:"persisted"`
	Skipped   int                    `json:"skipped"`
	Warnings  []string               `json:"warnings"`
}

type EnrichResponse struct {
	Updated  int      `json:"updated"`
	Skipped  int      `json:"skipped"`
	Warnings []string `json:"warnings"`
}

type StatusResponse struct {
	Spotify     ProviderStatus `json:"spotify"`
	MusicBrainz ProviderStatus `json:"musicbrainz"`
	Cache       CacheStats     `json:"cache"`
}

type ProviderStatus struct {
	Configured bool      `json:"configured"`
	TokenMode  string    `json:"token_mode,omitempty"`
	Available  bool      `json:"available"`
	LastError  string    `json:"last_error,omitempty"`
	CheckedAt  time.Time `json:"checked_at"`
}

type CacheEntry struct {
	Provider  string
	ItemType  string
	ItemID    string
	Market    string
	Payload   []byte
	FetchedAt time.Time
	ExpiresAt time.Time
	LastError string
}

func (e CacheEntry) Fresh(now time.Time) bool {
	return len(e.Payload) > 0 && now.Before(e.ExpiresAt)
}

type CacheStats struct {
	Entries      int64 `json:"entries"`
	FreshEntries int64 `json:"fresh_entries"`
	StaleEntries int64 `json:"stale_entries"`
}

type ImportJob struct {
	ID             string
	Provider       string
	SourceType     string
	SourceValue    string
	Market         string
	Status         string
	ImportedTracks int
	UpdatedTracks  int
	Skipped        int
	Warnings       []string
	StartedAt      time.Time
	FinishedAt     time.Time
}

type TrackEnrichment struct {
	TrackID                string
	Provider               string
	MusicBrainzRecordingID string
	MusicBrainzArtistID    string
	ISRC                   string
	Payload                []byte
	UpdatedAt              time.Time
}

type Store interface {
	UpsertTrack(ctx context.Context, track recommendation.Track) error
	UpsertTracks(ctx context.Context, tracks []recommendation.Track) error
	GetTracksByIDs(ctx context.Context, ids []string) ([]recommendation.Track, error)
	GetProviderCache(ctx context.Context, providerName, itemType, itemID, market string) (CacheEntry, bool, error)
	UpsertProviderCache(ctx context.Context, entry CacheEntry) error
	ProviderCacheStats(ctx context.Context) (CacheStats, error)
	CreateImportJob(ctx context.Context, job ImportJob) error
	FinishImportJob(ctx context.Context, job ImportJob) error
	UpsertTrackEnrichment(ctx context.Context, enrichment TrackEnrichment) error
}
