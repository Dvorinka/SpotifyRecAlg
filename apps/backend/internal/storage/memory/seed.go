package memory

import (
	"context"
	"time"

	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
)

func SeedDemo(store *Store) {
	ctx := context.Background()
	now := time.Now().UTC()
	tracks := []recommendation.Track{
		{ID: "trk-neon-dawn", Title: "Neon Dawn", Artist: "The Arrays", Genres: []string{"synthpop", "indie"}, Popularity: 0.72, Features: recommendation.AudioFeatures{Danceability: 0.74, Energy: 0.70, Loudness: -6, Speechiness: 0.05, Acousticness: 0.12, Instrumentalness: 0.15, Liveness: 0.12, Valence: 0.67, Tempo: 118, TimeSignature: 4, Key: 2, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-static-heart", Title: "Static Heart", Artist: "The Arrays", Genres: []string{"synthpop"}, Popularity: 0.62, Features: recommendation.AudioFeatures{Danceability: 0.68, Energy: 0.66, Loudness: -8, Speechiness: 0.04, Acousticness: 0.18, Instrumentalness: 0.20, Liveness: 0.10, Valence: 0.58, Tempo: 116, TimeSignature: 4, Key: 4, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-glass-road", Title: "Glass Road", Artist: "North Index", Genres: []string{"indie", "rock"}, Popularity: 0.51, Features: recommendation.AudioFeatures{Danceability: 0.55, Energy: 0.78, Loudness: -5, Speechiness: 0.06, Acousticness: 0.20, Instrumentalness: 0.08, Liveness: 0.18, Valence: 0.54, Tempo: 132, TimeSignature: 4, Key: 9, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-late-platform", Title: "Late Platform", Artist: "North Index", Genres: []string{"indie", "ambient"}, Popularity: 0.37, Features: recommendation.AudioFeatures{Danceability: 0.44, Energy: 0.38, Loudness: -13, Speechiness: 0.03, Acousticness: 0.66, Instrumentalness: 0.70, Liveness: 0.09, Valence: 0.35, Tempo: 92, TimeSignature: 4, Key: 11, Mode: 0}, DiscoveryAllowed: true},
		{ID: "trk-velvet-motor", Title: "Velvet Motor", Artist: "Signal Choir", Genres: []string{"pop-punk", "rock"}, Popularity: 0.49, Features: recommendation.AudioFeatures{Danceability: 0.60, Energy: 0.88, Loudness: -4, Speechiness: 0.07, Acousticness: 0.09, Instrumentalness: 0.02, Liveness: 0.21, Valence: 0.62, Tempo: 148, TimeSignature: 4, Key: 5, Mode: 1}, DiscoveryAllowed: true, CommercialBoost: 0.02},
		{ID: "trk-blue-hour", Title: "Blue Hour", Artist: "Mira Vale", Genres: []string{"acoustic", "folk"}, Popularity: 0.43, Features: recommendation.AudioFeatures{Danceability: 0.38, Energy: 0.31, Loudness: -14, Speechiness: 0.04, Acousticness: 0.86, Instrumentalness: 0.12, Liveness: 0.11, Valence: 0.42, Tempo: 82, TimeSignature: 4, Key: 7, Mode: 0}, DiscoveryAllowed: true},
	}
	_ = store.UpsertTracks(ctx, tracks)
	for _, interaction := range []recommendation.Interaction{
		{UserID: "demo-user", TrackID: "trk-neon-dawn", Type: recommendation.InteractionLike, OccurredAt: now.Add(-24 * time.Hour)},
		{UserID: "demo-user", TrackID: "trk-static-heart", Type: recommendation.InteractionSave, OccurredAt: now.Add(-48 * time.Hour)},
		{UserID: "demo-user", TrackID: "trk-blue-hour", Type: recommendation.InteractionSkip, OccurredAt: now.Add(-12 * time.Hour)},
		{UserID: "neighbor-a", TrackID: "trk-neon-dawn", Type: recommendation.InteractionLike, OccurredAt: now.Add(-72 * time.Hour)},
		{UserID: "neighbor-a", TrackID: "trk-glass-road", Type: recommendation.InteractionLike, OccurredAt: now.Add(-24 * time.Hour)},
		{UserID: "neighbor-b", TrackID: "trk-static-heart", Type: recommendation.InteractionLike, OccurredAt: now.Add(-72 * time.Hour)},
		{UserID: "neighbor-b", TrackID: "trk-velvet-motor", Type: recommendation.InteractionLike, OccurredAt: now.Add(-18 * time.Hour)},
	} {
		_ = store.RecordInteraction(ctx, interaction)
	}
}
