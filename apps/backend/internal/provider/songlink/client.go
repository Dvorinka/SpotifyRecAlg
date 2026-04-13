// Package songlink provides a client for the Song.link/Odesli API.
// Song.link offers free cross-platform music URL mapping.
package songlink

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sync"
	"time"
)

const (
	apiBase               = "https://api.song.link/v1-alpha.1"
	minRequestInterval    = 7 * time.Second
	maxRequestsPerMinute  = 9
)

// PlatformLink represents a link to a track on a specific platform
type PlatformLink struct {
	Platform   string `json:"platform"`
	URL        string `json:"url"`
	EntityType string `json:"entity_type"`
	ID         string `json:"id,omitempty"`
	NativeURI  string `json:"native_uri,omitempty"`
}

// CrossPlatformLinks holds links for a track across multiple platforms
type CrossPlatformLinks struct {
	SpotifyID string                    `json:"spotify_id"`
	ISRC      string                    `json:"isrc,omitempty"`
	Links     map[string]PlatformLink    `json:"links"`
}

// Client for Song.link API
type Client struct {
	httpClient        *http.Client
	lastRequestTime   time.Time
	requestCount      int
	countResetTime    time.Time
	mu                sync.Mutex
}

// NewClient creates a new Song.link client
func NewClient() *Client {
	return &Client{
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
		countResetTime: time.Now(),
	}
}

// Configured always returns true for Song.link (no API key needed)
func (c *Client) Configured() bool {
	return true
}

func (c *Client) rateLimit() {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()

	// Reset counter every minute
	if now.Sub(c.countResetTime) >= time.Minute {
		c.requestCount = 0
		c.countResetTime = now
	}

	// Check if we've hit the per-minute limit
	if c.requestCount >= maxRequestsPerMinute {
		waitTime := time.Minute - now.Sub(c.countResetTime)
		if waitTime > 0 {
			time.Sleep(waitTime)
			c.requestCount = 0
			c.countResetTime = time.Now()
		}
	}

	// Ensure minimum interval between requests
	elapsed := now.Sub(c.lastRequestTime)
	if elapsed < minRequestInterval {
		time.Sleep(minRequestInterval - elapsed)
	}

	c.lastRequestTime = time.Now()
	c.requestCount++
}

// GetLinksFromSpotifyID gets cross-platform links from a Spotify track ID
func (c *Client) GetLinksFromSpotifyID(spotifyID string) (*CrossPlatformLinks, error) {
	spotifyURL := fmt.Sprintf("https://open.spotify.com/track/%s", spotifyID)
	return c.GetLinks(spotifyURL)
}

// GetLinks gets cross-platform links from any music URL
func (c *Client) GetLinks(musicURL string) (*CrossPlatformLinks, error) {
	c.rateLimit()

	params := url.Values{
		"url": {musicURL},
		"userCountry": {"US"},
	}

	apiURL := fmt.Sprintf("%s/links?%s", apiBase, params.Encode())

	req, err := http.NewRequest("GET", apiURL, nil)
	if err != nil {
		return nil, err
	}

	req.Header.Set("User-Agent", "SpotifyRecAlg/1.0")
	req.Header.Set("Accept", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusTooManyRequests {
		// Rate limited - wait and retry once
		retryAfter := 15
		if ra := resp.Header.Get("Retry-After"); ra != "" {
			fmt.Sscanf(ra, "%d", &retryAfter)
		}
		time.Sleep(time.Duration(retryAfter) * time.Second)
		return c.GetLinks(musicURL)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("song.link API error: HTTP %d", resp.StatusCode)
	}

	var data struct {
		EntityUniqueID string `json:"entityUniqueId"`
		UserCountry    string `json:"userCountry"`
		PageURL        string `json:"pageUrl"`
		LinksByPlatform map[string]struct {
			URL       string `json:"url"`
			EntityUniqueID string `json:"entityUniqueId"`
		} `json:"linksByPlatform"`
		EntitiesByUniqueID map[string]struct {
			ID       string `json:"id"`
			Type     string `json:"type"`
			Title    string `json:"title"`
			Artist   string `json:"artistName"`
			ThumbnailURL string `json:"thumbnailUrl"`
			APIProvider  string `json:"apiProvider"`
			Platforms    []string `json:"platforms"`
		} `json:"entitiesByUniqueId"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return nil, err
	}

	links := &CrossPlatformLinks{
		Links: make(map[string]PlatformLink),
	}

	// Extract Spotify ID
	for uniqueID, entity := range data.EntitiesByUniqueID {
		if entity.APIProvider == "spotify" {
			links.SpotifyID = entity.ID
		}
		if entity.Type == "song" {
			// ISRC can sometimes be derived from the unique ID format
			_ = uniqueID
		}
	}

	// Platform name mapping
	platformNames := map[string]string{
		"spotify":      "spotify",
		"tidal":        "tidal",
		"qobuz":        "qobuz",
		"amazonMusic":  "amazonMusic",
		"amazonStore":  "amazon",
		"deezer":       "deezer",
		"appleMusic":   "appleMusic",
		"youtube":      "youtube",
		"youtubeMusic": "youtubeMusic",
		"soundcloud":   "soundcloud",
		"napster":      "napster",
		"pandora":      "pandora",
	}

	for platform, linkData := range data.LinksByPlatform {
		if name, ok := platformNames[platform]; ok {
			links.Links[name] = PlatformLink{
				Platform:   platform,
				URL:        linkData.URL,
				EntityType: "track",
			}
		}
	}

	return links, nil
}
