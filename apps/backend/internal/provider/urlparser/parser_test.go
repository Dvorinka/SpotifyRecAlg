package urlparser

import "testing"

func TestParseURLDetectsSupportedMusicLinks(t *testing.T) {
	parser := NewParser()
	tests := []struct {
		name     string
		url      string
		service  Service
		itemType string
		id       string
	}{
		{name: "spotify intl", url: "https://open.spotify.com/intl-us/track/7tFiyTwD0nx5a1eklYtX2J?si=x", service: Spotify, itemType: "track", id: "7tFiyTwD0nx5a1eklYtX2J"},
		{name: "spotify uri", url: "spotify:album:1GbtB4zTqAsyfZEsm1RZfx", service: Spotify, itemType: "album", id: "1GbtB4zTqAsyfZEsm1RZfx"},
		{name: "apple album track", url: "https://music.apple.com/us/album/example/1440857781?i=1440857782", service: AppleMusic, itemType: "song", id: "1440857782"},
		{name: "youtube music video", url: "https://music.youtube.com/watch?v=abc_DEF-123&si=x", service: YouTubeMusic, itemType: "video", id: "abc_DEF-123"},
		{name: "youtube playlist", url: "https://www.youtube.com/playlist?list=PL123", service: YouTube, itemType: "playlist", id: "PL123"},
		{name: "soundcloud set", url: "https://soundcloud.com/artist/sets/mixtape", service: SoundCloud, itemType: "playlist", id: "artist/mixtape"},
		{name: "tidal", url: "https://listen.tidal.com/browse/track/12345", service: Tidal, itemType: "track", id: "12345"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parser.ParseURL(tt.url)
			if got == nil {
				t.Fatal("expected parsed URL")
			}
			if got.Service != tt.service || got.ItemType != tt.itemType || got.ID != tt.id {
				t.Fatalf("got service=%q type=%q id=%q, want service=%q type=%q id=%q", got.Service, got.ItemType, got.ID, tt.service, tt.itemType, tt.id)
			}
		})
	}
}
