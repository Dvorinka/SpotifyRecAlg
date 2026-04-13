package spotify

type Track struct {
	ID           string            `json:"id"`
	Name         string            `json:"name"`
	Artists      []Artist          `json:"artists"`
	Album        Album             `json:"album"`
	DurationMS   int               `json:"duration_ms"`
	Popularity   int               `json:"popularity"`
	Explicit     bool              `json:"explicit"`
	ExternalIDs  map[string]string `json:"external_ids"`
	ExternalURLs map[string]string `json:"external_urls"`
	Type         string            `json:"type"`
	IsLocal      bool              `json:"is_local"`
}

type TrackRef struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	IsLocal bool   `json:"is_local"`
}

type Artist struct {
	ID           string            `json:"id"`
	Name         string            `json:"name"`
	Genres       []string          `json:"genres"`
	ExternalURLs map[string]string `json:"external_urls"`
}

type Album struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	ReleaseDate string   `json:"release_date"`
	Images      []Image  `json:"images"`
	Artists     []Artist `json:"artists"`
}

type Image struct {
	URL    string `json:"url"`
	Height int    `json:"height"`
	Width  int    `json:"width"`
}

type AudioFeatures struct {
	Danceability     float64 `json:"danceability"`
	Energy           float64 `json:"energy"`
	Loudness         float64 `json:"loudness"`
	Speechiness      float64 `json:"speechiness"`
	Acousticness     float64 `json:"acousticness"`
	Instrumentalness float64 `json:"instrumentalness"`
	Liveness         float64 `json:"liveness"`
	Valence          float64 `json:"valence"`
	Tempo            float64 `json:"tempo"`
	TimeSignature    float64 `json:"time_signature"`
	Key              float64 `json:"key"`
	Mode             float64 `json:"mode"`
}

type SearchResult struct {
	Tracks struct {
		Items []Track `json:"items"`
	} `json:"tracks"`
	Albums struct {
		Items []Album `json:"items"`
	} `json:"albums"`
	Artists struct {
		Items []Artist `json:"items"`
	} `json:"artists"`
	Playlists struct {
		Items []struct {
			ID   string `json:"id"`
			Name string `json:"name"`
		} `json:"items"`
	} `json:"playlists"`
}
