package httpapi

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
	"go.uber.org/zap"
)

type Store interface {
	recommendation.SnapshotProvider
	Ping(ctx context.Context) error
	UpsertTrack(ctx context.Context, track recommendation.Track) error
	UpsertTracks(ctx context.Context, tracks []recommendation.Track) error
	RecordInteraction(ctx context.Context, interaction recommendation.Interaction) error
	GetControls(ctx context.Context, userID string) (recommendation.UserControls, error)
	UpsertControls(ctx context.Context, controls recommendation.UserControls) error
}

type RouterConfig struct {
	Store    Store
	Engine   *recommendation.Engine
	Provider *provider.Service
	Logger   *zap.Logger
	APIKeys  []string
	Version  string
}

func NewRouter(cfg RouterConfig) http.Handler {
	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.Use(recovery(cfg.Logger), cors(), requestID(), accessLog(cfg.Logger), apiKeyAuth(cfg.APIKeys))

	handler := handler{
		store:    cfg.Store,
		engine:   cfg.Engine,
		provider: cfg.Provider,
		logger:   cfg.Logger,
		version:  cfg.Version,
	}

	router.GET("/healthz", handler.health)
	router.GET("/readyz", handler.ready)

	v1 := router.Group("/v1")
	v1.GET("/openapi.yaml", handler.openapi)
	v1.POST("/tracks", handler.upsertTrack)
	v1.PUT("/tracks/batch", handler.upsertTracks)
	v1.POST("/interactions", handler.recordInteraction)
	v1.POST("/recommendations", handler.recommend)
	v1.GET("/users/:user_id/taste-profile", handler.tasteProfile)
	v1.GET("/users/:user_id/controls", handler.getControls)
	v1.PUT("/users/:user_id/controls", handler.upsertControls)
	v1.POST("/providers/spotify/import", handler.importSpotify)
	v1.POST("/providers/spotify/search", handler.searchSpotify)
	v1.POST("/providers/musicbrainz/enrich", handler.enrichMusicBrainz)
	v1.GET("/providers/status", handler.providerStatus)

	return router
}

type handler struct {
	store    Store
	engine   *recommendation.Engine
	provider *provider.Service
	logger   *zap.Logger
	version  string
}

func (h handler) health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"status": "ok", "version": h.version})
}

func (h handler) ready(c *gin.Context) {
	if err := h.store.Ping(c.Request.Context()); err != nil {
		problem(c, http.StatusServiceUnavailable, "https://spotify-rec.local/errors/storage-unavailable", "Storage unavailable", err.Error())
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "ready"})
}

func (h handler) openapi(c *gin.Context) {
	c.File("docs/openapi.yaml")
}

func (h handler) upsertTrack(c *gin.Context) {
	var req recommendation.Track
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	if err := recommendation.ValidateTrack(req); err != nil {
		problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/validation", "Validation failed", err.Error())
		return
	}
	if err := h.store.UpsertTrack(c.Request.Context(), req); err != nil {
		h.logger.Error("upsert track", zap.Error(err))
		problem(c, http.StatusInternalServerError, "https://spotify-rec.local/errors/storage-write", "Storage write failed", "Track could not be stored.")
		return
	}
	c.JSON(http.StatusOK, req)
}

