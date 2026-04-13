package recommendation

import (
	"context"
	"testing"
	"time"
)

type testProvider struct {
	snapshot CatalogSnapshot
}

func (p testProvider) Snapshot(context.Context, string) (CatalogSnapshot, error) {
	return p.snapshot, nil
}

func TestRecommendBlendsContentAndCollaborativeSignals(t *testing.T) {
	now := time.Date(2026, 4, 13, 12, 0, 0, 0, time.UTC)
	engine := NewEngine(EngineConfig{
		Now:               func() time.Time { return now },
		ContentWeight:     0.44,
		CollabWeight:      0.28,
		PopularityWeight:  0.08,
		ExplorationWeight: 0.20,
		DiversityLambda:   0.74,
	})

	tracks := []Track{
		track("liked", "Known Good", "A", []string{"synth"}, 0.7, AudioFeatures{Danceability: 0.8, Energy: 0.8, Loudness: -5, Valence: 0.7, Tempo: 120, TimeSignature: 4, Key: 1, Mode: 1}),
		track("neighbor", "Neighbor Pick", "B", []string{"synth"}, 0.6, AudioFeatures{Danceability: 0.76, Energy: 0.77, Loudness: -6, Valence: 0.66, Tempo: 121, TimeSignature: 4, Key: 2, Mode: 1}),
		track("far", "Far Away", "C", []string{"folk"}, 0.5, AudioFeatures{Danceability: 0.2, Energy: 0.2, Loudness: -18, Acousticness: 0.9, Valence: 0.3, Tempo: 80, TimeSignature: 3, Key: 9, Mode: 0}),
	}

	recs, profile, err := engine.Recommend(context.Background(), testProvider{snapshot: CatalogSnapshot{
		Tracks: tracks,
		Interactions: []Interaction{
			{UserID: "u1", TrackID: "liked", Type: InteractionLike, OccurredAt: now.Add(-time.Hour)},
			{UserID: "u1", TrackID: "far", Type: InteractionSkip, OccurredAt: now.Add(-2 * time.Hour)},
			{UserID: "n1", TrackID: "liked", Type: InteractionLike, OccurredAt: now.Add(-3 * time.Hour)},
			{UserID: "n1", TrackID: "far", Type: InteractionSkip, OccurredAt: now.Add(-4 * time.Hour)},
			{UserID: "n1", TrackID: "neighbor", Type: InteractionLike, OccurredAt: now.Add(-5 * time.Hour)},
		},
		Controls: UserControls{UserID: "u1", AllowExplicit: true},
	}}, RecommendRequest{UserID: "u1", Limit: 2})
	if err != nil {
		t.Fatalf("recommend: %v", err)
	}
	if len(recs) == 0 {
		t.Fatal("expected recommendations")
	}
	if recs[0].Track.ID != "neighbor" {
		t.Fatalf("expected neighbor track first, got %q", recs[0].Track.ID)
	}
	if profile.Confidence <= 0 {
		t.Fatalf("expected non-zero confidence, got %v", profile.Confidence)
	}
}

func TestRecommendRespectsControls(t *testing.T) {
	now := time.Date(2026, 4, 13, 12, 0, 0, 0, time.UTC)
	engine := NewEngine(EngineConfig{Now: func() time.Time { return now }, ContentWeight: 1, DiversityLambda: 0.8})
	explicit := track("explicit", "Explicit", "A", []string{"pop"}, 0.5, AudioFeatures{Danceability: 0.7, Energy: 0.7, Loudness: -6, Valence: 0.7, Tempo: 120, TimeSignature: 4})
	explicit.Explicit = true
	clean := track("clean", "Clean", "A", []string{"pop"}, 0.5, AudioFeatures{Danceability: 0.69, Energy: 0.71, Loudness: -6, Valence: 0.68, Tempo: 121, TimeSignature: 4})

	recs, _, err := engine.Recommend(context.Background(), testProvider{snapshot: CatalogSnapshot{
		Tracks: []Track{
			track("seed", "Seed", "A", []string{"pop"}, 0.5, AudioFeatures{Danceability: 0.7, Energy: 0.7, Loudness: -6, Valence: 0.7, Tempo: 120, TimeSignature: 4}),
			explicit,
			clean,
		},
		Interactions: []Interaction{{UserID: "u1", TrackID: "seed", Type: InteractionLike, OccurredAt: now}},
		Controls:     UserControls{UserID: "u1", AllowExplicit: false},
	}}, RecommendRequest{UserID: "u1", Limit: 10})
	if err != nil {
		t.Fatalf("recommend: %v", err)
	}
	for _, rec := range recs {
		if rec.Track.ID == "explicit" {
			t.Fatal("explicit track should be filtered")
		}
	}
	if len(recs) != 1 || recs[0].Track.ID != "clean" {
		t.Fatalf("expected only clean track, got %#v", recs)
	}
}

