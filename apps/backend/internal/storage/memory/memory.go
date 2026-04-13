package memory

import (
	"context"
	"slices"
	"sync"
	"time"

	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
)

type Store struct {
	mu            sync.RWMutex
	tracks        map[string]recommendation.Track
	interactions  []recommendation.Interaction
	controls      map[string]recommendation.UserControls
	providerCache map[string]provider.CacheEntry
	importJobs    map[string]provider.ImportJob
	enrichments   map[string]provider.TrackEnrichment
}

func New() *Store {
	return &Store{
		tracks:        make(map[string]recommendation.Track),
		controls:      make(map[string]recommendation.UserControls),
		providerCache: make(map[string]provider.CacheEntry),
		importJobs:    make(map[string]provider.ImportJob),
		enrichments:   make(map[string]provider.TrackEnrichment),
	}
}

func (s *Store) Ping(context.Context) error {
	return nil
}

func (s *Store) UpsertTrack(_ context.Context, track recommendation.Track) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now().UTC()
	existing := s.tracks[track.ID]
	if track.CreatedAt.IsZero() {
		track.CreatedAt = existing.CreatedAt
	}
	if track.CreatedAt.IsZero() {
		track.CreatedAt = now
	}
	track.UpdatedAt = now
	s.tracks[track.ID] = track
	return nil
}

func (s *Store) UpsertTracks(ctx context.Context, tracks []recommendation.Track) error {
	for _, track := range tracks {
		if err := s.UpsertTrack(ctx, track); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) GetTracksByIDs(_ context.Context, ids []string) ([]recommendation.Track, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]recommendation.Track, 0, len(ids))
	for _, id := range ids {
		if track, ok := s.tracks[id]; ok {
			out = append(out, track)
		}
	}
	return out, nil
}

func (s *Store) RecordInteraction(_ context.Context, interaction recommendation.Interaction) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if interaction.OccurredAt.IsZero() {
		interaction.OccurredAt = time.Now().UTC()
	}
	s.interactions = append(s.interactions, interaction)
	return nil
}

func (s *Store) GetControls(_ context.Context, userID string) (recommendation.UserControls, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	controls, ok := s.controls[userID]
	if !ok {
		return recommendation.UserControls{UserID: userID, AllowExplicit: true}, nil
	}
	return controls, nil
}

func (s *Store) UpsertControls(_ context.Context, controls recommendation.UserControls) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.controls[controls.UserID] = controls
	return nil
}

func (s *Store) Snapshot(_ context.Context, userID string) (recommendation.CatalogSnapshot, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	tracks := make([]recommendation.Track, 0, len(s.tracks))
	for _, track := range s.tracks {
		tracks = append(tracks, track)
	}
	slices.SortFunc(tracks, func(a, b recommendation.Track) int {
		if a.ID < b.ID {
			return -1
		}
		if a.ID > b.ID {
			return 1
		}
		return 0
	})

	interactions := slices.Clone(s.interactions)
	controls, ok := s.controls[userID]
	if !ok {
		controls = recommendation.UserControls{UserID: userID, AllowExplicit: true}
	}
	return recommendation.CatalogSnapshot{
		Tracks:       tracks,
		Interactions: interactions,
		Controls:     controls,
	}, nil
}

func (s *Store) GetProviderCache(_ context.Context, providerName, itemType, itemID, market string) (provider.CacheEntry, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	entry, ok := s.providerCache[providerCacheKey(providerName, itemType, itemID, market)]
	if !ok {
		return provider.CacheEntry{}, false, nil
	}
	return cloneCacheEntry(entry), true, nil
}

func (s *Store) UpsertProviderCache(_ context.Context, entry provider.CacheEntry) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.providerCache[providerCacheKey(entry.Provider, entry.ItemType, entry.ItemID, entry.Market)] = cloneCacheEntry(entry)
	return nil
}

func (s *Store) ProviderCacheStats(context.Context) (provider.CacheStats, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	now := time.Now().UTC()
	stats := provider.CacheStats{Entries: int64(len(s.providerCache))}
	for _, entry := range s.providerCache {
		if entry.Fresh(now) {
			stats.FreshEntries++
		} else {
			stats.StaleEntries++
		}
	}
	return stats, nil
}

func (s *Store) CreateImportJob(_ context.Context, job provider.ImportJob) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.importJobs[job.ID] = job
	return nil
}

func (s *Store) FinishImportJob(_ context.Context, job provider.ImportJob) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.importJobs[job.ID] = job
	return nil
}

func (s *Store) UpsertTrackEnrichment(_ context.Context, enrichment provider.TrackEnrichment) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.enrichments[enrichment.TrackID+":"+enrichment.Provider] = cloneEnrichment(enrichment)
	return nil
}

func providerCacheKey(providerName, itemType, itemID, market string) string {
	return providerName + "\x00" + itemType + "\x00" + itemID + "\x00" + market
}

func cloneCacheEntry(entry provider.CacheEntry) provider.CacheEntry {
	if len(entry.Payload) > 0 {
		entry.Payload = slices.Clone(entry.Payload)
	}
	return entry
}

func cloneEnrichment(enrichment provider.TrackEnrichment) provider.TrackEnrichment {
	if len(enrichment.Payload) > 0 {
		enrichment.Payload = slices.Clone(enrichment.Payload)
	}
	return enrichment
}
