// Package webplayer provides a Go native Spotify Web Player client using TOTP authentication.
// This is a port of the Python implementation, allowing auth-free access to Spotify metadata.
package webplayer

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha1"
	"encoding/base32"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	// Hardcoded TOTP secret from Spotify Web Player (publicly known)
	totpSecret         = "GM3TMMJTGYZTQNZVGM4DINJZHA4TGOBYGMZTCMRTGEYDSMJRHE4TEOBUG4YTCMRUGQ4DQOJUGQYTAMRRGA2TCMJSHE3TCMBY"
	totpVersion        = 61
	clientVersion      = "1.2.40"
	minRequestInterval = 100 * time.Millisecond
)

// GraphQL persisted query hashes
var graphqlHashes = map[string]string{
	"getTrack":      "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294",
	"getAlbum":      "b9bfabef66ed756e5e13f68a942deb60bd4125ec1f1be8cc42769dc0259b4b10",
	"fetchPlaylist": "bb67e0af06e8d6f52b531f97468ee4acd44cd0f82b988e15c2ea47b1148efc77",
	"getArtist":     "2e7f695dd9c0a6591c2d4f3b9e6e0a7c8d5b4a3f2e1d0c9b8a7f6e5d4c3b2a1",
}

// Track represents Spotify track metadata
type Track struct {
	ID           string            `json:"id"`
	Name         string            `json:"name"`
	Artists      []Artist          `json:"artists"`
	Album        Album             `json:"album"`
	DurationMs   int               `json:"duration_ms"`
	Explicit     bool              `json:"explicit"`
	ExternalURLs map[string]string `json:"external_urls"`
}

// Artist represents a Spotify artist
type Artist struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	URI  string `json:"uri"`
}

// Album represents a Spotify album
type Album struct {
	ID     string  `json:"id"`
	Name   string  `json:"name"`
	URI    string  `json:"uri"`
	Images []Image `json:"images"`
}

// Image represents an image asset
type Image struct {
	URL    string `json:"url"`
	Width  int    `json:"width"`
	Height int    `json:"height"`
}

// token holds the Spotify access token
type token struct {
	AccessToken   string
	ClientID      string
	DeviceID      string
	ClientVersion string
	ExpiresAt     time.Time
	ClientToken   string
}

// Client is the Spotify Web Player API client
type Client struct {
	httpClient  *http.Client
	baseURL     string
	token       *token
	mu          sync.RWMutex
	lastRequest time.Time
	cookies     map[string]string
}

// NewClient creates a new Web Player client
func NewClient() *Client {
	jar, _ := cookiejar.New(nil)
	return &Client{
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
			Jar:     jar,
		},
		baseURL: "https://open.spotify.com",
		cookies: make(map[string]string),
	}
}

// Configured returns true if the client is functional (always true for this client)
func (c *Client) Configured() bool {
	return true
}

// generateTOTP generates a TOTP code using the hardcoded secret
func generateTOTP() string {
	// Base32 decode the secret
	secretBytes, _ := base32.StdEncoding.DecodeString(totpSecret)

	// Get current time in 30-second intervals
	currentTime := uint64(time.Now().Unix() / 30)

	// Convert to bytes (big-endian, 8 bytes)
	timeBytes := make([]byte, 8)
	for i := 7; i >= 0; i-- {
		timeBytes[i] = byte(currentTime & 0xFF)
		currentTime >>= 8
	}

	// HMAC-SHA1
	h := hmac.New(sha1.New, secretBytes)
	h.Write(timeBytes)
	hmacResult := h.Sum(nil)

	// Dynamic truncation
	offset := hmacResult[len(hmacResult)-1] & 0x0F
	code := int(hmacResult[offset]&0x7F)<<24 |
		int(hmacResult[offset+1]&0xFF)<<16 |
		int(hmacResult[offset+2]&0xFF)<<8 |
		int(hmacResult[offset+3]&0xFF)

	// Get 6-digit code
	totpCode := fmt.Sprintf("%06d", code%1000000)

	return totpCode
}

func (c *Client) rateLimit() {
	c.mu.Lock()
	defer c.mu.Unlock()

	now := time.Now()
	elapsed := now.Sub(c.lastRequest)
	if elapsed < minRequestInterval {
		time.Sleep(minRequestInterval - elapsed)
	}
	c.lastRequest = time.Now()
}

