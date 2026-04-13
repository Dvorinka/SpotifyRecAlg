package provider

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/musicbrainz"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/songlink"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/spotify"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/urlparser"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/provider/webplayer"
	"github.com/tdvorak/spotifyrecalg/apps/backend/internal/recommendation"
)

type ServiceConfig struct {
	DefaultMarket string
	CacheTTL      time.Duration
	Version       string
}

type Service struct {
	store         Store
	spotify       *spotify.Client
	webplayer     *webplayer.Client
	songlink      *songlink.Client
	urlparser     *urlparser.Parser
	musicbrainz   *musicbrainz.Client
	defaultMarket string
	cacheTTL      time.Duration
	now           func() time.Time
}

func NewService(store Store, spotifyClient *spotify.Client, webplayerClient *webplayer.Client, songlinkClient *songlink.Client, musicBrainzClient *musicbrainz.Client, cfg ServiceConfig) *Service {
	cacheTTL := cfg.CacheTTL
	if cacheTTL <= 0 {
		cacheTTL = 24 * time.Hour
	}
	return &Service{
		store:         store,
		spotify:       spotifyClient,
		webplayer:     webplayerClient,
		songlink:      songlinkClient,
		urlparser:     urlparser.NewParser(),
		musicbrainz:   musicBrainzClient,
		defaultMarket: strings.ToUpper(strings.TrimSpace(cfg.DefaultMarket)),
		cacheTTL:      cacheTTL,
		now:           func() time.Time { return time.Now().UTC() },
	}
}

func (s *Service) ImportSpotify(ctx context.Context, req ImportRequest) (ImportResponse, error) {
	// Try official Spotify API first (more reliable, has audio features)
	if s.spotify != nil && s.spotify.Configured() {
		return s.importFromOfficialAPI(ctx, req)
	}

	// Fall back to native webplayer client (auth-free, no API keys needed)
	if s.webplayer != nil && s.webplayer.Configured() {
		return s.importFromWebPlayer(ctx, req)
	}

	return ImportResponse{}, spotify.ErrNotConfigured
}

func (s *Service) importFromOfficialAPI(ctx context.Context, req ImportRequest) (ImportResponse, error) {
	persist := true
	if req.Persist != nil {
		persist = *req.Persist
	}
	limit := capLimit(req.Limit, 100)
	market := s.market(req.Market)
	parsed, sourceWarnings, err := s.resolveSpotifySource(ctx, req.Source)
	if err != nil {
		return ImportResponse{}, err
	}

	job := ImportJob{
		ID:          newID("import"),
		Provider:    ProviderSpotify,
		SourceType:  parsed.Type,
		SourceValue: parsed.ID,
		Market:      market,
		Status:      "running",
		StartedAt:   s.now(),
	}
	if persist {
		if err := s.store.CreateImportJob(ctx, job); err != nil {
			return ImportResponse{}, err
		}
	}

	tracks, skipped, warnings, err := s.importSpotifyTracks(ctx, parsed, market, limit, boolDefault(req.EnrichMusicBrainz, true), req.AllowMissingFields)
	warnings = append(sourceWarnings, warnings...)
	if err != nil {
		job.Status = "failed"
		job.Warnings = append(warnings, err.Error())
		job.FinishedAt = s.now()
		if persist {
			_ = s.store.FinishImportJob(ctx, job)
		}
		return ImportResponse{}, err
	}

	imported, updated := 0, 0
	if persist && len(tracks) > 0 {
		existingIDs := make([]string, 0, len(tracks))
		for _, track := range tracks {
			existingIDs = append(existingIDs, track.ID)
		}
		existing, err := s.store.GetTracksByIDs(ctx, existingIDs)
		if err != nil {
			return ImportResponse{}, err
		}
		existingSet := make(map[string]struct{}, len(existing))
		for _, track := range existing {
			existingSet[track.ID] = struct{}{}
		}
		for _, track := range tracks {
			if _, ok := existingSet[track.ID]; ok {
				updated++
			} else {
				imported++
			}
		}
		if err := s.store.UpsertTracks(ctx, tracks); err != nil {
			return ImportResponse{}, err
		}
		if err := s.upsertTrackEnrichments(ctx, tracks); err != nil {
			return ImportResponse{}, err
		}
	}

	job.Status = "succeeded"
	job.ImportedTracks = imported
	job.UpdatedTracks = updated
	job.Skipped = skipped
	job.Warnings = warnings
	job.FinishedAt = s.now()
	if persist {
		if err := s.store.FinishImportJob(ctx, job); err != nil {
			return ImportResponse{}, err
		}
	}

	return ImportResponse{
		ImportID:       job.ID,
		ImportedTracks: imported,
		UpdatedTracks:  updated,
		Skipped:        skipped,
		Warnings:       warnings,
	}, nil
}

