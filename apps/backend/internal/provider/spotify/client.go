package spotify

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	defaultAccountsBaseURL = "https://accounts.spotify.com"
	defaultAPIBaseURL      = "https://api.spotify.com/v1"
	defaultTimeout         = 10 * time.Second
)

var ErrNotConfigured = errors.New("spotify credentials are not configured")

type Config struct {
	ClientID        string
	ClientSecret    string
	BearerToken     string
	Market          string
	AccountsBaseURL string
	APIBaseURL      string
	HTTPClient      *http.Client
	Timeout         time.Duration
	MaxRetries      int
}

type Client struct {
	clientID        string
	clientSecret    string
	staticToken     string
	defaultMarket   string
	accountsBaseURL string
	apiBaseURL      string
	httpClient      *http.Client
	timeout         time.Duration
	maxRetries      int

	mu        sync.Mutex
	token     string
	expiresAt time.Time
	lastError string
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
	accountsBaseURL := strings.TrimRight(cfg.AccountsBaseURL, "/")
	if accountsBaseURL == "" {
		accountsBaseURL = defaultAccountsBaseURL
	}
	apiBaseURL := strings.TrimRight(cfg.APIBaseURL, "/")
	if apiBaseURL == "" {
		apiBaseURL = defaultAPIBaseURL
	}
	maxRetries := cfg.MaxRetries
	if maxRetries <= 0 {
		maxRetries = 2
	}
	return &Client{
		clientID:        strings.TrimSpace(cfg.ClientID),
		clientSecret:    strings.TrimSpace(cfg.ClientSecret),
		staticToken:     strings.TrimSpace(cfg.BearerToken),
		defaultMarket:   strings.ToUpper(strings.TrimSpace(cfg.Market)),
		accountsBaseURL: accountsBaseURL,
		apiBaseURL:      apiBaseURL,
		httpClient:      httpClient,
		timeout:         timeout,
		maxRetries:      maxRetries,
	}
}

func (c *Client) Configured() bool {
	return c.staticToken != "" || (c.clientID != "" && c.clientSecret != "")
}

func (c *Client) TokenMode() string {
	if c.staticToken != "" {
		return "static_bearer"
	}
	if c.clientID != "" && c.clientSecret != "" {
		return "client_credentials"
	}
	return "unconfigured"
}

func (c *Client) LastError() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.lastError
}

func (c *Client) GetTrack(ctx context.Context, id, market string) (Track, []byte, error) {
	var out Track
	payload, err := c.get(ctx, "/tracks/"+url.PathEscape(id), marketParams(marketOrDefault(market, c.defaultMarket)), &out)
	return out, payload, err
}

func (c *Client) GetAudioFeatures(ctx context.Context, id string) (AudioFeatures, []byte, error) {
	var out AudioFeatures
	payload, err := c.get(ctx, "/audio-features/"+url.PathEscape(id), nil, &out)
	return out, payload, err
}

func (c *Client) Search(ctx context.Context, query, itemType, market string, limit int) (SearchResult, []byte, error) {
	itemType = strings.ToLower(strings.TrimSpace(itemType))
	if itemType == "" {
		itemType = "track"
	}
	if limit <= 0 {
		limit = 5
	}
	if limit > 10 {
		limit = 10
	}
	params := url.Values{}
	params.Set("q", query)
	params.Set("type", itemType)
	params.Set("limit", strconv.Itoa(limit))
	if market = marketOrDefault(market, c.defaultMarket); market != "" {
		params.Set("market", market)
	}
	var out SearchResult
	payload, err := c.get(ctx, "/search", params, &out)
	return out, payload, err
}

func (c *Client) GetAlbumTracks(ctx context.Context, id, market string, limit int) ([]TrackRef, []byte, error) {
	return c.getPagedTrackRefs(ctx, "/albums/"+url.PathEscape(id)+"/tracks", "items", market, limit)
}