func TestRecommendUsesMetadataWhenAudioFeaturesAreMissing(t *testing.T) {
	now := time.Date(2026, 4, 13, 12, 0, 0, 0, time.UTC)
	engine := NewEngine(EngineConfig{Now: func() time.Time { return now }, ContentWeight: 1, DiversityLambda: 0.85})

	recs, _, err := engine.Recommend(context.Background(), testProvider{snapshot: CatalogSnapshot{
		Tracks: []Track{
			track("seed", "Seed", "Seed Artist", []string{"synthpop"}, 0.5, AudioFeatures{}),
			track("genre-match", "Genre Match", "Other Artist", []string{"synthpop"}, 0.5, AudioFeatures{}),
			track("unrelated", "Unrelated", "Far Artist", []string{"folk"}, 0.5, AudioFeatures{}),
		},
		Controls: UserControls{UserID: "u1", AllowExplicit: true},
	}}, RecommendRequest{UserID: "u1", SeedTrackIDs: []string{"seed"}, Limit: 2})
	if err != nil {
		t.Fatalf("recommend: %v", err)
	}
	if len(recs) == 0 {
		t.Fatal("expected recommendations")
	}
	if recs[0].Track.ID != "genre-match" {
		t.Fatalf("expected metadata genre match first, got %q", recs[0].Track.ID)
	}
	for _, rec := range recs {
		if rec.Track.ID == "seed" {
			t.Fatal("seed track should not be recommended back")
		}
	}
}

func TestRecommendPenalizesSkippedNeighborhoods(t *testing.T) {
	now := time.Date(2026, 4, 13, 12, 0, 0, 0, time.UTC)
	engine := NewEngine(EngineConfig{
		Now:               func() time.Time { return now },
		ContentWeight:     0.74,
		PopularityWeight:  0.08,
		ExplorationWeight: 0.18,
		DiversityLambda:   0.9,
	})

	audio := AudioFeatures{Danceability: 0.74, Energy: 0.76, Loudness: -5, Speechiness: 0.05, Acousticness: 0.12, Instrumentalness: 0.04, Liveness: 0.12, Valence: 0.66, Tempo: 124, TimeSignature: 4, Key: 2, Mode: 1}
	recs, _, err := engine.Recommend(context.Background(), testProvider{snapshot: CatalogSnapshot{
		Tracks: []Track{
			track("liked", "Liked", "A", []string{"dance"}, 0.7, audio),
			track("skipped", "Skipped", "B", []string{"metal"}, 0.7, audio),
			track("penalized", "Penalized", "C", []string{"metal"}, 0.7, audio),
			track("safe", "Safe", "D", []string{"dance"}, 0.62, AudioFeatures{Danceability: 0.72, Energy: 0.74, Loudness: -6, Speechiness: 0.05, Acousticness: 0.14, Instrumentalness: 0.05, Liveness: 0.1, Valence: 0.64, Tempo: 125, TimeSignature: 4, Key: 3, Mode: 1}),
		},
		Interactions: []Interaction{
			{UserID: "u1", TrackID: "liked", Type: InteractionLike, OccurredAt: now.Add(-time.Hour)},
			{UserID: "u1", TrackID: "skipped", Type: InteractionSkip, OccurredAt: now.Add(-30 * time.Minute)},
		},
		Controls: UserControls{UserID: "u1", AllowExplicit: true},
	}}, RecommendRequest{UserID: "u1", Limit: 2})
	if err != nil {
		t.Fatalf("recommend: %v", err)
	}
	if len(recs) == 0 {
		t.Fatal("expected recommendations")
	}
	if recs[0].Track.ID != "safe" {
		t.Fatalf("expected non-skipped neighborhood first, got %q", recs[0].Track.ID)
	}
}

func track(id, title, artist string, genres []string, popularity float64, features AudioFeatures) Track {
	return Track{
		ID:               id,
		Title:            title,
		Artist:           artist,
		Genres:           genres,
		Popularity:       popularity,
		Features:         features,
		DiscoveryAllowed: true,
	}
}