func (s *Service) resolveSpotifySource(ctx context.Context, source Source) (spotify.ParsedSource, []string, error) {
	_ = ctx
	parsed, err := spotify.ParseSource(source.Type, source.Value)
	if err == nil {
		return parsed, nil, nil
	}

	if strings.ToLower(strings.TrimSpace(source.Type)) != "url" {
		return spotify.ParsedSource{}, nil, err
	}
	parsedURL := s.urlparser.ParseURL(source.Value)
	if parsedURL == nil || parsedURL.Service == urlparser.Spotify {
		return spotify.ParsedSource{}, nil, err
	}
	if s.songlink == nil || !s.songlink.Configured() {
		return spotify.ParsedSource{}, nil, err
	}

	links, linkErr := s.songlink.GetLinks(parsedURL.URL)
	if linkErr != nil {
		return spotify.ParsedSource{}, nil, fmt.Errorf("could not resolve %s URL to Spotify: %w", parsedURL.Service, linkErr)
	}
	if strings.TrimSpace(links.SpotifyID) == "" {
		return spotify.ParsedSource{}, nil, fmt.Errorf("could not resolve %s URL to a Spotify track", parsedURL.Service)
	}

	spotifyID := strings.TrimSpace(links.SpotifyID)
	return spotify.ParsedSource{
		Type: "track",
		ID:   spotifyID,
		URL:  "https://open.spotify.com/track/" + spotifyID,
	}, []string{"resolved " + string(parsedURL.Service) + " URL to Spotify via Song.link"}, nil
}

func (s *Service) SearchSpotify(ctx context.Context, req SearchRequest) (SearchResponse, error) {
	if s.spotify == nil || !s.spotify.Configured() {
		// Try webplayer search if available (auth-free)
		if s.webplayer != nil && s.webplayer.Configured() {
			return s.searchViaWebPlayer(ctx, req)
		}
		return SearchResponse{}, spotify.ErrNotConfigured
	}
	itemType := strings.ToLower(strings.TrimSpace(req.Type))
	if itemType == "" {
		itemType = "track"
	}
	if !validSearchType(itemType) {
		return SearchResponse{}, errors.New("search type must be track, album, artist, or playlist")
	}
	limit := capSearchLimit(req.Limit)
	market := s.market(req.Market)
	result, _, warnings, err := s.spotifySearch(ctx, req.Query, itemType, market, limit)
	if err != nil {
		return SearchResponse{}, err
	}
	ids, idWarnings := s.trackIDsFromSearch(ctx, result, itemType, market, limit)
	warnings = append(warnings, idWarnings...)
	tracks := make([]recommendation.Track, 0, len(ids))
	skipped := 0
	for _, id := range ids {
		track, trackWarnings, ok := s.buildTrack(ctx, id, market, boolDefault(req.EnrichMusicBrainz, true), req.AllowMissingFields)
		warnings = append(warnings, trackWarnings...)
		if !ok {
			skipped++
			continue
		}
		tracks = append(tracks, track)
	}
	persisted := 0
	if req.Persist && len(tracks) > 0 {
		if err := s.store.UpsertTracks(ctx, tracks); err != nil {
			return SearchResponse{}, err
		}
		if err := s.upsertTrackEnrichments(ctx, tracks); err != nil {
			return SearchResponse{}, err
		}
		persisted = len(tracks)
	}
	return SearchResponse{Tracks: tracks, Persisted: persisted, Skipped: skipped, Warnings: warnings}, nil
}

