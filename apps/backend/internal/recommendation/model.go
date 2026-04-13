package recommendation

import "time"

type Track struct {
	ID               string            `json:"id"`
	Title            string            `json:"title"`
	Artist           string            `json:"artist"`
	Album            string            `json:"album,omitempty"`
	Genres           []string          `json:"genres,omitempty"`
	ReleaseDate      string            `json:"release_date,omitempty"`
	DurationMS       int               `json:"duration_ms,omitempty"`
	Popularity       float64           `json:"popularity"`
	Explicit         bool              `json:"explicit"`
	Features         AudioFeatures     `json:"features"`
	External         map[string]string `json:"external,omitempty"`
	CreatedAt        time.Time         `json:"created_at"`
	UpdatedAt        time.Time         `json:"updated_at"`
	CommercialBoost  float64           `json:"commercial_boost,omitempty"`
	QualityPenalty   float64           `json:"quality_penalty,omitempty"`
	DiscoveryAllowed bool              `json:"discovery_allowed"`
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

type InteractionType string

const (
	InteractionPlay    InteractionType = "play"
	InteractionSkip    InteractionType = "skip"
	InteractionLike    InteractionType = "like"
	InteractionDislike InteractionType = "dislike"
	InteractionSave    InteractionType = "save"
	InteractionHide    InteractionType = "hide"
)

type Interaction struct {
	UserID      string          `json:"user_id"`
	TrackID     string          `json:"track_id"`
	Type        InteractionType `json:"type"`
	Weight      float64         `json:"weight,omitempty"`
	OccurredAt  time.Time       `json:"occurred_at"`
	Context     Context         `json:"context,omitempty"`
	CompletedMS int             `json:"completed_ms,omitempty"`
}

type Context struct {
	Locale    string `json:"locale,omitempty"`
	Device    string `json:"device,omitempty"`
	TimeOfDay string `json:"time_of_day,omitempty"`
	Activity  string `json:"activity,omitempty"`
	Mood      string `json:"mood,omitempty"`
}

type UserControls struct {
	UserID          string   `json:"user_id"`
	AllowExplicit   bool     `json:"allow_explicit"`
	ExcludedTracks  []string `json:"excluded_tracks,omitempty"`
	ExcludedArtists []string `json:"excluded_artists,omitempty"`
	ExcludedGenres  []string `json:"excluded_genres,omitempty"`
	PostponedTracks []string `json:"postponed_tracks,omitempty"`
}

type RecommendRequest struct {
	UserID            string         `json:"user_id"`
	Limit             int            `json:"limit"`
	SeedTrackIDs      []string       `json:"seed_track_ids,omitempty"`
	FeatureTargets    *AudioFeatures `json:"feature_targets,omitempty"`
	Context           Context        `json:"context,omitempty"`
	Mode              string         `json:"mode,omitempty"`
	ExplorationTarget float64        `json:"exploration_target,omitempty"`
	MinPopularity     *float64       `json:"min_popularity,omitempty"`
	MaxPopularity     *float64       `json:"max_popularity,omitempty"`
	IncludeExplicit   *bool          `json:"include_explicit,omitempty"`
	ExcludedTrackIDs  []string       `json:"excluded_track_ids,omitempty"`
	ExcludedArtistIDs []string       `json:"excluded_artist_ids,omitempty"`
	ExcludedGenres    []string       `json:"excluded_genres,omitempty"`
}

type Recommendation struct {
	Track          Track              `json:"track"`
	Score          float64            `json:"score"`
	Rank           int                `json:"rank"`
	Reason         string             `json:"reason"`
	ScoreBreakdown ScoreBreakdown     `json:"score_breakdown"`
	Explanation    map[string]float64 `json:"explanation"`
}

type ScoreBreakdown struct {
	Content       float64 `json:"content"`
	Collaborative float64 `json:"collaborative"`
	Popularity    float64 `json:"popularity"`
	Exploration   float64 `json:"exploration"`
	Diversity     float64 `json:"diversity"`
	Safety        float64 `json:"safety"`
	Commercial    float64 `json:"commercial"`
	Final         float64 `json:"final"`
}

type TasteProfile struct {
	UserID               string             `json:"user_id"`
	Vector               []float64          `json:"vector"`
	TopGenres            map[string]float64 `json:"top_genres"`
	InteractionCount     int                `json:"interaction_count"`
	Confidence           float64            `json:"confidence"`
	ExplorationReadiness float64            `json:"exploration_readiness"`
	UpdatedAt            time.Time          `json:"updated_at"`
}

type CatalogSnapshot struct {
	Tracks       []Track
	Interactions []Interaction
	Controls     UserControls
}
