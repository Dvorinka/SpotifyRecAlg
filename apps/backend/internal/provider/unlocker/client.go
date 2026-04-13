package unlocker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"
)

var (
	ErrNotConfigured = errors.New("unlocker service not configured")
	ErrTrackNotFound = errors.New("track not found")
)

// Client for the Python unlocker service (auth-free Spotify access)
type Client struct {
	baseURL string
	client  *http.Client
}

func NewClient(baseURL string) *Client {
	if baseURL == "" {
		return nil
	}
	return &Client{
		baseURL: baseURL,
		client:  &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *Client) Configured() bool {
	return c != nil && c.baseURL != ""
}

type TrackResponse struct {
	ID           string            `json:"id"`
	Title        string            `json:"title"`
	Artist       string            `json:"artist"`
	Artists      []string          `json:"artists"`
	Album        string            `json:"album"`
	DurationMS   int               `json:"duration_ms"`
	Explicit     bool              `json:"explicit"`
	ExternalURLs map[string]string `json:"external_urls"`
}

type ImportRequest struct {
	URL string `json:"url"`
}

type ImportResponse struct {
	Track  *TrackResponse    `json:"track"`
	Links  map[string]string `json:"links"`
	Parsed *ParsedInfo       `json:"parsed,omitempty"`
	Note   string            `json:"note,omitempty"`
}

type ParsedInfo struct {
	Service string `json:"service"`
	Type    string `json:"type"`
	ID      string `json:"id"`
}

type LinksResponse struct {
	SpotifyID string                 `json:"spotify_id"`
	ISRC      string                 `json:"isrc"`
	Links     map[string]LinkDetails `json:"links"`
}

type LinkDetails struct {
	URL       string `json:"url"`
	ID        string `json:"id"`
}

// ImportFromURL imports a track from any streaming service URL (auth-free)
func (c *Client) ImportFromURL(ctx context.Context, url string) (*ImportResponse, error) {
	if !c.Configured() {
		return nil, ErrNotConfigured
	}

	reqBody, _ := json.Marshal(ImportRequest{URL: url})
	req, err := http.NewRequestWithContext(ctx, "POST", c.baseURL+"/import", bytes.NewReader(reqBody))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("unlocker request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("unlocker returned %d: %s", resp.StatusCode, string(body))
	}

	var result ImportResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("failed to parse unlocker response: %w", err)
	}

	return &result, nil
}

// GetTrack gets a track by Spotify ID (auth-free)
func (c *Client) GetTrack(ctx context.Context, trackID string) (*TrackResponse, error) {
	if !c.Configured() {
		return nil, ErrNotConfigured
	}

	req, err := http.NewRequestWithContext(ctx, "GET", c.baseURL+"/spotify/track/"+trackID, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("unlocker request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode == http.StatusNotFound {
		return nil, ErrTrackNotFound
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("unlocker returned %d: %s", resp.StatusCode, string(body))
	}

	var result TrackResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("failed to parse unlocker response: %w", err)
	}

	return &result, nil
}

// GetLinks gets cross-platform links for a Spotify track
func (c *Client) GetLinks(ctx context.Context, spotifyID string) (*LinksResponse, error) {
	if !c.Configured() {
		return nil, ErrNotConfigured
	}

	req, err := http.NewRequestWithContext(ctx, "GET", c.baseURL+"/links/"+spotifyID, nil)
	if err != nil {
		return nil, err
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("unlocker request failed: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("unlocker returned %d: %s", resp.StatusCode, string(body))
	}

	var result LinksResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("failed to parse unlocker response: %w", err)
	}

	return &result, nil
}
