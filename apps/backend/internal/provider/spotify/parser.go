package spotify

import (
	"errors"
	"net/url"
	"regexp"
	"strings"
)

var (
	uriPattern    = regexp.MustCompile(`(?i)^spotify:(track|album|playlist|artist):([A-Za-z0-9]+)$`)
	idPattern     = regexp.MustCompile(`^[A-Za-z0-9]{10,}$`)
	pathIDPattern = regexp.MustCompile(`^[A-Za-z0-9]+$`)
)

type ParsedSource struct {
	Type string
	ID   string
	URL  string
}

func ParseSource(sourceType, value string) (ParsedSource, error) {
	sourceType = strings.ToLower(strings.TrimSpace(sourceType))
	value = strings.TrimSpace(value)
	if value == "" {
		return ParsedSource{}, errors.New("source value is required")
	}

	if sourceType == "" || sourceType == "url" {
		parsed, err := ParseURL(value)
		if err == nil {
			return parsed, nil
		}
		if sourceType == "url" {
			return ParsedSource{}, err
		}
	}
	if sourceType == "" {
		sourceType = "track"
	}
	if !validSpotifyType(sourceType) {
		return ParsedSource{}, errors.New("source type must be track, album, playlist, artist, or url")
	}
	if parsed, err := ParseURL(value); err == nil {
		if parsed.Type != sourceType {
			return ParsedSource{}, errors.New("source URL type does not match requested type")
		}
		return parsed, nil
	}
	if !idPattern.MatchString(value) {
		return ParsedSource{}, errors.New("source value must be a Spotify ID, URI, or open.spotify.com URL")
	}
	return ParsedSource{Type: sourceType, ID: value, URL: "https://open.spotify.com/" + sourceType + "/" + value}, nil
}

func ParseURL(raw string) (ParsedSource, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ParsedSource{}, errors.New("url is required")
	}

	if match := uriPattern.FindStringSubmatch(raw); len(match) == 3 {
		return ParsedSource{Type: strings.ToLower(match[1]), ID: match[2], URL: "https://open.spotify.com/" + strings.ToLower(match[1]) + "/" + match[2]}, nil
	}

	parsedURL, err := parseURLWithDefaultScheme(raw)
	if err == nil {
		if value := parsedURL.Query().Get("uri"); value != "" {
			return ParseURL(value)
		}

		host := spotifyHost(parsedURL.Host)
		switch host {
		case "open.spotify.com", "play.spotify.com":
			if parsed, ok := parseSpotifyPath(parsedURL.Path); ok {
				parsed.URL = canonicalURL(parsed.Type, parsed.ID)
				return parsed, nil
			}
		case "embed.spotify.com":
			if parsed, ok := parseSpotifyPath(parsedURL.Path); ok {
				parsed.URL = canonicalURL(parsed.Type, parsed.ID)
				return parsed, nil
			}
		}
	}

	return ParsedSource{}, errors.New("unsupported Spotify URL")
}

func parseURLWithDefaultScheme(raw string) (*url.URL, error) {
	if strings.Contains(raw, "://") {
		return url.Parse(raw)
	}
	lower := strings.ToLower(raw)
	if strings.HasPrefix(lower, "open.spotify.com/") ||
		strings.HasPrefix(lower, "play.spotify.com/") ||
		strings.HasPrefix(lower, "embed.spotify.com/") {
		return url.Parse("https://" + raw)
	}
	return url.Parse(raw)
}

func spotifyHost(host string) string {
	host = strings.ToLower(strings.TrimSpace(host))
	host = strings.TrimPrefix(host, "www.")
	return host
}

func parseSpotifyPath(path string) (ParsedSource, bool) {
	parts := pathSegments(path)
	if len(parts) == 0 {
		return ParsedSource{}, false
	}
	if strings.HasPrefix(strings.ToLower(parts[0]), "intl-") {
		parts = parts[1:]
	}
	if len(parts) > 0 && strings.EqualFold(parts[0], "embed") {
		parts = parts[1:]
	}
	if len(parts) >= 4 && strings.EqualFold(parts[0], "user") && strings.EqualFold(parts[2], "playlist") && pathIDPattern.MatchString(parts[3]) {
		return ParsedSource{Type: "playlist", ID: parts[3]}, true
	}
	itemType := strings.ToLower(parts[0])
	if len(parts) >= 2 && validSpotifyType(itemType) && pathIDPattern.MatchString(parts[1]) {
		return ParsedSource{Type: itemType, ID: parts[1]}, true
	}
	return ParsedSource{}, false
}

func pathSegments(path string) []string {
	rawParts := strings.Split(path, "/")
	parts := make([]string, 0, len(rawParts))
	for _, part := range rawParts {
		part = strings.TrimSpace(part)
		if part != "" {
			parts = append(parts, part)
		}
	}
	return parts
}

func canonicalURL(itemType, id string) string {
	return "https://open.spotify.com/" + itemType + "/" + id
}

func validSpotifyType(value string) bool {
	switch value {
	case "track", "album", "playlist", "artist":
		return true
	default:
		return false
	}
}
