package httpapi

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

type Problem struct {
	Type     string `json:"type"`
	Title    string `json:"title"`
	Status   int    `json:"status"`
	Detail   string `json:"detail,omitempty"`
	Instance string `json:"instance,omitempty"`
}

func problem(c *gin.Context, status int, problemType, title, detail string) {
	if c.Writer.Written() {
		return
	}
	c.Header("Content-Type", "application/problem+json")
	c.JSON(status, Problem{
		Type:     problemType,
		Title:    title,
		Status:   status,
		Detail:   detail,
		Instance: c.Request.URL.Path,
	})
}

func notFound(c *gin.Context) {
	problem(c, http.StatusNotFound, "https://spotify-rec.local/errors/not-found", "Not found", "The requested resource does not exist.")
}
