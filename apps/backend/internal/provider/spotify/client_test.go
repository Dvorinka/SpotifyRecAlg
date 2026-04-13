package spotify

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestClientCredentialsTokenIsCached(t *testing.T) {
	var tokenRequests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/token":
			tokenRequests.Add(1)
			if got := r.Header.Get("Authorization"); !strings.HasPrefix(got, "Basic ") {
				t.Fatalf("missing basic authorization header")
			}
			_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "token-a", "expires_in": 3600, "token_type": "Bearer"})
		case "/v1/tracks/abc":
			if got := r.Header.Get("Authorization"); got != "Bearer token-a" {
				t.Fatalf("got authorization %q", got)
			}
			writeTrack(w, "abc")
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	client := New(Config{
		ClientID:        "client-id",
		ClientSecret:    "client-secret",
		AccountsBaseURL: server.URL,
		APIBaseURL:      server.URL + "/v1",
	})

	for i := 0; i < 2; i++ {
		if _, _, err := client.GetTrack(context.Background(), "abc", "US"); err != nil {
			t.Fatalf("get track %d: %v", i, err)
		}
	}
	if got := tokenRequests.Load(); got != 1 {
		t.Fatalf("token requests = %d, want 1", got)
	}
}

func TestClientRetriesRateLimitedRequest(t *testing.T) {
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/tracks/abc" {
			if calls.Add(1) == 1 {
				w.Header().Set("Retry-After", "0")
				w.WriteHeader(http.StatusTooManyRequests)
				return
			}
			writeTrack(w, "abc")
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()

	client := New(Config{BearerToken: "token", APIBaseURL: server.URL + "/v1", MaxRetries: 1})
	if _, _, err := client.GetTrack(context.Background(), "abc", "US"); err != nil {
		t.Fatalf("get track after retry: %v", err)
	}
	if got := calls.Load(); got != 2 {
		t.Fatalf("calls = %d, want 2", got)
	}
}

func TestClientReportsMalformedJSONAndContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{`))
	}))
	defer server.Close()

	client := New(Config{BearerToken: "token", APIBaseURL: server.URL, MaxRetries: 0})
	if _, _, err := client.GetTrack(context.Background(), "abc", "US"); err == nil {
		t.Fatal("expected malformed JSON error")
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, err := client.GetTrack(ctx, "abc", "US"); err == nil {
		t.Fatal("expected context cancellation error")
	}
}

func writeTrack(w http.ResponseWriter, id string) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(Track{
		ID:         id,
		Name:       "Track",
		Artists:    []Artist{{Name: "Artist"}},
		Album:      Album{Name: "Album", ReleaseDate: "2024-01-01"},
		DurationMS: int((3 * time.Minute).Milliseconds()),
		Popularity: 77,
		ExternalIDs: map[string]string{
			"isrc": "USRC17607839",
		},
		ExternalURLs: map[string]string{
			"spotify": "https://open.spotify.com/track/" + id,
		},
	})
}