func (c *Client) GetPlaylistTracks(ctx context.Context, id, market string, limit int) ([]TrackRef, []byte, error) {
	limit = normalizeCollectionLimit(limit)
	refs := make([]TrackRef, 0, limit)
	var lastPayload []byte
	for offset := 0; len(refs) < limit; offset += 50 {
		params := marketParams(marketOrDefault(market, c.defaultMarket))
		params.Set("limit", strconv.Itoa(minInt(50, limit-len(refs))))
		params.Set("offset", strconv.Itoa(offset))
		params.Set("fields", "items(track(id,is_local,type)),next")
		payload, err := c.getRaw(ctx, "/playlists/"+url.PathEscape(id)+"/tracks", params)
		if err != nil {
			return nil, payload, err
		}
		lastPayload = payload
		var page struct {
			Items []struct {
				Track TrackRef `json:"track"`
			} `json:"items"`
			Next string `json:"next"`
		}
		if err := json.Unmarshal(payload, &page); err != nil {
			return nil, payload, fmt.Errorf("decode playlist tracks: %w", err)
		}
		for _, item := range page.Items {
			if item.Track.ID != "" && !item.Track.IsLocal {
				refs = append(refs, item.Track)
				if len(refs) >= limit {
					break
				}
			}
		}
		if page.Next == "" || len(page.Items) == 0 {
			break
		}
	}
	return refs, lastPayload, nil
}

func (c *Client) GetArtistTopTracks(ctx context.Context, id, market string) ([]Track, []byte, error) {
	params := marketParams(marketOrDefault(market, c.defaultMarket))
	if params.Get("market") == "" {
		params.Set("market", "US")
	}
	var out struct {
		Tracks []Track `json:"tracks"`
	}
	payload, err := c.get(ctx, "/artists/"+url.PathEscape(id)+"/top-tracks", params, &out)
	return out.Tracks, payload, err
}

func (c *Client) getPagedTrackRefs(ctx context.Context, path, listField, market string, limit int) ([]TrackRef, []byte, error) {
	limit = normalizeCollectionLimit(limit)
	refs := make([]TrackRef, 0, limit)
	var lastPayload []byte
	for offset := 0; len(refs) < limit; offset += 50 {
		params := marketParams(marketOrDefault(market, c.defaultMarket))
		params.Set("limit", strconv.Itoa(minInt(50, limit-len(refs))))
		params.Set("offset", strconv.Itoa(offset))
		payload, err := c.getRaw(ctx, path, params)
		if err != nil {
			return nil, payload, err
		}
		lastPayload = payload
		var page struct {
			Items []TrackRef `json:"items"`
			Next  string     `json:"next"`
		}
		if err := json.Unmarshal(payload, &page); err != nil {
			return nil, payload, fmt.Errorf("decode %s: %w", listField, err)
		}
		for _, item := range page.Items {
			if item.ID != "" {
				refs = append(refs, item)
				if len(refs) >= limit {
					break
				}
			}
		}
		if page.Next == "" || len(page.Items) == 0 {
			break
		}
	}
	return refs, lastPayload, nil
}

func (c *Client) get(ctx context.Context, path string, params url.Values, out any) ([]byte, error) {
	payload, err := c.getRaw(ctx, path, params)
	if err != nil {
		return payload, err
	}
	if err := json.Unmarshal(payload, out); err != nil {
		return payload, fmt.Errorf("decode spotify response: %w", err)
	}
	return payload, nil
}

func (c *Client) getRaw(ctx context.Context, path string, params url.Values) ([]byte, error) {
	if params == nil {
		params = url.Values{}
	}
	endpoint := c.apiBaseURL + path
	if encoded := params.Encode(); encoded != "" {
		endpoint += "?" + encoded
	}
	return c.doJSON(ctx, http.MethodGet, endpoint, nil, true)
}

