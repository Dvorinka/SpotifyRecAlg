// Package urlparser provides universal music URL parsing for multiple streaming services.
package urlparser

import (
	neturl "net/url"
	"regexp"
	"strings"
)

// Service represents a music streaming service
type Service string

const (
	Spotify      Service = "spotify"
	Tidal        Service = "tidal"
	AppleMusic   Service = "apple_music"
	YouTube      Service = "youtube"
	YouTubeMusic Service = "youtube_music"
	SoundCloud   Service = "soundcloud"
	Deezer       Service = "deezer"
	Bandcamp     Service = "bandcamp"
	MusicBrainz  Service = "musicbrainz"
)

// ParsedURL represents a parsed music service URL
type ParsedURL struct {
	Service  Service
	URL      string
	ItemType string
	ID       string
	Metadata map[string]string
}

// Parser for music service URLs
type Parser struct {
	patterns map[Service][]*regexp.Regexp
	services []Service
}

// NewParser creates a new URL parser
func NewParser() *Parser {
	return &Parser{
		services: []Service{
			Spotify,
			Tidal,
			AppleMusic,
			YouTubeMusic,
			YouTube,
			SoundCloud,
			Deezer,
			Bandcamp,
			MusicBrainz,
		},
		patterns: map[Service][]*regexp.Regexp{
			Spotify: {
				regexp.MustCompile(`(?i)^spotify:(track|album|playlist|artist):([a-zA-Z0-9]+)$`),
				regexp.MustCompile(`(?i)https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?(?:embed/)?(track|album|playlist|artist)/([a-zA-Z0-9]+)`),
				regexp.MustCompile(`(?i)https://spotify\.link/([a-zA-Z0-9]+)`),
			},
			Tidal: {
				regexp.MustCompile(`(?i)https://tidal\.com/(?:browse/)?(track|album|playlist|artist)/(\d+)`),
				regexp.MustCompile(`(?i)https://listen\.tidal\.com/(?:browse/)?(track|album|playlist|artist)/(\d+)`),
			},
			AppleMusic: {
				regexp.MustCompile(`(?i)https://music\.apple\.com/([a-z]{2})/(song|album|playlist|artist)/(?:[^/]+/)?(\d+)`),
			},
			YouTubeMusic: {
				regexp.MustCompile(`(?i)https://music\.youtube\.com/(watch|playlist|channel)\?([^#]+)`),
			},
			YouTube: {
				regexp.MustCompile(`(?i)https://(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]+)`),
				regexp.MustCompile(`(?i)https://youtu\.be/([a-zA-Z0-9_-]+)`),
				regexp.MustCompile(`(?i)https://(?:www\.)?youtube\.com/playlist\?list=([a-zA-Z0-9_-]+)`),
			},
			SoundCloud: {
				regexp.MustCompile(`(?i)https://soundcloud\.com/([^/]+)/sets/([^/?#]+)`),
				regexp.MustCompile(`(?i)https://soundcloud\.com/([^/]+)/([^/]+)`),
			},
			Deezer: {
				regexp.MustCompile(`(?i)https://www\.deezer\.com/(?:[a-z]{2}/)?(track|album|playlist|artist)/(\d+)`),
			},
			Bandcamp: {
				regexp.MustCompile(`(?i)https://([a-zA-Z0-9-]+)\.bandcamp\.com/(track|album)/(.+)`),
			},
			MusicBrainz: {
				regexp.MustCompile(`(?i)https://musicbrainz\.org/(recording|release|release-group|artist)/([a-f0-9-]+)`),
			},
		},
	}
}

// ParseURL parses a music service URL and extracts service, type, and ID
func (p *Parser) ParseURL(url string) *ParsedURL {
	url = strings.TrimSpace(url)
	if url == "" {
		return nil
	}

	for _, service := range p.services {
		patterns := p.patterns[service]
		for _, pattern := range patterns {
			matches := pattern.FindStringSubmatch(url)
			if matches != nil {
				return p.extractServiceInfo(service, matches, url)
			}
		}
	}

	return nil
}

func (p *Parser) extractServiceInfo(service Service, matches []string, url string) *ParsedURL {
	switch service {
	case Spotify:
		if len(matches) >= 3 {
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: matches[1],
				ID:       matches[2],
			}
		}
		if len(matches) == 2 {
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: "short",
				ID:       matches[1],
			}
		}

	case Tidal:
		if len(matches) >= 3 {
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: matches[1],
				ID:       matches[2],
			}
		}

	case AppleMusic:
		if len(matches) >= 4 {
			itemType := matches[2]
			id := matches[3]
			if parsed, err := neturl.Parse(url); err == nil && itemType == "album" {
				if trackID := parsed.Query().Get("i"); trackID != "" {
					itemType = "song"
					id = trackID
				}
			}
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: itemType,
				ID:       id,
				Metadata: map[string]string{
					"region": matches[1],
				},
			}
		}

	case YouTube, YouTubeMusic:
		if parsed, err := neturl.Parse(url); err == nil {
			if v := parsed.Query().Get("v"); v != "" {
				return &ParsedURL{Service: service, URL: url, ItemType: "video", ID: v}
			}
			if list := parsed.Query().Get("list"); list != "" {
				return &ParsedURL{Service: service, URL: url, ItemType: "playlist", ID: list}
			}
		}
		return &ParsedURL{
			Service:  service,
			URL:      url,
			ItemType: "video",
			ID:       matches[1],
		}

	case SoundCloud:
		if len(matches) >= 3 {
			itemType := "track"
			if strings.EqualFold(matches[1], "sets") || strings.Contains(strings.ToLower(url), "/sets/") {
				itemType = "playlist"
			}
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: itemType,
				ID:       matches[1] + "/" + matches[2],
			}
		}

	case Deezer:
		if len(matches) >= 3 {
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: matches[1],
				ID:       matches[2],
			}
		}

	case Bandcamp:
		if len(matches) >= 4 {
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: matches[2],
				ID:       matches[1] + "/" + matches[3],
			}
		}

	case MusicBrainz:
		if len(matches) >= 3 {
			return &ParsedURL{
				Service:  service,
				URL:      url,
				ItemType: matches[1],
				ID:       matches[2],
			}
		}
	}

	return nil
}

// GetServiceFromURL quickly identifies the service from a URL without full parsing
func (p *Parser) GetServiceFromURL(url string) Service {
	urlLower := strings.ToLower(url)

	if strings.Contains(urlLower, "spotify.com") || strings.Contains(urlLower, "spotify.link") {
		return Spotify
	}
	if strings.Contains(urlLower, "tidal.com") || strings.Contains(urlLower, "listen.tidal.com") {
		return Tidal
	}
	if strings.Contains(urlLower, "music.apple.com") {
		return AppleMusic
	}
	if strings.Contains(urlLower, "music.youtube.com") {
		return YouTubeMusic
	}
	if strings.Contains(urlLower, "youtube.com") || strings.Contains(urlLower, "youtu.be") {
		return YouTube
	}
	if strings.Contains(urlLower, "soundcloud.com") {
		return SoundCloud
	}
	if strings.Contains(urlLower, "deezer.com") {
		return Deezer
	}
	if strings.Contains(urlLower, "bandcamp.com") {
		return Bandcamp
	}
	if strings.Contains(urlLower, "musicbrainz.org") {
		return MusicBrainz
	}

	return ""
}

// ValidateURL checks if a URL is from a supported service
func (p *Parser) ValidateURL(url string) bool {
	return p.ParseURL(url) != nil
}