func (s *Service) trackIDsFromSearch(ctx context.Context, result spotify.SearchResult, itemType, market string, limit int) ([]string, []string) {
	var warnings []string
	ids := make([]string, 0, limit)
	addID := func(id string) {
		if id == "" || len(ids) >= limit {
			return
		}
		ids = append(ids, id)
	}
	switch itemType {
	case "track":
		for _, item := range result.Tracks.Items {
			addID(item.ID)
		}
	case "album":
		for _, album := range result.Albums.Items {
			refs, _, cacheWarnings, err := s.spotifyAlbumTracks(ctx, album.ID, market, limit-len(ids))
			warnings = append(warnings, cacheWarnings...)
			if err != nil {
				warnings = append(warnings, fmt.Sprintf("spotify album %s skipped: %v", album.ID, err))
				continue
			}
			for _, ref := range refs {
				addID(ref.ID)
			}
		}
	case "artist":
		for _, artist := range result.Artists.Items {
			items, _, cacheWarnings, err := s.spotifyArtistTopTracks(ctx, artist.ID, market)
			warnings = append(warnings, cacheWarnings...)
			if err != nil {
				warnings = append(warnings, fmt.Sprintf("spotify artist %s skipped: %v", artist.ID, err))
				continue
			}
			for _, item := range items {
				addID(item.ID)
			}
		}
	case "playlist":
		for _, playlist := range result.Playlists.Items {
			refs, _, cacheWarnings, err := s.spotifyPlaylistTracks(ctx, playlist.ID, market, limit-len(ids))
			warnings = append(warnings, cacheWarnings...)
			if err != nil {
				warnings = append(warnings, fmt.Sprintf("spotify playlist %s skipped: %v", playlist.ID, err))
				continue
			}
			for _, ref := range refs {
				addID(ref.ID)
			}
		}
	}
	return ids, warnings
}

func (s *Service) EnrichMusicBrainz(ctx context.Context, req EnrichRequest) (EnrichResponse, error) {
	if s.musicbrainz == nil || !s.musicbrainz.Configured() {
		return EnrichResponse{}, errors.New("musicbrainz app name and contact are required")
	}
	tracks, err := s.store.GetTracksByIDs(ctx, req.TrackIDs)
	if err != nil {
		return EnrichResponse{}, err
	}
	byID := make(map[string]recommendation.Track, len(tracks))
	for _, track := range tracks {
		byID[track.ID] = track
	}
	var warnings []string
	updated, skipped := 0, 0
	for _, id := range req.TrackIDs {
		track, ok := byID[id]
		if !ok {
			skipped++
			warnings = append(warnings, "track not found: "+id)
			continue
		}
		if !req.Force && track.External["musicbrainz_recording_id"] != "" {
			skipped++
			continue
		}
		mb, raw, warn, ok := s.enrichTrack(ctx, track)
		if warn != "" {
			warnings = append(warnings, warn)
		}
		if !ok {
			skipped++
			continue
		}
		if track.External == nil {
			track.External = map[string]string{}
		}
		track.External["musicbrainz_recording_id"] = mb.ID
		if mb.ArtistID != "" {
			track.External["musicbrainz_artist_id"] = mb.ArtistID
		}
		if mb.ISRC != "" && track.External["isrc"] == "" {
			track.External["isrc"] = mb.ISRC
		}
		track.Genres = mergeStrings(track.Genres, mb.Genres...)
		track.Genres = mergeStrings(track.Genres, mb.Tags...)
		if err := s.store.UpsertTrack(ctx, track); err != nil {
			return EnrichResponse{}, err
		}
		if err := s.store.UpsertTrackEnrichment(ctx, TrackEnrichment{
			TrackID:                track.ID,
			Provider:               ProviderMusicBrainz,
			MusicBrainzRecordingID: mb.ID,
			MusicBrainzArtistID:    mb.ArtistID,
			ISRC:                   mb.ISRC,
			Payload:                raw,
			UpdatedAt:              s.now(),
		}); err != nil {
			return EnrichResponse{}, err
		}
		updated++
	}
	return EnrichResponse{Updated: updated, Skipped: skipped, Warnings: warnings}, nil
}