func (c *Client) ensureToken() error {
	c.mu.RLock()
	tok := c.token
	c.mu.RUnlock()

	if tok == nil || time.Now().After(tok.ExpiresAt.Add(-60*time.Second)) {
		return c.getAccessToken()
	}

	if tok.ClientToken == "" {
		return c.getClientToken()
	}

	return nil
}

func (c *Client) getAccessToken() error {
	// Try TOTP generation first (same as official Web Player)
	if err := c.getAccessTokenTOTP(); err == nil {
		// Client token is optional
		_ = c.getClientToken()
		return nil
	}

	// Fall back to tokener API
	if err := c.getAccessTokenTokener(); err == nil {
		// Client token is optional - try to get it but don't fail if unavailable
		_ = c.getClientToken()
		return nil
	}

	return errors.New("failed to obtain access token")
}

func (c *Client) getAccessTokenTOTP() error {
	c.rateLimit()

	totpCode := generateTOTP()

	params := url.Values{
		"reason":      {"init"},
		"productType": {"web-player"},
		"totp":        {totpCode},
		"totpVer":     {strconv.Itoa(totpVersion)},
		"totpServer":  {totpCode},
	}

	tokenURL := fmt.Sprintf("%s/api/token?%s", c.baseURL, params.Encode())

	req, err := http.NewRequest("GET", tokenURL, nil)
	if err != nil {
		return err
	}

	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
	req.Header.Set("Accept", "application/json, text/plain, */*")
	req.Header.Set("Accept-Language", "en-US,en;q=0.9")
	req.Header.Set("Referer", "https://open.spotify.com/")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	// Read body for debugging - check content length first
	var bodyBytes []byte
	if resp.ContentLength > 0 {
		bodyBytes = make([]byte, resp.ContentLength)
		_, err = io.ReadFull(resp.Body, bodyBytes)
		if err != nil {
			return fmt.Errorf("failed to read response body: %w", err)
		}
	} else {
		bodyBytes, err = io.ReadAll(resp.Body)
		if err != nil {
			return fmt.Errorf("failed to read response body: %w", err)
		}
	}

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("TOTP token request failed: HTTP %d, body: %s, content-length: %d", resp.StatusCode, string(bodyBytes), resp.ContentLength)
	}

	// Extract cookies
	for _, cookie := range resp.Cookies() {
		c.cookies[cookie.Name] = cookie.Value
	}

	var data struct {
		AccessToken string `json:"accessToken"`
		ClientID    string `json:"clientId"`
	}

	if err := json.Unmarshal(bodyBytes, &data); err != nil {
		return fmt.Errorf("failed to decode JSON: %w, body: %s", err, string(bodyBytes))
	}

	deviceID := c.cookies["sp_t"]
	if deviceID == "" {
		deviceID = generateDeviceID()
	}

	c.mu.Lock()
	c.token = &token{
		AccessToken:   data.AccessToken,
		ClientID:      data.ClientID,
		DeviceID:      deviceID,
		ClientVersion: clientVersion,
		ExpiresAt:     time.Now().Add(time.Hour),
	}
	c.mu.Unlock()

	return nil
}