func (c *Client) accessToken(ctx context.Context) (string, error) {
	if c.staticToken != "" {
		return c.staticToken, nil
	}
	if c.clientID == "" || c.clientSecret == "" {
		c.setLastError(ErrNotConfigured.Error())
		return "", ErrNotConfigured
	}

	c.mu.Lock()
	if c.token != "" && time.Now().Add(60*time.Second).Before(c.expiresAt) {
		token := c.token
		c.mu.Unlock()
		return token, nil
	}
	c.mu.Unlock()

	body := strings.NewReader("grant_type=client_credentials")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.accountsBaseURL+"/api/token", body)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	credential := base64.StdEncoding.EncodeToString([]byte(c.clientID + ":" + c.clientSecret))
	req.Header.Set("Authorization", "Basic "+credential)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		c.setLastError(err.Error())
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		_, _ = io.Copy(io.Discard, resp.Body)
		err := fmt.Errorf("spotify token request failed with status %d", resp.StatusCode)
		c.setLastError(err.Error())
		return "", err
	}
	var decoded struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
		TokenType   string `json:"token_type"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&decoded); err != nil {
		c.setLastError(err.Error())
		return "", fmt.Errorf("decode spotify token: %w", err)
	}
	if decoded.AccessToken == "" {
		err := errors.New("spotify token response did not include an access token")
		c.setLastError(err.Error())
		return "", err
	}
	expiresIn := time.Duration(decoded.ExpiresIn) * time.Second
	if expiresIn <= 0 {
		expiresIn = time.Hour
	}
	c.mu.Lock()
	c.token = decoded.AccessToken
	c.expiresAt = time.Now().Add(expiresIn)
	c.lastError = ""
	c.mu.Unlock()
	return decoded.AccessToken, nil
}

func (c *Client) doJSON(ctx context.Context, method, endpoint string, body []byte, authenticate bool) ([]byte, error) {
	var lastErr error
	for attempt := 0; attempt <= c.maxRetries; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(time.Duration(attempt) * 250 * time.Millisecond):
			}
		}

		var reader io.Reader
		if len(body) > 0 {
			reader = bytes.NewReader(body)
		}
		req, err := http.NewRequestWithContext(ctx, method, endpoint, reader)
		if err != nil {
			return nil, err
		}
		req.Header.Set("Accept", "application/json")
		if authenticate {
			token, err := c.accessToken(ctx)
			if err != nil {
				return nil, err
			}
			req.Header.Set("Authorization", "Bearer "+token)
		}

		resp, err := c.httpClient.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			lastErr = err
			continue
		}
		payload, readErr := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
		closeErr := resp.Body.Close()
		if readErr != nil {
			return payload, readErr
		}
		if closeErr != nil {
			return payload, closeErr
		}
		if resp.StatusCode >= 200 && resp.StatusCode <= 299 {
			c.setLastError("")
			return payload, nil
		}
		if resp.StatusCode == http.StatusUnauthorized {
			c.mu.Lock()
			c.token = ""
			c.expiresAt = time.Time{}
			c.mu.Unlock()
		}
		lastErr = spotifyHTTPError{StatusCode: resp.StatusCode, Body: string(payload)}
		if resp.StatusCode == http.StatusTooManyRequests {
			wait := retryAfter(resp.Header.Get("Retry-After"))
			if wait > 0 && attempt < c.maxRetries {
				select {
				case <-ctx.Done():
					return payload, ctx.Err()
				case <-time.After(wait):
					continue
				}
			}
		}
		if resp.StatusCode < 500 && resp.StatusCode != http.StatusUnauthorized && resp.StatusCode != http.StatusTooManyRequests {
			break
		}
	}
	if lastErr == nil {
		lastErr = errors.New("spotify request failed")
	}
	c.setLastError(lastErr.Error())
	return nil, lastErr
}

func (c *Client) setLastError(message string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.lastError = message
}

type spotifyHTTPError struct {
	StatusCode int
	Body       string
}

func (e spotifyHTTPError) Error() string {
	if e.Body == "" {
		return fmt.Sprintf("spotify request failed with status %d", e.StatusCode)
	}
	return fmt.Sprintf("spotify request failed with status %d", e.StatusCode)
}

func IsNotFound(err error) bool {
	var httpErr spotifyHTTPError
	return errors.As(err, &httpErr) && httpErr.StatusCode == http.StatusNotFound
}

func retryAfter(value string) time.Duration {
	if value == "" {
		return 0
	}
	if seconds, err := strconv.Atoi(strings.TrimSpace(value)); err == nil {
		return time.Duration(seconds) * time.Second
	}
	if when, err := http.ParseTime(value); err == nil {
		return time.Until(when)
	}
	return 0
}

func marketParams(market string) url.Values {
	params := url.Values{}
	if market = strings.ToUpper(strings.TrimSpace(market)); market != "" {
		params.Set("market", market)
	}
	return params
}

func marketOrDefault(market, fallback string) string {
	if market = strings.ToUpper(strings.TrimSpace(market)); market != "" {
		return market
	}
	return strings.ToUpper(strings.TrimSpace(fallback))
}

func normalizeCollectionLimit(limit int) int {
	if limit <= 0 {
		return 100
	}
	if limit > 100 {
		return 100
	}
	return limit
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
