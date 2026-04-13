package memory

import (
	"context"
	"time"

	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
)

// SeedLargeCatalog creates a diverse catalog for realistic recommendations
func SeedLargeCatalog(store *Store) {
	ctx := context.Background()
	now := time.Now().UTC()

	// Diverse catalog with many tracks across genres
	tracks := []recommendation.Track{
		// Electronic / Dance (similar to Avicii)
		{ID: "25FTMokYEbEWHEdss5JLZS", Title: "The Nights", Artist: "Avicii", Genres: []string{"electronic", "dance", "edm"}, Popularity: 0.85, Features: recommendation.AudioFeatures{Danceability: 0.72, Energy: 0.78, Loudness: -5, Speechiness: 0.05, Acousticness: 0.15, Instrumentalness: 0.10, Liveness: 0.20, Valence: 0.70, Tempo: 126, TimeSignature: 4, Key: 4, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-wake-me-up", Title: "Wake Me Up", Artist: "Avicii", Genres: []string{"electronic", "dance", "country"}, Popularity: 0.90, Features: recommendation.AudioFeatures{Danceability: 0.68, Energy: 0.82, Loudness: -4, Speechiness: 0.06, Acousticness: 0.22, Instrumentalness: 0.05, Liveness: 0.15, Valence: 0.65, Tempo: 124, TimeSignature: 4, Key: 2, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-levels", Title: "Levels", Artist: "Avicii", Genres: []string{"electronic", "dance", "edm"}, Popularity: 0.88, Features: recommendation.AudioFeatures{Danceability: 0.75, Energy: 0.85, Loudness: -3, Speechiness: 0.04, Acousticness: 0.08, Instrumentalness: 0.20, Liveness: 0.25, Valence: 0.72, Tempo: 128, TimeSignature: 4, Key: 5, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-hey-brother", Title: "Hey Brother", Artist: "Avicii", Genres: []string{"electronic", "dance", "country"}, Popularity: 0.82, Features: recommendation.AudioFeatures{Danceability: 0.65, Energy: 0.80, Loudness: -5, Speechiness: 0.07, Acousticness: 0.30, Instrumentalness: 0.08, Liveness: 0.18, Valence: 0.60, Tempo: 125, TimeSignature: 4, Key: 7, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-waiting-for-love", Title: "Waiting For Love", Artist: "Avicii", Genres: []string{"electronic", "dance", "pop"}, Popularity: 0.83, Features: recommendation.AudioFeatures{Danceability: 0.70, Energy: 0.78, Loudness: -5, Speechiness: 0.05, Acousticness: 0.18, Instrumentalness: 0.12, Liveness: 0.20, Valence: 0.68, Tempo: 126, TimeSignature: 4, Key: 9, Mode: 1}, DiscoveryAllowed: true},

		// Pop / Dance
		{ID: "trk-counting-stars", Title: "Counting Stars", Artist: "OneRepublic", Genres: []string{"pop", "rock"}, Popularity: 0.87, Features: recommendation.AudioFeatures{Danceability: 0.68, Energy: 0.75, Loudness: -6, Speechiness: 0.08, Acousticness: 0.20, Instrumentalness: 0.02, Liveness: 0.15, Valence: 0.55, Tempo: 122, TimeSignature: 4, Key: 2, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-uptown-funk", Title: "Uptown Funk", Artist: "Bruno Mars", Genres: []string{"pop", "funk"}, Popularity: 0.89, Features: recommendation.AudioFeatures{Danceability: 0.82, Energy: 0.88, Loudness: -4, Speechiness: 0.10, Acousticness: 0.05, Instrumentalness: 0.02, Liveness: 0.30, Valence: 0.90, Tempo: 115, TimeSignature: 4, Key: 0, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-happy", Title: "Happy", Artist: "Pharrell Williams", Genres: []string{"pop", "soul"}, Popularity: 0.86, Features: recommendation.AudioFeatures{Danceability: 0.78, Energy: 0.82, Loudness: -5, Speechiness: 0.12, Acousticness: 0.10, Instrumentalness: 0.03, Liveness: 0.22, Valence: 0.95, Tempo: 160, TimeSignature: 4, Key: 5, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-cant-hold-us", Title: "Can't Hold Us", Artist: "Macklemore", Genres: []string{"hip-hop", "pop"}, Popularity: 0.84, Features: recommendation.AudioFeatures{Danceability: 0.72, Energy: 0.86, Loudness: -4, Speechiness: 0.20, Acousticness: 0.15, Instrumentalness: 0.02, Liveness: 0.28, Valence: 0.75, Tempo: 146, TimeSignature: 4, Key: 7, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-i-gotta-feeling", Title: "I Gotta Feeling", Artist: "Black Eyed Peas", Genres: []string{"pop", "dance"}, Popularity: 0.85, Features: recommendation.AudioFeatures{Danceability: 0.76, Energy: 0.80, Loudness: -5, Speechiness: 0.08, Acousticness: 0.12, Instrumentalness: 0.05, Liveness: 0.25, Valence: 0.80, Tempo: 128, TimeSignature: 4, Key: 2, Mode: 1}, DiscoveryAllowed: true},

		// Alternative / Indie
		{ID: "trk-do-i-wanna-know", Title: "Do I Wanna Know?", Artist: "Arctic Monkeys", Genres: []string{"alternative", "indie"}, Popularity: 0.80, Features: recommendation.AudioFeatures{Danceability: 0.58, Energy: 0.68, Loudness: -8, Speechiness: 0.05, Acousticness: 0.35, Instrumentalness: 0.25, Liveness: 0.12, Valence: 0.35, Tempo: 85, TimeSignature: 4, Key: 9, Mode: 0}, DiscoveryAllowed: true},
		{ID: "trk-somebody-told-me", Title: "Somebody Told Me", Artist: "The Killers", Genres: []string{"alternative", "rock"}, Popularity: 0.82, Features: recommendation.AudioFeatures{Danceability: 0.62, Energy: 0.85, Loudness: -5, Speechiness: 0.06, Acousticness: 0.08, Instrumentalness: 0.02, Liveness: 0.20, Valence: 0.65, Tempo: 138, TimeSignature: 4, Key: 0, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-mr-brightside", Title: "Mr. Brightside", Artist: "The Killers", Genres: []string{"alternative", "rock"}, Popularity: 0.88, Features: recommendation.AudioFeatures{Danceability: 0.55, Energy: 0.90, Loudness: -4, Speechiness: 0.09, Acousticness: 0.05, Instrumentalness: 0.02, Liveness: 0.25, Valence: 0.60, Tempo: 148, TimeSignature: 4, Key: 2, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-take-me-out", Title: "Take Me Out", Artist: "Franz Ferdinand", Genres: []string{"alternative", "indie"}, Popularity: 0.78, Features: recommendation.AudioFeatures{Danceability: 0.65, Energy: 0.82, Loudness: -5, Speechiness: 0.04, Acousticness: 0.15, Instrumentalness: 0.05, Liveness: 0.18, Valence: 0.55, Tempo: 105, TimeSignature: 4, Key: 7, Mode: 1}, DiscoveryAllowed: true},

		// Rock
		{ID: "trk-boulevard-of-broken-dreams", Title: "Boulevard of Broken Dreams", Artist: "Green Day", Genres: []string{"rock", "punk"}, Popularity: 0.86, Features: recommendation.AudioFeatures{Danceability: 0.52, Energy: 0.78, Loudness: -5, Speechiness: 0.04, Acousticness: 0.12, Instrumentalness: 0.05, Liveness: 0.15, Valence: 0.40, Tempo: 167, TimeSignature: 4, Key: 0, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-knights-of-cydonia", Title: "Knights of Cydonia", Artist: "Muse", Genres: []string{"rock", "alternative"}, Popularity: 0.81, Features: recommendation.AudioFeatures{Danceability: 0.45, Energy: 0.92, Loudness: -3, Speechiness: 0.08, Acousticness: 0.05, Instrumentalness: 0.35, Liveness: 0.22, Valence: 0.45, Tempo: 137, TimeSignature: 4, Key: 2, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-smells-like-teen-spirit", Title: "Smells Like Teen Spirit", Artist: "Nirvana", Genres: []string{"rock", "grunge"}, Popularity: 0.87, Features: recommendation.AudioFeatures{Danceability: 0.48, Energy: 0.95, Loudness: -2, Speechiness: 0.07, Acousticness: 0.03, Instrumentalness: 0.10, Liveness: 0.30, Valence: 0.35, Tempo: 117, TimeSignature: 4, Key: 4, Mode: 0}, DiscoveryAllowed: true},

		// Acoustic / Folk
		{ID: "trk-ho-hey", Title: "Ho Hey", Artist: "The Lumineers", Genres: []string{"folk", "acoustic"}, Popularity: 0.79, Features: recommendation.AudioFeatures{Danceability: 0.58, Energy: 0.55, Loudness: -10, Speechiness: 0.06, Acousticness: 0.75, Instrumentalness: 0.05, Liveness: 0.18, Valence: 0.55, Tempo: 80, TimeSignature: 4, Key: 7, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-riptide", Title: "Riptide", Artist: "Vance Joy", Genres: []string{"folk", "indie"}, Popularity: 0.83, Features: recommendation.AudioFeatures{Danceability: 0.62, Energy: 0.48, Loudness: -11, Speechiness: 0.08, Acousticness: 0.72, Instrumentalness: 0.08, Liveness: 0.12, Valence: 0.60, Tempo: 102, TimeSignature: 4, Key: 9, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-let-her-go", Title: "Let Her Go", Artist: "Passenger", Genres: []string{"folk", "acoustic"}, Popularity: 0.80, Features: recommendation.AudioFeatures{Danceability: 0.52, Energy: 0.42, Loudness: -12, Speechiness: 0.05, Acousticness: 0.80, Instrumentalness: 0.05, Liveness: 0.15, Valence: 0.35, Tempo: 75, TimeSignature: 4, Key: 4, Mode: 1}, DiscoveryAllowed: true},

		// Synth / New Wave
		{ID: "trk-neon-dawn", Title: "Neon Dawn", Artist: "The Arrays", Genres: []string{"synthpop", "indie"}, Popularity: 0.72, Features: recommendation.AudioFeatures{Danceability: 0.74, Energy: 0.70, Loudness: -6, Speechiness: 0.05, Acousticness: 0.12, Instrumentalness: 0.15, Liveness: 0.12, Valence: 0.67, Tempo: 118, TimeSignature: 4, Key: 2, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-static-heart", Title: "Static Heart", Artist: "The Arrays", Genres: []string{"synthpop"}, Popularity: 0.62, Features: recommendation.AudioFeatures{Danceability: 0.68, Energy: 0.66, Loudness: -8, Speechiness: 0.04, Acousticness: 0.18, Instrumentalness: 0.20, Liveness: 0.10, Valence: 0.58, Tempo: 116, TimeSignature: 4, Key: 4, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-glass-road", Title: "Glass Road", Artist: "North Index", Genres: []string{"indie", "rock"}, Popularity: 0.51, Features: recommendation.AudioFeatures{Danceability: 0.55, Energy: 0.78, Loudness: -5, Speechiness: 0.06, Acousticness: 0.20, Instrumentalness: 0.08, Liveness: 0.18, Valence: 0.54, Tempo: 132, TimeSignature: 4, Key: 9, Mode: 1}, DiscoveryAllowed: true},
		{ID: "trk-velvet-motor", Title: "Velvet Motor", Artist: "Signal Choir", Genres: []string{"pop-punk", "rock"}, Popularity: 0.49, Features: recommendation.AudioFeatures{Danceability: 0.60, Energy: 0.88, Loudness: -4, Speechiness: 0.07, Acousticness: 0.09, Instrumentalness: 0.02, Liveness: 0.21, Valence: 0.62, Tempo: 148, TimeSignature: 4, Key: 5, Mode: 1}, DiscoveryAllowed: true, CommercialBoost: 0.02},
	}

	_ = store.UpsertTracks(ctx, tracks)

	// Add collaborative interactions to create taste neighborhoods
	interactions := []recommendation.Interaction{
		// User who likes electronic/dance
		{UserID: "user-electronic", TrackID: "25FTMokYEbEWHEdss5JLZS", Type: recommendation.InteractionLike, OccurredAt: now.Add(-24 * time.Hour)},
		{UserID: "user-electronic", TrackID: "trk-wake-me-up", Type: recommendation.InteractionLike, OccurredAt: now.Add(-48 * time.Hour)},
		{UserID: "user-electronic", TrackID: "trk-levels", Type: recommendation.InteractionLike, OccurredAt: now.Add(-72 * time.Hour)},

		// User who likes pop
		{UserID: "user-pop", TrackID: "trk-counting-stars", Type: recommendation.InteractionLike, OccurredAt: now.Add(-24 * time.Hour)},
		{UserID: "user-pop", TrackID: "trk-uptown-funk", Type: recommendation.InteractionLike, OccurredAt: now.Add(-48 * time.Hour)},
		{UserID: "user-pop", TrackID: "25FTMokYEbEWHEdss5JLZS", Type: recommendation.InteractionLike, OccurredAt: now.Add(-36 * time.Hour)},

		// User who likes alternative
		{UserID: "user-alt", TrackID: "trk-do-i-wanna-know", Type: recommendation.InteractionLike, OccurredAt: now.Add(-24 * time.Hour)},
		{UserID: "user-alt", TrackID: "trk-somebody-told-me", Type: recommendation.InteractionLike, OccurredAt: now.Add(-48 * time.Hour)},

		// Cross-genre listener
		{UserID: "user-mixed", TrackID: "25FTMokYEbEWHEdss5JLZS", Type: recommendation.InteractionLike, OccurredAt: now.Add(-24 * time.Hour)},
		{UserID: "user-mixed", TrackID: "trk-boulevard-of-broken-dreams", Type: recommendation.InteractionLike, OccurredAt: now.Add(-48 * time.Hour)},
		{UserID: "user-mixed", TrackID: "trk-riptide", Type: recommendation.InteractionLike, OccurredAt: now.Add(-72 * time.Hour)},
	}

	for _, interaction := range interactions {
		_ = store.RecordInteraction(ctx, interaction)
	}
}
