package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Environment         string
	Version             string
	HTTPAddr            string
	StoreDriver         string
	DatabaseURL         string
	APIKeys             []string
	SeedDemoData        bool
	ContentWeight       float64
	CollaborativeWeight float64
	PopularityWeight    float64
	ExplorationWeight   float64
	DiversityLambda     float64
	SpotifyClientID     string
	SpotifyClientSecret string
	SpotifyBearerToken  string
	SpotifyMarket       string
	UnlockerURL         string
	MusicBrainzAppName  string
	MusicBrainzContact  string
	ProviderCacheTTL    time.Duration
}

func Load() Config {
	return Config{
		Environment:         env("APP_ENV", "development"),
		Version:             env("APP_VERSION", "0.1.0"),
		HTTPAddr:            env("HTTP_ADDR", ":8080"),
		StoreDriver:         env("STORE_DRIVER", "postgres"),
		DatabaseURL:         env("DATABASE_URL", "postgres://spotify:spotify@localhost:5432/spotifyrec?sslmode=disable"),
		APIKeys:             csv(env("API_KEYS", "")),
		SeedDemoData:        boolEnv("SEED_DEMO_DATA", false),
		ContentWeight:       floatEnv("REC_CONTENT_WEIGHT", 0.44),
		CollaborativeWeight: floatEnv("REC_COLLAB_WEIGHT", 0.28),
		PopularityWeight:    floatEnv("REC_POPULARITY_WEIGHT", 0.08),
		ExplorationWeight:   floatEnv("REC_EXPLORATION_WEIGHT", 0.20),
		DiversityLambda:     floatEnv("REC_DIVERSITY_LAMBDA", 0.74),
		SpotifyClientID:     env("SPOTIFY_CLIENT_ID", ""),
		SpotifyClientSecret: env("SPOTIFY_CLIENT_SECRET", ""),
		SpotifyBearerToken:  env("SPOTIFY_BEARER_TOKEN", ""),
		SpotifyMarket:       env("SPOTIFY_MARKET", "US"),
		MusicBrainzAppName:  env("MUSICBRAINZ_APP_NAME", "SpotifyRecAlg"),
		MusicBrainzContact:  env("MUSICBRAINZ_CONTACT", ""),
		ProviderCacheTTL:    time.Duration(intEnv("PROVIDER_CACHE_TTL_HOURS", 24)) * time.Hour,
		UnlockerURL:         env("UNLOCKER_URL", "http://localhost:5000"),
	}
}

func env(key, fallback string) string {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}
	return value
}

func csv(value string) []string {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

func boolEnv(key string, fallback bool) bool {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	value, err := strconv.ParseBool(raw)
	if err != nil {
		return fallback
	}
	return value
}

func floatEnv(key string, fallback float64) float64 {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return fallback
	}
	return value
}

func intEnv(key string, fallback int) int {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}
