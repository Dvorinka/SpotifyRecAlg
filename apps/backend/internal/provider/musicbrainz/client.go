package musicbrainz

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const (
	defaultBaseURL = "https://musicbrainz.org/ws/2"
	defaultTimeout = 10 * time.Second
)

type Config struct {
	AppName    string
	Contact    string
	Version    string
	BaseURL    string
	HTTPClient *http.Client
	Timeout    time.Duration
	MinDelay   time.Duration
}

type Client struct {
	appName    string
	contact    string
	version    string
	baseURL    string
	httpClient *http.Client
	minDelay   time.Duration

	mu        sync.Mutex
	lastCall  time.Time
	lastError string
}

type Recording struct {
	ID       string
	Title    string
	Artist   string
	ArtistID string
	ISRC     string
	Genres   []string
	Tags     []string
}

func New(cfg Config) *Client {
	timeout := cfg.Timeout
	if timeout <= 0 {
		timeout = defaultTimeout
	}
	httpClient := cfg.HTTPClient
	if httpClient == nil {
		httpClient = &http.Client{Timeout: timeout}
	}
	baseURL := strings.TrimRight(cfg.BaseURL, "/")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	minDelay := cfg.MinDelay
	if minDelay <= 0 {
		minDelay = time.Second
	}
	version := strings.TrimSpace(cfg.Version)
	if version == "" {
		version = "0.1.0"
	}
	return &Client{
		appName:    strings.TrimSpace(cfg.AppName),
		contact:    strings.TrimSpace(cfg.Contact),
		version:    version,
		baseURL:    baseURL,
		httpClient: httpClient,
		minDelay:   minDelay,
	}
}

func (c *Client) Configured() bool {
	return c.appName != "" && c.contact != ""
}

func (c *Client) LastError() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.lastError
}

func (c *Client) LookupByISRC(ctx context.Context, isrc string) (Recording, []byte, error) {
	isrc = strings.ToUpper(strings.TrimSpace(isrc))
	if isrc == "" {
		return Recording{}, nil, errors.New("isrc is required")
	}
	params := url.Values{}
	params.Set("fmt", "json")
	params.Set("inc", "artist-credits+isrcs+tags")
	payload, err := c.get(ctx, "/isrc/"+url.PathEscape(isrc), params)
	if err != nil {
		return Recording{}, payload, err
	}
	recording, err := parseISRCRecording(payload, isrc)
	return recording, payload, err
}

func (c *Client) SearchRecording(ctx context.Context, title, artist string) (Recording, []byte, error) {
	title = strings.TrimSpace(title)
	artist = strings.TrimSpace(artist)
	if title == "" {
		return Recording{}, nil, errors.New("title is required")
	}
	query := `recording:"` + escapeQuery(title) + `"`
	if artist != "" {
		query += ` AND artist:"` + escapeQuery(artist) + `"`
	}
	params := url.Values{}
	params.Set("fmt", "json")
	params.Set("query", query)
	params.Set("limit", "1")
	payload, err := c.get(ctx, "/recording", params)
	if err != nil {
		return Recording{}, payload, err
	}
	recording, err := parseSearchRecording(payload)
	return recording, payload, err
}

func (c *Client) get(ctx context.Context, path string, params url.Values) ([]byte, error) {
	if !c.Configured() {
		err := errors.New("musicbrainz app name and contact are required")
		c.setLastError(err.Error())
		return nil, err
	}
	if err := c.wait(ctx); err != nil {
		return nil, err
	}
	endpoint := c.baseURL + path
	if encoded := params.Encode(); encoded != "" {
		endpoint += "?" + encoded
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.userAgent())

	resp, err := c.httpClient.Do(req)
	if err != nil {
		c.setLastError(err.Error())
		return nil, err
	}
	defer resp.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		c.setLastError(err.Error())
		return payload, err
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		err := fmt.Errorf("musicbrainz request failed with status %d", resp.StatusCode)
		c.setLastError(err.Error())
		return payload, err
	}
	c.setLastError("")
	return payload, nil
}

func (c *Client) wait(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	wait := c.minDelay - time.Since(c.lastCall)
	if wait > 0 {
		timer := time.NewTimer(wait)
		c.mu.Unlock()
		select {
		case <-ctx.Done():
			timer.Stop()
			c.mu.Lock()
			return ctx.Err()
		case <-timer.C:
		}
		c.mu.Lock()
	}
	c.lastCall = time.Now()
	return nil
}

func (c *Client) userAgent() string {
	return fmt.Sprintf("%s/%s (%s)", c.appName, c.version, c.contact)
}

func (c *Client) setLastError(message string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.lastError = message
}

func parseISRCRecording(payload []byte, isrc string) (Recording, error) {
	var decoded struct {
		Recordings []recordingJSON `json:"recordings"`
	}
	if err := json.Unmarshal(payload, &decoded); err != nil {
		return Recording{}, fmt.Errorf("decode musicbrainz isrc: %w", err)
	}
	if len(decoded.Recordings) == 0 {
		return Recording{}, errors.New("musicbrainz isrc lookup returned no recordings")
	}
	return decoded.Recordings[0].toRecording(isrc), nil
}

func parseSearchRecording(payload []byte) (Recording, error) {
	var decoded struct {
		Recordings []recordingJSON `json:"recordings"`
	}
	if err := json.Unmarshal(payload, &decoded); err != nil {
		return Recording{}, fmt.Errorf("decode musicbrainz recording search: %w", err)
	}
	if len(decoded.Recordings) == 0 {
		return Recording{}, errors.New("musicbrainz recording search returned no matches")
	}
	return decoded.Recordings[0].toRecording(""), nil
}

type recordingJSON struct {
	ID           string `json:"id"`
	Title        string `json:"title"`
	ArtistCredit []struct {
		Artist struct {
			ID   string `json:"id"`
			Name string `json:"name"`
		} `json:"artist"`
	} `json:"artist-credit"`
	ISRCs []string `json:"isrcs"`
	Tags  []struct {
		Name string `json:"name"`
	} `json:"tags"`
	Genres []struct {
		Name string `json:"name"`
	} `json:"genres"`
}

func (r recordingJSON) toRecording(fallbackISRC string) Recording {
	out := Recording{ID: r.ID, Title: r.Title, ISRC: fallbackISRC}
	if len(r.ArtistCredit) > 0 {
		out.Artist = r.ArtistCredit[0].Artist.Name
		out.ArtistID = r.ArtistCredit[0].Artist.ID
	}
	if out.ISRC == "" && len(r.ISRCs) > 0 {
		out.ISRC = strings.ToUpper(r.ISRCs[0])
	}
	for _, genre := range r.Genres {
		if genre.Name != "" {
			out.Genres = append(out.Genres, genre.Name)
		}
	}
	for _, tag := range r.Tags {
		if tag.Name != "" {
			out.Tags = append(out.Tags, tag.Name)
		}
	}
	return out
}

func escapeQuery(value string) string {
	return strings.ReplaceAll(value, `"`, `\"`)
}