func (h handler) upsertTracks(c *gin.Context) {
	var req struct {
		Tracks []recommendation.Track `json:"tracks" binding:"required"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	if len(req.Tracks) == 0 {
		problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/validation", "Validation failed", "tracks must contain at least one item")
		return
	}
	if len(req.Tracks) > 1000 {
		problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/validation", "Validation failed", "batch limit is 1000 tracks")
		return
	}
	for i, track := range req.Tracks {
		if err := recommendation.ValidateTrack(track); err != nil {
			problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/validation", "Validation failed", "tracks["+strconv.Itoa(i)+"]: "+err.Error())
			return
		}
	}
	if err := h.store.UpsertTracks(c.Request.Context(), req.Tracks); err != nil {
		h.logger.Error("upsert tracks", zap.Error(err))
		problem(c, http.StatusInternalServerError, "https://spotify-rec.local/errors/storage-write", "Storage write failed", "Tracks could not be stored.")
		return
	}
	c.JSON(http.StatusOK, gin.H{"stored": len(req.Tracks)})
}

func (h handler) recordInteraction(c *gin.Context) {
	var req recommendation.Interaction
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	if strings.TrimSpace(req.UserID) == "" || strings.TrimSpace(req.TrackID) == "" || strings.TrimSpace(string(req.Type)) == "" {
		problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/validation", "Validation failed", "user_id, track_id, and type are required")
		return
	}
	if err := h.store.RecordInteraction(c.Request.Context(), req); err != nil {
		h.logger.Error("record interaction", zap.Error(err))
		problem(c, http.StatusInternalServerError, "https://spotify-rec.local/errors/storage-write", "Storage write failed", "Interaction could not be stored.")
		return
	}
	c.JSON(http.StatusAccepted, gin.H{"accepted": true})
}

func (h handler) recommend(c *gin.Context) {
	var req recommendation.RecommendRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	recs, profile, err := h.engine.Recommend(c.Request.Context(), h.store, req)
	if err != nil {
		switch {
		case errors.Is(err, context.Canceled):
			return
		case strings.Contains(err.Error(), "required"), strings.Contains(err.Error(), "empty"):
			problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/validation", "Validation failed", err.Error())
		default:
			h.logger.Error("recommend", zap.Error(err))
			problem(c, http.StatusInternalServerError, "https://spotify-rec.local/errors/recommendation-failed", "Recommendation failed", "The recommendation engine could not complete the request.")
		}
		return
	}
	c.JSON(http.StatusOK, gin.H{
		"data":          recs,
		"taste_profile": profile,
		"pagination":    gin.H{"next_cursor": nil, "has_more": false},
	})
}

func (h handler) tasteProfile(c *gin.Context) {
	userID := c.Param("user_id")
	profile, err := h.engine.TasteProfile(c.Request.Context(), h.store, userID)
	if err != nil {
		problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/profile-unavailable", "Taste profile unavailable", err.Error())
		return
	}
	c.JSON(http.StatusOK, profile)
}

func (h handler) getControls(c *gin.Context) {
	controls, err := h.store.GetControls(c.Request.Context(), c.Param("user_id"))
	if err != nil {
		h.logger.Error("get controls", zap.Error(err))
		problem(c, http.StatusInternalServerError, "https://spotify-rec.local/errors/storage-read", "Storage read failed", "Controls could not be loaded.")
		return
	}
	c.JSON(http.StatusOK, controls)
}

func (h handler) upsertControls(c *gin.Context) {
	var req recommendation.UserControls
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	req.UserID = c.Param("user_id")
	if strings.TrimSpace(req.UserID) == "" {
		problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/validation", "Validation failed", "user_id is required")
		return
	}
	if err := h.store.UpsertControls(c.Request.Context(), req); err != nil {
		h.logger.Error("upsert controls", zap.Error(err))
		problem(c, http.StatusInternalServerError, "https://spotify-rec.local/errors/storage-write", "Storage write failed", "Controls could not be stored.")
		return
	}
	c.JSON(http.StatusOK, req)
}

func (h handler) importSpotify(c *gin.Context) {
	service, ok := h.providerService(c)
	if !ok {
		return
	}
	var req provider.ImportRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	resp, err := service.ImportSpotify(c.Request.Context(), req)
	if err != nil {
		h.providerProblem(c, err)
		return
	}
	c.JSON(http.StatusOK, resp)
}

func (h handler) searchSpotify(c *gin.Context) {
	service, ok := h.providerService(c)
	if !ok {
		return
	}
	var req provider.SearchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	resp, err := service.SearchSpotify(c.Request.Context(), req)
	if err != nil {
		h.providerProblem(c, err)
		return
	}
	c.JSON(http.StatusOK, resp)
}

func (h handler) enrichMusicBrainz(c *gin.Context) {
	service, ok := h.providerService(c)
	if !ok {
		return
	}
	var req provider.EnrichRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		problem(c, http.StatusBadRequest, "https://spotify-rec.local/errors/invalid-json", "Invalid JSON", err.Error())
		return
	}
	resp, err := service.EnrichMusicBrainz(c.Request.Context(), req)
	if err != nil {
		h.providerProblem(c, err)
		return
	}
	c.JSON(http.StatusOK, resp)
}

func (h handler) providerStatus(c *gin.Context) {
	service, ok := h.providerService(c)
	if !ok {
		return
	}
	c.JSON(http.StatusOK, service.Status(c.Request.Context()))
}

func (h handler) providerService(c *gin.Context) (*provider.Service, bool) {
	if h.provider == nil {
		problem(c, http.StatusServiceUnavailable, "https://spotify-rec.local/errors/provider-unavailable", "Provider unavailable", "Provider imports are not configured for this storage backend.")
		return nil, false
	}
	return h.provider, true
}

func (h handler) providerProblem(c *gin.Context, err error) {
	if errors.Is(err, context.Canceled) {
		return
	}
	message := err.Error()
	switch {
	case strings.Contains(message, "not configured"), strings.Contains(message, "credentials"):
		problem(c, http.StatusServiceUnavailable, "https://spotify-rec.local/errors/provider-not-configured", "Provider not configured", message)
	case strings.Contains(message, "required"), strings.Contains(message, "unsupported"), strings.Contains(message, "must be"):
		problem(c, http.StatusUnprocessableEntity, "https://spotify-rec.local/errors/provider-validation", "Validation failed", message)
	default:
		h.logger.Error("provider request", zap.Error(err))
		problem(c, http.StatusBadGateway, "https://spotify-rec.local/errors/provider-request-failed", "Provider request failed", "The upstream provider request could not be completed.")
	}
}