func (s *Service) Status(ctx context.Context) StatusResponse {
	stats, _ := s.store.ProviderCacheStats(ctx)
	now := s.now()
	spotifyStatus := ProviderStatus{CheckedAt: now}
	if s.spotify != nil {
		spotifyStatus.Configured = s.spotify.Configured()
		spotifyStatus.TokenMode = s.spotify.TokenMode()
		spotifyStatus.Available = s.spotify.Configured() && s.spotify.LastError() == ""
		spotifyStatus.LastError = s.spotify.LastError()
	}
	mbStatus := ProviderStatus{CheckedAt: now}
	if s.musicbrainz != nil {
		mbStatus.Configured = s.musicbrainz.Configured()
		mbStatus.TokenMode = "user_agent"
		mbStatus.Available = s.musicbrainz.Configured() && s.musicbrainz.LastError() == ""
		mbStatus.LastError = s.musicbrainz.LastError()
	}
	return StatusResponse{Spotify: spotifyStatus, MusicBrainz: mbStatus, Cache: stats}
}

func (s *Service) importSpotifyTracks(ctx context.Context, parsed spotify.ParsedSource, market string, limit int, enrichMB, allowMissing bool) ([]recommendation.Track, int, []string, error) {
	ids := []string{parsed.ID}
	var warnings []string
	switch parsed.Type {
	case "track":
	case "album":
		refs, _, cacheWarnings, err := s.spotifyAlbumTracks(ctx, parsed.ID, market, limit)
		if err != nil {
			return nil, 0, warnings, err
		}
		warnings = append(warnings, cacheWarnings...)
		ids = ids[:0]
		for _, ref := range refs {
			if ref.ID != "" {
				ids = append(ids, ref.ID)
			}
		}
	case "playlist":
		refs, _, cacheWarnings, err := s.spotifyPlaylistTracks(ctx, parsed.ID, market, limit)
		if err != nil {
			return nil, 0, warnings, err
		}
		warnings = append(warnings, cacheWarnings...)
		ids = ids[:0]
		for _, ref := range refs {
			if ref.ID != "" {
				ids = append(ids, ref.ID)
			}
		}
	case "artist":
		items, _, cacheWarnings, err := s.spotifyArtistTopTracks(ctx, parsed.ID, market)
		if err != nil {
			return nil, 0, warnings, err
		}
		warnings = append(warnings, cacheWarnings...)
		ids = ids[:0]
		for _, item := range items {
			if item.ID != "" {
				ids = append(ids, item.ID)
				if limit > 0 && len(ids) >= limit {
					break
				}
			}
		}
	default:
		return nil, 0, warnings, errors.New("unsupported Spotify source type")
	}

	tracks := make([]recommendation.Track, 0, len(ids))
	skipped := 0
	for _, id := range ids {
		track, trackWarnings, ok := s.buildTrack(ctx, id, market, enrichMB, allowMissing)
		warnings = append(warnings, trackWarnings...)
		if !ok {
			skipped++
			continue
		}
		tracks = append(tracks, track)
	}
	return tracks, skipped, warnings, nil
}

func (s *Service) buildTrack(ctx context.Context, id, market string, enrichMB, allowMissing bool) (recommendation.Track, []string, bool) {
	var warnings []string
	item, _, cacheWarnings, err := s.spotifyTrack(ctx, id, market)
	warnings = append(warnings, cacheWarnings...)
	if err != nil {
		warnings = append(warnings, fmt.Sprintf("spotify track %s skipped: %v", id, err))
		return recommendation.Track{}, warnings, false
	}
	features, _, cacheWarnings, err := s.spotifyAudioFeatures(ctx, id)
	warnings = append(warnings, cacheWarnings...)
	missingFeatures := false
	if err != nil {
		if !allowMissing {
			warnings = append(warnings, fmt.Sprintf("spotify track %s skipped: audio features unavailable", id))
			return recommendation.Track{}, warnings, false
		}
		missingFeatures = true
		warnings = append(warnings, fmt.Sprintf("spotify track %s imported without audio features", id))
	}
	var mb musicbrainz.Recording
	if enrichMB {
		recording, _, warn, ok := s.enrichSpotifyTrack(ctx, item)
		if warn != "" {
			warnings = append(warnings, warn)
		}
		if ok {
			mb = recording
		}
	}
	return mapSpotifyTrack(item, features, mb, missingFeatures), warnings, true
}

