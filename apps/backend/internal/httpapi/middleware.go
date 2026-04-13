package httpapi

import (
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"slices"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

const requestIDKey = "request_id"

func requestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader("X-Request-ID")
		if id == "" {
			id = newRequestID()
		}
		c.Set(requestIDKey, id)
		c.Header("X-Request-ID", id)
		c.Next()
	}
}

func accessLog(logger *zap.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		logger.Info("http request",
			zap.String("method", c.Request.Method),
			zap.String("path", c.FullPath()),
			zap.Int("status", c.Writer.Status()),
			zap.Duration("duration", time.Since(start)),
			zap.String("request_id", requestIDFromContext(c)),
		)
	}
}

func recovery(logger *zap.Logger) gin.HandlerFunc {
	return gin.CustomRecovery(func(c *gin.Context, recovered any) {
		logger.Error("panic recovered", zap.Any("panic", recovered), zap.String("request_id", requestIDFromContext(c)))
		problem(c, http.StatusInternalServerError, "https://spotify-rec.local/errors/internal", "Internal server error", "The server encountered an unexpected error.")
	})
}

func apiKeyAuth(keys []string) gin.HandlerFunc {
	return func(c *gin.Context) {
		if len(keys) == 0 || c.Request.URL.Path == "/healthz" || c.Request.URL.Path == "/readyz" {
			c.Next()
			return
		}
		key := c.GetHeader("X-API-Key")
		if !slices.Contains(keys, key) {
			problem(c, http.StatusUnauthorized, "https://spotify-rec.local/errors/unauthorized", "Unauthorized", "A valid X-API-Key header is required.")
			c.Abort()
			return
		}
		c.Next()
	}
}

func newRequestID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return time.Now().UTC().Format("20060102150405.000000000")
	}
	return hex.EncodeToString(b[:])
}

func requestIDFromContext(c *gin.Context) string {
	value, ok := c.Get(requestIDKey)
	if !ok {
		return ""
	}
	id, _ := value.(string)
	return id
}

func cors() gin.HandlerFunc {
	return func(c *gin.Context) {
		origin := c.GetHeader("Origin")
		if origin == "" {
			origin = "*"
		}
		c.Header("Access-Control-Allow-Origin", origin)
		c.Header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		c.Header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, X-Request-ID")
		c.Header("Access-Control-Allow-Credentials", "true")
		c.Header("Access-Control-Max-Age", "86400")

		if c.Request.Method == "OPTIONS" {
			c.AbortWithStatus(http.StatusNoContent)
			return
		}
		c.Next()
	}
}
