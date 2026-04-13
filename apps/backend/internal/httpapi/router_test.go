package httpapi

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
	memstore "github.com/tdvorak/spotifyrecalg/apps/backend/internal/storage/memory"
	"go.uber.org/zap"
)

func TestRecommendationEndpoint(t *testing.T) {
	gin.SetMode(gin.TestMode)
	store := memstore.New()
	memstore.SeedDemo(store)
	engine := recommendation.NewEngine(recommendation.EngineConfig{
		Now:               func() time.Time { return time.Date(2026, 4, 13, 12, 0, 0, 0, time.UTC) },
		ContentWeight:     0.44,
		CollabWeight:      0.28,
		PopularityWeight:  0.08,
		ExplorationWeight: 0.20,
		DiversityLambda:   0.74,
	})
	router := NewRouter(RouterConfig{
		Store:   store,
		Engine:  engine,
		Logger:  zap.NewNop(),
		Version: "test",
	})

	body := bytes.NewBufferString(`{"user_id":"demo-user","limit":3,"mode":"balanced"}`)
	req := httptest.NewRequest(http.MethodPost, "/v1/recommendations", body)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()

	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
	}
	if !bytes.Contains(rec.Body.Bytes(), []byte(`"taste_profile"`)) {
		t.Fatalf("expected taste profile in response: %s", rec.Body.String())
	}
}

func TestAPIKeyMiddleware(t *testing.T) {
	router := NewRouter(RouterConfig{
		Store:   memstore.New(),
		Engine:  recommendation.NewEngine(recommendation.EngineConfig{}),
		Logger:  zap.NewNop(),
		APIKeys: []string{"secret"},
		Version: "test",
	})

	req := httptest.NewRequest(http.MethodPost, "/v1/recommendations", bytes.NewBufferString(`{}`))
	rec := httptest.NewRecorder()
	router.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", rec.Code)
	}
}
