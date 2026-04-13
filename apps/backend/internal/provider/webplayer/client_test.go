package webplayer

import (
	"os"
	"testing"
)

func TestParseSpotifyURLVariants(t *testing.T) {
	tests := []struct {
		name     string
		url      string
		wantType string
		wantID   string
	}{
		{name: "open URL", url: "https://open.spotify.com/track/7tFiyTwD0nx5a1eklYtX2J?si=ignored", wantType: "track", wantID: "7tFiyTwD0nx5a1eklYtX2J"},
		{name: "intl URL", url: "https://open.spotify.com/intl-cs/album/1GbtB4zTqAsyfZEsm1RZfx", wantType: "album", wantID: "1GbtB4zTqAsyfZEsm1RZfx"},
		{name: "URI", url: "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", wantType: "playlist", wantID: "37i9dQZF1DXcBWIGoYBM5M"},
		{name: "embed URI", url: "https://embed.spotify.com/?uri=spotify:track:7tFiyTwD0nx5a1eklYtX2J", wantType: "track", wantID: "7tFiyTwD0nx5a1eklYtX2J"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			itemType, itemID, err := ParseSpotifyURL(tt.url)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			if itemType != tt.wantType || itemID != tt.wantID {
				t.Fatalf("got type=%q id=%q, want type=%q id=%q", itemType, itemID, tt.wantType, tt.wantID)
			}
		})
	}
}

// TestWebPlayerIntegration tests against real Spotify endpoints
// Run with: go test -v -run TestWebPlayerIntegration ./... -tags=integration
// Or set WEBPLAYER_TEST=1 environment variable
func TestWebPlayerIntegration(t *testing.T) {
	if os.Getenv("WEBPLAYER_TEST") == "" {
		t.Skip("Skipping integration test. Set WEBPLAYER_TEST=1 to run")
	}

	client := NewClient()

	t.Run("GetTrack", func(t *testing.T) {
		// Test with "Bohemian Rhapsody" - a well-known track
		track, err := client.GetTrack("7tFiyTwD0nx5a1eklYtX2J")
		if err != nil {
			t.Fatalf("GetTrack failed: %v", err)
		}

		if track.ID == "" {
			t.Error("track ID is empty")
		}
		if track.Name == "" {
			t.Error("track name is empty")
		}
		if len(track.Artists) == 0 {
			t.Error("no artists found")
		}
		if track.Album.Name == "" {
			t.Error("album name is empty")
		}

		t.Logf("Got track: %s by %s (%d artists) from album %s, duration=%dms",
			track.Name,
			track.Artists[0].Name,
			len(track.Artists),
			track.Album.Name,
			track.DurationMs,
		)
	})

	t.Run("Search", func(t *testing.T) {
		tracks, err := client.Search("Bohemian Rhapsody Queen", 5)
		if err != nil {
			t.Fatalf("Search failed: %v", err)
		}

		if len(tracks) == 0 {
			t.Error("no tracks found in search results")
		}

		for i, track := range tracks {
			t.Logf("Result %d: %s by %s", i+1, track.Name, track.Artists[0].Name)
		}
	})

	t.Run("ParseSpotifyURL", func(t *testing.T) {
		tests := []struct {
			url      string
			wantType string
			wantID   string
		}{
			{
				url:      "https://open.spotify.com/track/7tFiyTwD0nx5a1eklYtX2J",
				wantType: "track",
				wantID:   "7tFiyTwD0nx5a1eklYtX2J",
			},
			{
				url:      "https://open.spotify.com/album/1GbtB4zTqAsyfZEsm1RZfx",
				wantType: "album",
				wantID:   "1GbtB4zTqAsyfZEsm1RZfx",
			},
		}

		for _, tt := range tests {
			itemType, itemID, err := ParseSpotifyURL(tt.url)
			if err != nil {
				t.Errorf("ParseSpotifyURL(%q) error: %v", tt.url, err)
				continue
			}
			if itemType != tt.wantType {
				t.Errorf("ParseSpotifyURL(%q) type = %q, want %q", tt.url, itemType, tt.wantType)
			}
			if itemID != tt.wantID {
				t.Errorf("ParseSpotifyURL(%q) ID = %q, want %q", tt.url, itemID, tt.wantID)
			}
		}
	})
}

// TestTOTPGeneration verifies TOTP generation produces valid codes
func TestTOTPGeneration(t *testing.T) {
	totp := generateTOTP()

	// TOTP should be 6 digits
	if len(totp) != 6 {
		t.Errorf("TOTP length = %d, want 6", len(totp))
	}

	// Should only contain digits
	for _, c := range totp {
		if c < '0' || c > '9' {
			t.Errorf("TOTP contains non-digit character: %c", c)
		}
	}

	t.Logf("Generated TOTP: %s", totp)
}