func (s *Service) upsertTrackEnrichments(ctx context.Context, tracks []recommendation.Track) error {
	for _, track := range tracks {
		if track.External["musicbrainz_recording_id"] == "" {
			continue
		}
		if err := s.store.UpsertTrackEnrichment(ctx, TrackEnrichment{
			TrackID:                track.ID,
			Provider:               ProviderMusicBrainz,
			MusicBrainzRecordingID: track.External["musicbrainz_recording_id"],
			MusicBrainzArtistID:    track.External["musicbrainz_artist_id"],
			ISRC:                   track.External["isrc"],
			UpdatedAt:              s.now(),
		}); err != nil {
			return err
		}
	}
	return nil
}

func (s *Service) enrichSpotifyTrack(ctx context.Context, track spotify.Track) (musicbrainz.Recording, []byte, string, bool) {
	if s.musicbrainz == nil || !s.musicbrainz.Configured() {
		return musicbrainz.Recording{}, nil, "", false
	}
	if isrc := strings.ToUpper(strings.TrimSpace(track.ExternalIDs["isrc"])); isrc != "" {
		mb, raw, warnings, err := s.musicBrainzISRC(ctx, isrc)
		if err == nil {
			return mb, raw, "", true
		}
		return musicbrainz.Recording{}, raw, appendWarning(warnings, "musicbrainz isrc lookup failed for "+isrc), false
	}
	artist := ""
	if len(track.Artists) > 0 {
		artist = track.Artists[0].Name
	}
	mb, raw, warnings, err := s.musicBrainzSearch(ctx, track.Name, artist)
	if err != nil {
		return musicbrainz.Recording{}, raw, appendWarning(warnings, "musicbrainz search failed for "+track.Name), false
	}
	return mb, raw, "", true
}

func (s *Service) enrichTrack(ctx context.Context, track recommendation.Track) (musicbrainz.Recording, []byte, string, bool) {
	if isrc := strings.TrimSpace(track.External["isrc"]); isrc != "" {
		mb, raw, warnings, err := s.musicBrainzISRC(ctx, isrc)
		if err == nil {
			return mb, raw, "", true
		}
		return musicbrainz.Recording{}, raw, appendWarning(warnings, "musicbrainz isrc lookup failed for "+isrc), false
	}
	mb, raw, warnings, err := s.musicBrainzSearch(ctx, track.Title, track.Artist)
	if err != nil {
		return musicbrainz.Recording{}, raw, appendWarning(warnings, "musicbrainz search failed for "+track.ID), false
	}
	return mb, raw, "", true
}