func (c *Client) getAccessTokenTokener() error {
	c.rateLimit()

	resp, err := c.httpClient.Get("https://spotify-tokener-api.vercel.app/api/getToken")
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("tokener API failed: HTTP %d", resp.StatusCode)
	}

	var data struct {
		AccessToken string `json:"accessToken"`
		ClientID    string `json:"clientId"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return err
	}

	if data.AccessToken == "" || data.ClientID == "" {
		return errors.New("tokener API returned invalid data")
	}

	c.mu.Lock()
	c.token = &token{
		AccessToken:   data.AccessToken,
		ClientID:      data.ClientID,
		DeviceID:      generateDeviceID(),
		ClientVersion: clientVersion,
		ExpiresAt:     time.Now().Add(time.Hour),
	}
	c.mu.Unlock()

	return nil
}

func (c *Client) getClientToken() error {
	c.mu.RLock()
	tok := c.token
	c.mu.RUnlock()

	if tok == nil {
		return errors.New("no access token available")
	}

	c.rateLimit()

	payload := map[string]interface{}{
		"client_data": map[string]interface{}{
			"client_version": tok.ClientVersion,
			"client_id":      tok.ClientID,
			"js_sdk_data": map[string]interface{}{
				"device_brand": "unknown",
				"device_model": "unknown",
				"os":           "windows",
				"os_version":   "NT 10.0",
				"device_id":    tok.DeviceID,
				"device_type":  "computer",
			},
		},
	}

	jsonPayload, _ := json.Marshal(payload)

	req, err := http.NewRequest("POST", "https://clienttoken.spotify.com/v1/clienttoken", bytes.NewReader(jsonPayload))
	if err != nil {
		return err
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Accept-Language", "en-US,en;q=0.9")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("client token request failed: HTTP %d", resp.StatusCode)
	}

	body, _ := io.ReadAll(resp.Body)

	var data struct {
		ResponseType string `json:"response_type"`
		GrantedToken struct {
			Token string `json:"token"`
		} `json:"granted_token"`
	}

	if err := json.Unmarshal(body, &data); err != nil {
		return err
	}

	if data.ResponseType != "RESPONSE_GRANTED_TOKEN_RESPONSE" {
		return errors.New("invalid client token response type: " + data.ResponseType)
	}

	c.mu.Lock()
	c.token.ClientToken = data.GrantedToken.Token
	c.mu.Unlock()

	return nil
}

func (c *Client) graphqlQuery(operationName string, variables map[string]interface{}) (map[string]interface{}, error) {
	if err := c.ensureToken(); err != nil {
		return nil, err
	}

	hash, ok := graphqlHashes[operationName]
	if !ok {
		return nil, fmt.Errorf("unknown GraphQL operation: %s", operationName)
	}

	c.mu.RLock()
	tok := c.token
	c.mu.RUnlock()

	// Use struct with explicit field order to match Python's JSON key ordering
	// The SHA256 hash is computed on the exact JSON string
	payload := struct {
		Variables     map[string]interface{} `json:"variables"`
		OperationName string                 `json:"operationName"`
		Extensions    struct {
			PersistedQuery struct {
				Version    int    `json:"version"`
				Sha256Hash string `json:"sha256Hash"`
			} `json:"persistedQuery"`
		} `json:"extensions"`
	}{
		Variables:     variables,
		OperationName: operationName,
	}
	payload.Extensions.PersistedQuery.Version = 1
	payload.Extensions.PersistedQuery.Sha256Hash = hash

	jsonPayload, _ := json.Marshal(payload)

	c.rateLimit()

	req, err := http.NewRequest("POST", "https://api-partner.spotify.com/pathfinder/v1/query", bytes.NewReader(jsonPayload))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Authorization", "Bearer "+tok.AccessToken)
	if tok.ClientToken != "" {
		req.Header.Set("Client-Token", tok.ClientToken)
	}
	req.Header.Set("Spotify-App-Version", tok.ClientVersion)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Accept-Language", "en-US,en;q=0.9")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode == http.StatusUnauthorized {
		// Token expired, refresh and retry
		c.mu.Lock()
		c.token = nil
		c.mu.Unlock()

		if err := c.ensureToken(); err != nil {
			return nil, err
		}

		// Retry request
		c.mu.RLock()
		tok = c.token
		c.mu.RUnlock()

		req.Header.Set("Authorization", "Bearer "+tok.AccessToken)
		if tok.ClientToken != "" {
			req.Header.Set("Client-Token", tok.ClientToken)
		}

		resp, err = c.httpClient.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		body, _ = io.ReadAll(resp.Body)
	}

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GraphQL query failed: HTTP %d: %s", resp.StatusCode, string(body))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, err
	}

	return result, nil
}

// GetTrack fetches track metadata by ID
func (c *Client) GetTrack(trackID string) (*Track, error) {
	variables := map[string]interface{}{
		"uri": fmt.Sprintf("spotify:track:%s", trackID),
	}

	data, err := c.graphqlQuery("getTrack", variables)
	if err != nil {
		return nil, err
	}

	trackData, ok := getNestedMap(data, "data", "trackUnion")
	if !ok {
		return nil, errors.New("track not found in response")
	}

	if getString(trackData, "__typename") != "Track" {
		return nil, errors.New("item is not a track")
	}

	// Extract artists
	var artists []Artist
	if firstArtist, ok := getNestedMap(trackData, "firstArtist"); ok {
		if profile, ok := getNestedMap(firstArtist, "profile"); ok {
			artists = append(artists, Artist{
				ID:   getString(firstArtist, "id"),
				Name: getString(profile, "name"),
				URI:  getString(firstArtist, "uri"),
			})
		}
	}

	if otherArtists, ok := getNestedMap(trackData, "otherArtists"); ok {
		if items, ok := otherArtists["items"].([]interface{}); ok {
			for _, item := range items {
				if artist, ok := item.(map[string]interface{}); ok {
					if profile, ok := getNestedMap(artist, "profile"); ok {
						artists = append(artists, Artist{
							ID:   getString(artist, "id"),
							Name: getString(profile, "name"),
							URI:  getString(artist, "uri"),
						})
					}
				}
			}
		}
	}

	// Extract album
	var album Album
	if albumData, ok := getNestedMap(trackData, "albumOfTrack"); ok {
		album = Album{
			ID:   getString(albumData, "id"),
			Name: getString(albumData, "name"),
			URI:  getString(albumData, "uri"),
		}

		if visualIdentity, ok := getNestedMap(albumData, "visualIdentity"); ok {
			if avatarImage, ok := getNestedMap(visualIdentity, "avatarImage"); ok {
				if sources, ok := avatarImage["sources"].([]interface{}); ok && len(sources) > 0 {
					if img, ok := sources[0].(map[string]interface{}); ok {
						album.Images = append(album.Images, Image{
							URL:    getString(img, "url"),
							Width:  int(getFloat(img, "width")),
							Height: int(getFloat(img, "height")),
						})
					}
				}
			}
		}
	}

	// Get duration
	durationMs := 0
	if duration, ok := getNestedMap(trackData, "duration"); ok {
		durationMs = int(getFloat(duration, "totalMilliseconds"))
	}

	// Check explicit
	explicit := false
	if contentRating, ok := getNestedMap(trackData, "contentRating"); ok {
		explicit = getString(contentRating, "label") == "EXPLICIT"
	}

	track := &Track{
		ID:         getString(trackData, "id"),
		Name:       getString(trackData, "name"),
		Artists:    artists,
		Album:      album,
		DurationMs: durationMs,
		Explicit:   explicit,
		ExternalURLs: map[string]string{
			"spotify": fmt.Sprintf("https://open.spotify.com/track/%s", trackID),
		},
	}

	return track, nil
}

// Search searches for tracks (uses public search endpoint)
func (c *Client) Search(query string, limit int) ([]Track, error) {
	if err := c.ensureToken(); err != nil {
		return nil, err
	}

	c.mu.RLock()
	tok := c.token
	c.mu.RUnlock()

	if limit <= 0 {
		limit = 20
	}
	if limit > 50 {
		limit = 50
	}

	params := url.Values{
		"q":      {query},
		"type":   {"track"},
		"limit":  {strconv.Itoa(limit)},
		"market": {"US"},
	}

	searchURL := fmt.Sprintf("https://api.spotify.com/v1/search?%s", params.Encode())

	c.rateLimit()

	req, err := http.NewRequest("GET", searchURL, nil)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Authorization", "Bearer "+tok.AccessToken)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("search failed: HTTP %d", resp.StatusCode)
	}

	var data struct {
		Tracks struct {
			Items []struct {
				ID      string `json:"id"`
				Name    string `json:"name"`
				Artists []struct {
					ID   string `json:"id"`
					Name string `json:"name"`
				} `json:"artists"`
				Album struct {
					ID     string `json:"id"`
					Name   string `json:"name"`
					Images []struct {
						URL    string `json:"url"`
						Width  int    `json:"width"`
						Height int    `json:"height"`
					} `json:"images"`
				} `json:"album"`
				DurationMs int  `json:"duration_ms"`
				Explicit   bool `json:"explicit"`
			} `json:"items"`
		} `json:"tracks"`
	}

	if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
		return nil, err
	}

	var tracks []Track
	for _, item := range data.Tracks.Items {
		var artists []Artist
		for _, a := range item.Artists {
			artists = append(artists, Artist{
				ID:   a.ID,
				Name: a.Name,
			})
		}

		var images []Image
		for _, img := range item.Album.Images {
			images = append(images, Image{
				URL:    img.URL,
				Width:  img.Width,
				Height: img.Height,
			})
		}

		tracks = append(tracks, Track{
			ID:         item.ID,
			Name:       item.Name,
			Artists:    artists,
			DurationMs: item.DurationMs,
			Explicit:   item.Explicit,
			Album: Album{
				ID:     item.Album.ID,
				Name:   item.Album.Name,
				Images: images,
			},
			ExternalURLs: map[string]string{
				"spotify": fmt.Sprintf("https://open.spotify.com/track/%s", item.ID),
			},
		})
	}

	return tracks, nil
}

// Helper functions

func generateDeviceID() string {
	b := make([]byte, 16)
	rand.Read(b)
	return hex.EncodeToString(b)
}

func getNestedMap(m map[string]interface{}, keys ...string) (map[string]interface{}, bool) {
	current := m
	for _, key := range keys {
		next, ok := current[key].(map[string]interface{})
		if !ok {
			return nil, false
		}
		current = next
	}
	return current, true
}

func getString(m map[string]interface{}, key string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

func getFloat(m map[string]interface{}, key string) float64 {
	switch v := m[key].(type) {
	case float64:
		return v
	case float32:
		return float64(v)
	case int:
		return float64(v)
	case string:
		f, _ := strconv.ParseFloat(v, 64)
		return f
	}
	return 0
}

// URL parsing helpers

var spotifyIDRegex = regexp.MustCompile(`^[A-Za-z0-9]{10,}$`)

// ParseSpotifyURL extracts the type and ID from a Spotify URL
func ParseSpotifyURL(urlStr string) (itemType, itemID string, err error) {
	urlStr = strings.TrimSpace(urlStr)
	if urlStr == "" {
		return "", "", errors.New("invalid Spotify URL")
	}
	if matches := regexp.MustCompile(`(?i)^spotify:(track|album|playlist|artist):([A-Za-z0-9]+)$`).FindStringSubmatch(urlStr); len(matches) == 3 {
		return strings.ToLower(matches[1]), matches[2], nil
	}

	parsed, parseErr := parseSpotifyWebURL(urlStr)
	if parseErr != nil {
		return "", "", parseErr
	}
	return parsed.itemType, parsed.itemID, nil
}

type parsedSpotifyWebURL struct {
	itemType string
	itemID   string
}

func parseSpotifyWebURL(raw string) (parsedSpotifyWebURL, error) {
	if !strings.Contains(raw, "://") {
		lower := strings.ToLower(raw)
		if strings.HasPrefix(lower, "open.spotify.com/") || strings.HasPrefix(lower, "play.spotify.com/") {
			raw = "https://" + raw
		}
	}

	u, err := url.Parse(raw)
	if err != nil {
		return parsedSpotifyWebURL{}, err
	}
	if value := u.Query().Get("uri"); value != "" {
		itemType, itemID, err := ParseSpotifyURL(value)
		if err != nil {
			return parsedSpotifyWebURL{}, err
		}
		return parsedSpotifyWebURL{itemType: itemType, itemID: itemID}, nil
	}

	host := strings.TrimPrefix(strings.ToLower(u.Host), "www.")
	if host != "open.spotify.com" && host != "play.spotify.com" && host != "embed.spotify.com" {
		return parsedSpotifyWebURL{}, errors.New("invalid Spotify URL")
	}

	parts := make([]string, 0, 4)
	for _, part := range strings.Split(u.Path, "/") {
		part = strings.TrimSpace(part)
		if part != "" {
			parts = append(parts, part)
		}
	}
	if len(parts) > 0 && strings.HasPrefix(strings.ToLower(parts[0]), "intl-") {
		parts = parts[1:]
	}
	if len(parts) > 0 && strings.EqualFold(parts[0], "embed") {
		parts = parts[1:]
	}
	if len(parts) >= 4 && strings.EqualFold(parts[0], "user") && strings.EqualFold(parts[2], "playlist") && spotifyIDRegex.MatchString(parts[3]) {
		return parsedSpotifyWebURL{itemType: "playlist", itemID: parts[3]}, nil
	}
	if len(parts) >= 2 {
		itemType := strings.ToLower(parts[0])
		switch itemType {
		case "track", "album", "playlist", "artist":
			if spotifyIDRegex.MatchString(parts[1]) {
				return parsedSpotifyWebURL{itemType: itemType, itemID: parts[1]}, nil
			}
		}
	}

	return parsedSpotifyWebURL{}, errors.New("invalid Spotify URL")
}
