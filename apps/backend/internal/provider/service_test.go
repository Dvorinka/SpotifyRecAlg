package provider_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/musicbrainz"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/songlink"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/spotify"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/webplayer"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/storage/memory"
)

func TestImportSpotifyTrackPersistsRecommendableTrack(t *testing.T) {
	store := memory.New()
	spotifyServer := fakeSpotifyServer(t)
	defer spotifyServer.Close()
	mbServer := fakeMusicBrainzServer(t)
	defer mbServer.Close()

	service := provider.NewService(store,
		spotify.New(spotify.Config{BearerToken: "token", APIBaseURL: spotifyServer.URL + "/v1"}),
		webplayer.NewClient(),
		songlink.NewClient(),
		musicbrainz.New(musicbrainz.Config{AppName: "SpotifyRecAlg", Contact: "test@example.com", BaseURL: mbServer.URL + "/ws/2", MinDelay: time.Nanosecond}),
		provider.ServiceConfig{DefaultMarket: "US", CacheTTL: time.Hour},
	)

	resp, err := service.ImportSpotify(context.Background(), provider.ImportRequest{
		Source:            provider.Source{Type: "url", Value: "https://open.spotify.com/track/good"},
		Market:            "US",
		EnrichMusicBrainz: boolPtr(true),
	})
	if err != nil {
		t.Fatalf("import spotify: %v", err)
	}
	if resp.ImportedTracks != 1 || resp.Skipped != 0 {
		t.Fatalf("unexpected import response: %+v", resp)
	}

	engine := recommendation.NewEngine(recommendation.EngineConfig{
		ContentWeight:     0.5,
		PopularityWeight:  0.2,
		ExplorationWeight: 0.3,
		DiversityLambda:   0.7,
	})
	recs, _, err := engine.Recommend(context.Background(), store, recommendation.RecommendRequest{UserID: "user", Limit: 1})
	if err != nil {
		t.Fatalf("recommend after import: %v", err)
	}
	if len(recs) != 1 || recs[0].Track.ID != "spotify:track:good" {
		t.Fatalf("unexpected recommendations: %+v", recs)
	}
	if got := recs[0].Track.External["musicbrainz_recording_id"]; got != "mb-recording" {
		t.Fatalf("musicbrainz recording id = %q", got)
	}
}

func boolPtr(value bool) *bool {
	return &value
}

func TestSearchSpotifyCapsLimitAndPersistFalse(t *testing.T) {
	store := memory.New()
	spotifyServer := fakeSpotifyServer(t)
	defer spotifyServer.Close()

	service := provider.NewService(store,
		spotify.New(spotify.Config{BearerToken: "token", APIBaseURL: spotifyServer.URL + "/v1"}),
		webplayer.NewClient(),
		songlink.NewClient(),
		nil,
		provider.ServiceConfig{DefaultMarket: "US", CacheTTL: time.Hour},
	)

	resp, err := service.SearchSpotify(context.Background(), provider.SearchRequest{Query: "hello", Type: "track", Limit: 50, Persist: false})
	if err != nil {
		t.Fatalf("search spotify: %v", err)
	}
	if len(resp.Tracks) != 1 || resp.Persisted != 0 {
		t.Fatalf("unexpected search response: %+v", resp)
	}
	if _, _, err := recommendation.NewEngine(recommendation.EngineConfig{}).Recommend(context.Background(), store, recommendation.RecommendRequest{UserID: "user", Limit: 1}); err == nil {
		t.Fatal("expected empty catalog because persist=false")
	}
}