func (s *Service) spotifyTrack(ctx context.Context, id, market string) (spotify.Track, []byte, []string, error) {
	var out spotify.Track
	payload, warnings, err := s.cachedJSON(ctx, ProviderSpotify, "track", id, market, func(context.Context) ([]byte, error) {
		_, raw, err := s.spotify.GetTrack(ctx, id, market)
		return raw, err
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) spotifyAudioFeatures(ctx context.Context, id string) (spotify.AudioFeatures, []byte, []string, error) {
	var out spotify.AudioFeatures
	payload, warnings, err := s.cachedJSON(ctx, ProviderSpotify, "audio_features", id, "", func(context.Context) ([]byte, error) {
		_, raw, err := s.spotify.GetAudioFeatures(ctx, id)
		return raw, err
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) spotifySearch(ctx context.Context, query, itemType, market string, limit int) (spotify.SearchResult, []byte, []string, error) {
	var out spotify.SearchResult
	itemID := itemType + ":" + query + ":" + fmt.Sprint(limit)
	payload, warnings, err := s.cachedJSON(ctx, ProviderSpotify, "search", itemID, market, func(context.Context) ([]byte, error) {
		_, raw, err := s.spotify.Search(ctx, query, itemType, market, limit)
		return raw, err
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) spotifyAlbumTracks(ctx context.Context, id, market string, limit int) ([]spotify.TrackRef, []byte, []string, error) {
	var out []spotify.TrackRef
	payload, warnings, err := s.cachedJSON(ctx, ProviderSpotify, "album_tracks", id+":"+fmt.Sprint(limit), market, func(context.Context) ([]byte, error) {
		refs, _, err := s.spotify.GetAlbumTracks(ctx, id, market, limit)
		if err != nil {
			return nil, err
		}
		return json.Marshal(refs)
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) spotifyPlaylistTracks(ctx context.Context, id, market string, limit int) ([]spotify.TrackRef, []byte, []string, error) {
	var out []spotify.TrackRef
	payload, warnings, err := s.cachedJSON(ctx, ProviderSpotify, "playlist_tracks", id+":"+fmt.Sprint(limit), market, func(context.Context) ([]byte, error) {
		refs, _, err := s.spotify.GetPlaylistTracks(ctx, id, market, limit)
		if err != nil {
			return nil, err
		}
		return json.Marshal(refs)
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) spotifyArtistTopTracks(ctx context.Context, id, market string) ([]spotify.Track, []byte, []string, error) {
	var out []spotify.Track
	payload, warnings, err := s.cachedJSON(ctx, ProviderSpotify, "artist_top_tracks", id, market, func(context.Context) ([]byte, error) {
		tracks, _, err := s.spotify.GetArtistTopTracks(ctx, id, market)
		if err != nil {
			return nil, err
		}
		return json.Marshal(tracks)
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) musicBrainzISRC(ctx context.Context, isrc string) (musicbrainz.Recording, []byte, []string, error) {
	var out musicbrainz.Recording
	payload, warnings, err := s.cachedJSON(ctx, ProviderMusicBrainz, "isrc", isrc, "", func(context.Context) ([]byte, error) {
		recording, raw, err := s.musicbrainz.LookupByISRC(ctx, isrc)
		if err != nil {
			return raw, err
		}
		return json.Marshal(recording)
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) musicBrainzSearch(ctx context.Context, title, artist string) (musicbrainz.Recording, []byte, []string, error) {
	var out musicbrainz.Recording
	itemID := title + ":" + artist
	payload, warnings, err := s.cachedJSON(ctx, ProviderMusicBrainz, "recording_search", itemID, "", func(context.Context) ([]byte, error) {
		recording, raw, err := s.musicbrainz.SearchRecording(ctx, title, artist)
		if err != nil {
			return raw, err
		}
		return json.Marshal(recording)
	}, &out)
	return out, payload, warnings, err
}

func (s *Service) cachedJSON(ctx context.Context, providerName, itemType, itemID, market string, fetch func(context.Context) ([]byte, error), out any) ([]byte, []string, error) {
	var warnings []string
	now := s.now()
	cached, ok, err := s.store.GetProviderCache(ctx, providerName, itemType, itemID, market)
	if err != nil {
		return nil, warnings, err
	}
	if ok && cached.Fresh(now) {
		if err := json.Unmarshal(cached.Payload, out); err != nil {
			return cached.Payload, warnings, err
		}
		return cached.Payload, warnings, nil
	}
	payload, err := fetch(ctx)
	if err != nil {
		if ok && len(cached.Payload) > 0 {
			warnings = append(warnings, fmt.Sprintf("using stale %s %s cache after provider error", providerName, itemType))
			if decodeErr := json.Unmarshal(cached.Payload, out); decodeErr != nil {
				return cached.Payload, warnings, decodeErr
			}
			return cached.Payload, warnings, nil
		}
		_ = s.store.UpsertProviderCache(ctx, CacheEntry{
			Provider:  providerName,
			ItemType:  itemType,
			ItemID:    itemID,
			Market:    market,
			FetchedAt: now,
			ExpiresAt: now,
			LastError: err.Error(),
		})
		return payload, warnings, err
	}
	if err := json.Unmarshal(payload, out); err != nil {
		return payload, warnings, err
	}
	if err := s.store.UpsertProviderCache(ctx, CacheEntry{
		Provider:  providerName,
		ItemType:  itemType,
		ItemID:    itemID,
		Market:    market,
		Payload:   payload,
		FetchedAt: now,
		ExpiresAt: now.Add(s.cacheTTL),
	}); err != nil {
		return payload, warnings, err
	}
	return payload, warnings, nil
}

func (s *Service) market(value string) string {
	if value = strings.ToUpper(strings.TrimSpace(value)); value != "" {
		return value
	}
	return s.defaultMarket
}

func capSearchLimit(value int) int {
	if value <= 0 {
		return 5
	}
	if value > 10 {
		return 10
	}
	return value
}

func validSearchType(value string) bool {
	switch value {
	case "track", "album", "artist", "playlist":
		return true
	default:
		return false
	}
}

func capLimit(value, maxValue int) int {
	if value <= 0 {
		return maxValue
	}
	if value > maxValue {
		return maxValue
	}
	return value
}

func boolDefault(value *bool, fallback bool) bool {
	if value == nil {
		return fallback
	}
	return *value
}

func newID(prefix string) string {
	var b [12]byte
	if _, err := rand.Read(b[:]); err != nil {
		return prefix + "_" + strings.ReplaceAll(time.Now().UTC().Format(time.RFC3339Nano), ":", "")
	}
	return prefix + "_" + hex.EncodeToString(b[:])
}

func appendWarning(warnings []string, fallback string) string {
	if len(warnings) == 0 {
		return fallback
	}
	return warnings[0]
}

// importFromWebPlayer imports tracks using the native auth-free webplayer client
func (s *Service) importFromWebPlayer(ctx context.Context, req ImportRequest) (ImportResponse, error) {
	persist := true
	if req.Persist != nil {
		persist = *req.Persist
	}

	// Parse the URL to get the Spotify track ID
	itemType, itemID, err := webplayer.ParseSpotifyURL(req.Source.Value)
	if err != nil {
		parsedURL := s.urlparser.ParseURL(req.Source.Value)
		if parsedURL == nil || parsedURL.Service == urlparser.Spotify || s.songlink == nil || !s.songlink.Configured() {
			return ImportResponse{}, fmt.Errorf("invalid Spotify URL: %w", err)
		}
		links, linkErr := s.songlink.GetLinks(parsedURL.URL)
		if linkErr != nil {
			return ImportResponse{}, fmt.Errorf("could not resolve %s URL to Spotify: %w", parsedURL.Service, linkErr)
		}
		if strings.TrimSpace(links.SpotifyID) == "" {
			return ImportResponse{}, fmt.Errorf("could not resolve %s URL to a Spotify track", parsedURL.Service)
		}
		itemType = "track"
		itemID = strings.TrimSpace(links.SpotifyID)
	}

	if itemType != "track" {
		return ImportResponse{}, fmt.Errorf("unsupported item type: %s (only tracks supported for web player import)", itemType)
	}

	job := ImportJob{
		ID:          newID("import"),
		Provider:    ProviderSpotify,
		SourceType:  itemType,
		SourceValue: itemID,
		Status:      "running",
		StartedAt:   s.now(),
	}
	if persist {
		if err := s.store.CreateImportJob(ctx, job); err != nil {
			return ImportResponse{}, err
		}
	}

	// Fetch track from web player (auth-free using TOTP)
	wpTrack, err := s.webplayer.GetTrack(itemID)
	if err != nil {
		job.Status = "failed"
		job.Warnings = []string{err.Error()}
		job.FinishedAt = s.now()
		if persist {
			_ = s.store.FinishImportJob(ctx, job)
		}
		return ImportResponse{}, fmt.Errorf("web player fetch failed: %w", err)
	}

	// Convert artist list to string
	artistName := ""
	if len(wpTrack.Artists) > 0 {
		artistNames := make([]string, len(wpTrack.Artists))
		for i, a := range wpTrack.Artists {
			artistNames[i] = a.Name
		}
		artistName = strings.Join(artistNames, ", ")
	}

	// Build external URLs
	externalURLs := map[string]string{
		"spotify": fmt.Sprintf("https://open.spotify.com/track/%s", wpTrack.ID),
	}

	// Get cross-platform links from Song.link
	if s.songlink != nil && s.songlink.Configured() {
		if links, err := s.songlink.GetLinksFromSpotifyID(wpTrack.ID); err == nil && links != nil {
			for platform, link := range links.Links {
				externalURLs[platform] = link.URL
			}
		}
	}

	// Convert to recommendation.Track
	track := recommendation.Track{
		ID:         wpTrack.ID,
		Title:      wpTrack.Name,
		Artist:     artistName,
		Album:      wpTrack.Album.Name,
		DurationMS: wpTrack.DurationMs,
		Explicit:   wpTrack.Explicit,
		Popularity: 0.5, // Web player doesn't provide popularity
		External:   externalURLs,
		CreatedAt:  s.now(),
		UpdatedAt:  s.now(),
	}

	// Add image URL if available
	if len(wpTrack.Album.Images) > 0 {
		track.External["image_url"] = wpTrack.Album.Images[0].URL
	}

	// Optionally enrich with MusicBrainz
	if boolDefault(req.EnrichMusicBrainz, true) && s.musicbrainz != nil {
		mb, _, _, ok := s.enrichTrack(ctx, track)
		if ok && mb.ID != "" {
			track.External["musicbrainz_recording_id"] = mb.ID
			if mb.ISRC != "" {
				track.External["isrc"] = mb.ISRC
			}
		}
	}

	// Store the track
	imported, updated := 0, 0
	if persist {
		existing, _ := s.store.GetTracksByIDs(ctx, []string{track.ID})
		if len(existing) > 0 {
			updated = 1
		} else {
			imported = 1
		}
		if err := s.store.UpsertTrack(ctx, track); err != nil {
			return ImportResponse{}, err
		}
		if err := s.upsertTrackEnrichments(ctx, []recommendation.Track{track}); err != nil {
			return ImportResponse{}, err
		}
	}

	job.Status = "succeeded"
	job.ImportedTracks = imported
	job.UpdatedTracks = updated
	job.FinishedAt = s.now()
	if persist {
		if err := s.store.FinishImportJob(ctx, job); err != nil {
			return ImportResponse{}, err
		}
	}

	return ImportResponse{
		ImportID:       job.ID,
		ImportedTracks: imported,
		UpdatedTracks:  updated,
		Skipped:        0,
		Warnings:       []string{"imported via webplayer (auth-free, native Go)"},
	}, nil
}

// searchViaWebPlayer searches using the native webplayer client
func (s *Service) searchViaWebPlayer(ctx context.Context, req SearchRequest) (SearchResponse, error) {
	// Use the webplayer's search capability
	wpTracks, err := s.webplayer.Search(req.Query, req.Limit)
	if err != nil {
		return SearchResponse{}, err
	}

	var tracks []recommendation.Track
	for _, wpTrack := range wpTracks {
		artistName := ""
		if len(wpTrack.Artists) > 0 {
			artistNames := make([]string, len(wpTrack.Artists))
			for i, a := range wpTrack.Artists {
				artistNames[i] = a.Name
			}
			artistName = strings.Join(artistNames, ", ")
		}

		track := recommendation.Track{
			ID:         wpTrack.ID,
			Title:      wpTrack.Name,
			Artist:     artistName,
			Album:      wpTrack.Album.Name,
			DurationMS: wpTrack.DurationMs,
			Explicit:   wpTrack.Explicit,
			Popularity: 0.5,
			External: map[string]string{
				"spotify": fmt.Sprintf("https://open.spotify.com/track/%s", wpTrack.ID),
			},
		}

		tracks = append(tracks, track)
	}

	return SearchResponse{
		Tracks:    tracks,
		Persisted: 0,
		Skipped:   0,
		Warnings:  []string{"search results from webplayer (auth-free)"},
	}, nil
}
