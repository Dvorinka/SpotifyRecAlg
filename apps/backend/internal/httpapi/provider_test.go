package httpapi

import (
	"bytes"
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
	"go.uber.org/zap"
)

func TestSpotifyImportEndpoint(t *testing.T) {
	store := memory.New()
	spotifyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/v1/tracks/imported":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"id":           "imported",
				"name":         "Imported",
				"artists":      []map[string]string{{"name": "Artist"}},
				"album":        map[string]any{"name": "Album", "release_date": "2025-01-01"},
				"duration_ms":  180000,
				"popularity":   60,
				"external_ids": map[string]string{"isrc": "USRC17607839"},
			})
		case "/v1/audio-features/imported":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"danceability": 0.6, "energy": 0.7, "loudness": -6, "speechiness": 0.05,
				"acousticness": 0.2, "instrumentalness": 0, "liveness": 0.1, "valence": 0.5,
				"tempo": 110, "time_signature": 4, "key": 5, "mode": 1,
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer spotifyServer.Close()

	service := provider.NewService(store,
		spotify.New(spotify.Config{BearerToken: "secret-token", APIBaseURL: spotifyServer.URL + "/v1"}),
		webplayer.NewClient(),
		songlink.NewClient(),
		musicbrainz.New(musicbrainz.Config{AppName: "SpotifyRecAlg", Contact: "test@example.com", BaseURL: "http://127.0.0.1:1", MinDelay: time.Nanosecond}),
		provider.ServiceConfig{DefaultMarket: "US", CacheTTL: time.Hour},
	)

	router := NewRouter(RouterConfig{
		Store:    store,
		Engine:   recommendation.NewEngine(recommendation.EngineConfig{}),
		Provider: service,
		Logger:   zap.NewNop(),
	})

	body := bytes.NewBufferString(`{"source":{"type":"url","value":"https://open.spotify.com/track/imported"},"market":"US","persist":true}`)
	req := httptest.NewRequest(http.MethodPost, "/v1/providers/spotify/import", body)
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", rec.Code, rec.Body.String())
	}
	var resp provider.ImportResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if resp.ImportedTracks != 1 {
		t.Fatalf("imported tracks = %d, want 1", resp.ImportedTracks)
	}

	req = httptest.NewRequest(http.MethodGet, "/v1/providers/status", nil)
	rec = httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status endpoint = %d body=%s", rec.Code, rec.Body.String())
	}
	if bytes.Contains(rec.Body.Bytes(), []byte("secret-token")) {
		t.Fatal("status response leaked bearer token")
	}
}