func TestProviderCacheUsesStaleOnError(t *testing.T) {
	store := memory.New()
	spotifyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "upstream down", http.StatusInternalServerError)
	}))
	defer spotifyServer.Close()
	service := provider.NewService(store,
		spotify.New(spotify.Config{BearerToken: "token", APIBaseURL: spotifyServer.URL + "/v1", MaxRetries: 1}),
		webplayer.NewClient(),
		songlink.NewClient(),
		nil,
		provider.ServiceConfig{DefaultMarket: "US", CacheTTL: time.Hour},
	)

	now := time.Now().UTC()
	trackPayload := []byte(`{"id":"cached","name":"Cached","artists":[{"name":"Artist"}],"album":{"name":"Album"},"popularity":50}`)
	if err := store.UpsertProviderCache(context.Background(), provider.CacheEntry{
		Provider:  provider.ProviderSpotify,
		ItemType:  "track",
		ItemID:    "cached",
		Market:    "US",
		Payload:   trackPayload,
		FetchedAt: now.Add(-2 * time.Hour),
		ExpiresAt: now.Add(-time.Hour),
	}); err != nil {
		t.Fatalf("upsert track cache: %v", err)
	}
	featuresPayload := []byte(`{"danceability":0.5,"energy":0.6,"loudness":-7,"speechiness":0.03,"acousticness":0.2,"instrumentalness":0,"liveness":0.1,"valence":0.4,"tempo":100,"time_signature":4,"key":1,"mode":1}`)
	if err := store.UpsertProviderCache(context.Background(), provider.CacheEntry{
		Provider:  provider.ProviderSpotify,
		ItemType:  "audio_features",
		ItemID:    "cached",
		Payload:   featuresPayload,
		FetchedAt: now.Add(-2 * time.Hour),
		ExpiresAt: now.Add(-time.Hour),
	}); err != nil {
		t.Fatalf("upsert features cache: %v", err)
	}

	resp, err := service.ImportSpotify(context.Background(), provider.ImportRequest{
		Source: provider.Source{Type: "url", Value: "https://open.spotify.com/track/cached"},
		Market: "US",
	})
	if err != nil {
		t.Fatalf("import with stale cache: %v", err)
	}
	if resp.ImportedTracks != 1 || len(resp.Warnings) == 0 {
		t.Fatalf("expected stale fallback import with warning, got %+v", resp)
	}
}

func fakeSpotifyServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/v1/search":
			if got := r.URL.Query().Get("limit"); got != "10" {
				t.Fatalf("search limit = %q, want 10", got)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"tracks": map[string]any{"items": []map[string]any{{"id": "good"}}},
			})
		case "/v1/tracks/good":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id":           "good",
				"name":         "Good Song",
				"artists":      []map[string]any{{"id": "spotify-artist", "name": "Good Artist"}},
				"album":        map[string]any{"id": "album", "name": "Good Album", "release_date": "2024-01-01", "images": []map[string]any{{"url": "https://img.example/good.jpg"}}},
				"duration_ms":  210000,
				"popularity":   80,
				"explicit":     false,
				"external_ids": map[string]string{"isrc": "USRC17607839"},
				"external_urls": map[string]string{
					"spotify": "https://open.spotify.com/track/good",
				},
			})
		case "/v1/audio-features/good":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"danceability":     0.7,
				"energy":           0.8,
				"loudness":         -5.0,
				"speechiness":      0.04,
				"acousticness":     0.1,
				"instrumentalness": 0.0,
				"liveness":         0.12,
				"valence":          0.6,
				"tempo":            120,
				"time_signature":   4,
				"key":              2,
				"mode":             1,
			})
		default:
			http.NotFound(w, r)
		}
	}))
}

func fakeMusicBrainzServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("User-Agent"); got == "" {
			t.Fatal("missing User-Agent")
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/ws/2/isrc/USRC17607839":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"recordings": []map[string]any{{
					"id":    "mb-recording",
					"title": "Good Song",
					"artist-credit": []map[string]any{{
						"artist": map[string]string{"id": "mb-artist", "name": "Good Artist"},
					}},
					"isrcs": []string{"USRC17607839"},
					"tags":  []map[string]string{{"name": "indie"}},
				}},
			})
		default:
			http.NotFound(w, r)
		}
	}))
}
