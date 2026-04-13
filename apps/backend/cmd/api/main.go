package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/config"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/httpapi"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/musicbrainz"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/songlink"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/spotify"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/webplayer"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
	memstore "github.com/tdvorak/spotifyrecalg/apps/backend/internal/storage/memory"
	pgstore "github.com/tdvorak/spotifyrecalg/apps/backend/internal/storage/postgres"
	"go.uber.org/zap"
)

func main() {
	cfg := config.Load()

	logger, err := zap.NewProduction()
	if cfg.Environment == "development" {
		logger, err = zap.NewDevelopment()
	}
	if err != nil {
		log.Fatalf("create logger: %v", err)
	}
	defer func() { _ = logger.Sync() }()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	store, cleanup, err := buildStore(ctx, cfg, logger)
	if err != nil {
		logger.Fatal("initialize storage", zap.Error(err))
	}
	defer cleanup()

	engine := recommendation.NewEngine(recommendation.EngineConfig{
		Now:               time.Now,
		ContentWeight:     cfg.ContentWeight,
		CollabWeight:      cfg.CollaborativeWeight,
		PopularityWeight:  cfg.PopularityWeight,
		ExplorationWeight: cfg.ExplorationWeight,
		DiversityLambda:   cfg.DiversityLambda,
	})

	router := httpapi.NewRouter(httpapi.RouterConfig{
		Store:    store,
		Engine:   engine,
		Provider: buildProviderService(store, cfg),
		Logger:   logger,
		APIKeys:  cfg.APIKeys,
		Version:  cfg.Version,
	})

	server := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           router,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() {
		logger.Info("api listening", zap.String("addr", cfg.HTTPAddr), zap.String("store", cfg.StoreDriver))
		errCh <- server.ListenAndServe()
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil {
			logger.Error("graceful shutdown failed", zap.Error(err))
		}
	case err := <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Fatal("server stopped", zap.Error(err))
		}
	}
}

func buildProviderService(store httpapi.Store, cfg config.Config) *provider.Service {
	providerStore, ok := store.(provider.Store)
	if !ok {
		return nil
	}
	spotifyClient := spotify.New(spotify.Config{
		ClientID:     cfg.SpotifyClientID,
		ClientSecret: cfg.SpotifyClientSecret,
		BearerToken:  cfg.SpotifyBearerToken,
		Market:       cfg.SpotifyMarket,
	})
	webplayerClient := webplayer.NewClient()
	songlinkClient := songlink.NewClient()
	musicBrainzClient := musicbrainz.New(musicbrainz.Config{
		AppName: cfg.MusicBrainzAppName,
		Contact: cfg.MusicBrainzContact,
		Version: cfg.Version,
	})
	return provider.NewService(providerStore, spotifyClient, webplayerClient, songlinkClient, musicBrainzClient, provider.ServiceConfig{
		DefaultMarket: cfg.SpotifyMarket,
		CacheTTL:      cfg.ProviderCacheTTL,
		Version:       cfg.Version,
	})
}

func buildStore(ctx context.Context, cfg config.Config, logger *zap.Logger) (httpapi.Store, func(), error) {
	if cfg.StoreDriver == "memory" {
		store := memstore.New()
		if cfg.SeedDemoData {
			memstore.SeedLargeCatalog(store)
		}
		return store, func() {}, nil
	}

	pool, err := pgxpool.New(ctx, cfg.DatabaseURL)
	if err != nil {
		return nil, nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, nil, err
	}

	logger.Info("connected to postgres")
	return pgstore.New(pool), pool.Close, nil
}
