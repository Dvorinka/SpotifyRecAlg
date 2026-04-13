package provider

import (
	"strings"

	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/musicbrainz"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/spotify"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
)

func mapSpotifyTrack(track spotify.Track, features spotify.AudioFeatures, mb musicbrainz.Recording, missingFeatures bool) recommendation.Track {
	artist := ""
	if len(track.Artists) > 0 {
		artist = track.Artists[0].Name
	}

	spotifyURL := "https://open.spotify.com/track/" + track.ID
	external := map[string]string{
		"source":      ProviderSpotify,
		"spotify_id":  track.ID,
		"spotify":     spotifyURL,
		"spotify_url": spotifyURL,
	}
	if value := strings.TrimSpace(track.ExternalURLs["spotify"]); value != "" {
		external["spotify"] = value
		external["spotify_url"] = value
	}
	if isrc := strings.ToUpper(strings.TrimSpace(track.ExternalIDs["isrc"])); isrc != "" {
		external["isrc"] = isrc
	}
	if len(track.Album.Images) > 0 && track.Album.Images[0].URL != "" {
		external["image_url"] = track.Album.Images[0].URL
		external["spotify_image_url"] = track.Album.Images[0].URL
	}
	if missingFeatures {
		external["features_missing"] = "true"
	}
	if mb.ID != "" {
		external["musicbrainz_recording_id"] = mb.ID
	}
	if mb.ArtistID != "" {
		external["musicbrainz_artist_id"] = mb.ArtistID
	}
	if mb.ISRC != "" && external["isrc"] == "" {
		external["isrc"] = mb.ISRC
	}

	genres := mergeStrings(nil, mb.Genres...)
	genres = mergeStrings(genres, mb.Tags...)

	return recommendation.Track{
		ID:          "spotify:track:" + track.ID,
		Title:       track.Name,
		Artist:      artist,
		Album:       track.Album.Name,
		Genres:      genres,
		ReleaseDate: track.Album.ReleaseDate,
		DurationMS:  track.DurationMS,
		Popularity:  clamp01(float64(track.Popularity) / 100),
		Explicit:    track.Explicit,
		Features: recommendation.AudioFeatures{
			Danceability:     features.Danceability,
			Energy:           features.Energy,
			Loudness:         features.Loudness,
			Speechiness:      features.Speechiness,
			Acousticness:     features.Acousticness,
			Instrumentalness: features.Instrumentalness,
			Liveness:         features.Liveness,
			Valence:          features.Valence,
			Tempo:            features.Tempo,
			TimeSignature:    features.TimeSignature,
			Key:              features.Key,
			Mode:             features.Mode,
		},
		External:         external,
		DiscoveryAllowed: true,
	}
}

func mergeStrings(values []string, next ...string) []string {
	seen := make(map[string]struct{}, len(values)+len(next))
	out := make([]string, 0, len(values)+len(next))
	for _, value := range append(values, next...) {
		value = strings.ToLower(strings.TrimSpace(value))
		if value == "" {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
	}
	return out
}

func clamp01(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 1
	}
	return value
}
