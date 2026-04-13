package spotify

import "testing"

func TestParseSource(t *testing.T) {
	tests := []struct {
		name       string
		sourceType string
		value      string
		wantType   string
		wantID     string
		wantErr    bool
	}{
		{name: "track URL", sourceType: "url", value: "https://open.spotify.com/track/abc123XYZ?si=ignored", wantType: "track", wantID: "abc123XYZ"},
		{name: "intl track URL", sourceType: "url", value: "https://open.spotify.com/intl-cs/track/7tFiyTwD0nx5a1eklYtX2J?si=ignored", wantType: "track", wantID: "7tFiyTwD0nx5a1eklYtX2J"},
		{name: "embed URI URL", sourceType: "url", value: "https://embed.spotify.com/?uri=spotify:track:7tFiyTwD0nx5a1eklYtX2J", wantType: "track", wantID: "7tFiyTwD0nx5a1eklYtX2J"},
		{name: "album URI", sourceType: "url", value: "spotify:album:album123456", wantType: "album", wantID: "album123456"},
		{name: "album URL with inferred type", sourceType: "", value: "https://open.spotify.com/album/1GbtB4zTqAsyfZEsm1RZfx", wantType: "album", wantID: "1GbtB4zTqAsyfZEsm1RZfx"},
		{name: "playlist URL", sourceType: "playlist", value: "https://open.spotify.com/playlist/pl123456", wantType: "playlist", wantID: "pl123456"},
		{name: "legacy user playlist URL", sourceType: "url", value: "https://open.spotify.com/user/someone/playlist/pl123456", wantType: "playlist", wantID: "pl123456"},
		{name: "artist ID", sourceType: "artist", value: "artist123456", wantType: "artist", wantID: "artist123456"},
		{name: "invalid URL", sourceType: "url", value: "https://example.com/track/abc", wantErr: true},
		{name: "type mismatch", sourceType: "track", value: "https://open.spotify.com/album/abc123456", wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := ParseSource(tt.sourceType, tt.value)
			if tt.wantErr {
				if err == nil {
					t.Fatal("expected error")
				}
				return
			}
			if err != nil {
				t.Fatalf("parse source: %v", err)
			}
			if got.Type != tt.wantType || got.ID != tt.wantID {
				t.Fatalf("got type=%q id=%q, want type=%q id=%q", got.Type, got.ID, tt.wantType, tt.wantID)
			}
		})
	}
}
