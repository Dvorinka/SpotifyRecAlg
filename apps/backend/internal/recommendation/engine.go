package recommendation

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"math"
	"slices"
	"strings"
	"time"
)

type SnapshotProvider interface {
	Snapshot(ctx context.Context, userID string) (CatalogSnapshot, error)
}

type Engine struct {
	now               func() time.Time
	contentWeight     float64
	collabWeight      float64
	popularityWeight  float64
	explorationWeight float64
	diversityLambda   float64
}

type EngineConfig struct {
	Now               func() time.Time
	ContentWeight     float64
	CollabWeight      float64
	PopularityWeight  float64
	ExplorationWeight float64
	DiversityLambda   float64
}

func NewEngine(cfg EngineConfig) *Engine {
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	return &Engine{
		now:               cfg.Now,
		contentWeight:     cfg.ContentWeight,
		collabWeight:      cfg.CollabWeight,
		popularityWeight:  cfg.PopularityWeight,
		explorationWeight: cfg.ExplorationWeight,
		diversityLambda:   cfg.DiversityLambda,
	}
}

func (e *Engine) Recommend(ctx context.Context, provider SnapshotProvider, req RecommendRequest) ([]Recommendation, TasteProfile, error) {
	if strings.TrimSpace(req.UserID) == "" {
		return nil, TasteProfile{}, errors.New("user_id is required")
	}
	if req.Limit <= 0 {
		req.Limit = 20
	}
	if req.Limit > 100 {
		req.Limit = 100
	}

	snapshot, err := provider.Snapshot(ctx, req.UserID)
	if err != nil {
		return nil, TasteProfile{}, err
	}
	if len(snapshot.Tracks) == 0 {
		return nil, TasteProfile{}, errors.New("catalog is empty")
	}

	b := bounds(snapshot.Tracks)
	byTrackID := indexTracks(snapshot.Tracks)
	userInteractions := interactionsForUser(snapshot.Interactions, req.UserID)
	tasteVector, positiveIDs, confidence, hasTasteAudio := e.tasteVector(snapshot.Tracks, byTrackID, userInteractions, req, b)
	preferences := e.preferenceProfile(byTrackID, userInteractions, req)
	neighborScores := e.collaborativeScores(snapshot.Interactions, req.UserID)
	controls := mergeControls(snapshot.Controls, req)
	candidates := make([]Recommendation, 0, len(snapshot.Tracks))

	for _, track := range snapshot.Tracks {
		if shouldFilter(track, positiveIDs, controls, req) {
			continue
		}

		trackVector := normalize(track.Features, b)
		trackHasAudio := hasAudioFeatures(track.Features)
		metadataScore := metadataAffinity(track, preferences)
		contentScore := metadataScore
		if hasTasteAudio && trackHasAudio {
			contentScore = clamp01(cosine(tasteVector, trackVector)*0.78 + metadataScore*0.22)
		}

		collabScore := clamp01(neighborScores[track.ID])
		popularityScore := popularityFit(track.Popularity, req.Mode)
		explorationScore := 0.5
		if hasTasteAudio && trackHasAudio {
			explorationScore = e.explorationScore(track, trackVector, tasteVector, req)
		}
		safetyScore := safetyScore(track, controls) * (1 - 0.52*negativeAffinity(track, preferences))
		commercialScore := commercialScore(track, contentScore)

		final := 0.0
		final += e.contentWeight * contentScore
		final += e.collabWeight * collabScore
		final += e.popularityWeight * popularityScore
		final += e.explorationWeight * explorationScore
		final *= safetyScore
		final += commercialScore

		candidates = append(candidates, Recommendation{
			Track:  track,
			Score:  final,
			Reason: reason(contentScore, collabScore, explorationScore, metadataScore, hasTasteAudio && trackHasAudio),
			ScoreBreakdown: ScoreBreakdown{
				Content:       round(contentScore),
				Collaborative: round(collabScore),
				Popularity:    round(popularityScore),
				Exploration:   round(explorationScore),
				Safety:        round(safetyScore),
				Commercial:    round(commercialScore),
				Final:         round(final),
			},
			Explanation: featureExplanation(tasteVector, trackVector),
		})
	}

	slices.SortFunc(candidates, func(a, b Recommendation) int {
		return cmp.Compare(b.Score, a.Score)
	})

	selected := e.diversify(candidates, req.Limit, b)
	for i := range selected {
		selected[i].Rank = i + 1
		selected[i].Score = round(selected[i].Score)
		selected[i].ScoreBreakdown.Final = selected[i].Score
	}

	profile := TasteProfile{
		UserID:               req.UserID,
		Vector:               arrayToSlice(tasteVector),
		TopGenres:            topGenres(snapshot.Tracks, byTrackID, userInteractions),
		InteractionCount:     len(userInteractions),
		Confidence:           round(confidence),
		ExplorationReadiness: round(explorationReadiness(confidence, userInteractions)),
		UpdatedAt:            e.now().UTC(),
	}
	return selected, profile, nil
}

func (e *Engine) TasteProfile(ctx context.Context, provider SnapshotProvider, userID string) (TasteProfile, error) {
	recs, profile, err := e.Recommend(ctx, provider, RecommendRequest{UserID: userID, Limit: 1})
	if err != nil {
		return TasteProfile{}, err
	}
	_ = recs
	return profile, nil
}

func (e *Engine) tasteVector(tracks []Track, byTrackID map[string]Track, interactions []Interaction, req RecommendRequest, b featureBounds) ([featureCount]float64, map[string]struct{}, float64, bool) {
	var sum [featureCount]float64
	var total float64
	var audioTotal float64
	positive := make(map[string]struct{})

	for _, seedID := range req.SeedTrackIDs {
		track, ok := byTrackID[seedID]
		if !ok {
			continue
		}
		positive[seedID] = struct{}{}
		if !hasAudioFeatures(track.Features) {
			continue
		}
		addWeighted(&sum, normalize(track.Features, b), 1.25)
		total += 1.25
		audioTotal += 1.25
	}

	for _, interaction := range interactions {
		track, ok := byTrackID[interaction.TrackID]
		if !ok {
			continue
		}
		weight := interactionWeight(interaction)
		if weight > 0 {
			positive[interaction.TrackID] = struct{}{}
		}
		if !hasAudioFeatures(track.Features) {
			continue
		}
		decay := timeDecay(e.now(), interaction.OccurredAt)
		addWeighted(&sum, normalize(track.Features, b), weight*decay)
		total += math.Abs(weight * decay)
		audioTotal += math.Abs(weight * decay)
	}

	if req.FeatureTargets != nil {
		addWeighted(&sum, normalize(*req.FeatureTargets, b), 1.15)
		total += 1.15
		audioTotal += 1.15
	}

	if total == 0 {
		return catalogCentroid(tracks, b), positive, 0, false
	}
	for i := range featureCount {
		sum[i] = clamp01(sum[i] / total)
	}
	confidence := clamp01(math.Log1p(total) / math.Log(32))
	return sum, positive, confidence, audioTotal > 0
}

func (e *Engine) collaborativeScores(interactions []Interaction, activeUserID string) map[string]float64 {
	userRatings := make(map[string]map[string]float64)
	for _, interaction := range interactions {
		if userRatings[interaction.UserID] == nil {
			userRatings[interaction.UserID] = make(map[string]float64)
		}
		userRatings[interaction.UserID][interaction.TrackID] += interactionWeight(interaction)
	}

	active := userRatings[activeUserID]
	scores := make(map[string]float64)
	if len(active) == 0 {
		return scores
	}

	for userID, ratings := range userRatings {
		if userID == activeUserID {
			continue
		}
		similarity, overlap := pearson(active, ratings)
		if similarity <= 0 {
			continue
		}
		similarity *= float64(overlap) / float64(overlap+3)
		for trackID, rating := range ratings {
			if _, alreadyKnown := active[trackID]; alreadyKnown || rating <= 0 {
				continue
			}
			scores[trackID] += similarity * rating
		}
	}

	maxScore := 0.0
	for _, score := range scores {
		if score > maxScore {
			maxScore = score
		}
	}
	if maxScore == 0 {
		return scores
	}
	for trackID, score := range scores {
		scores[trackID] = clamp01(score / maxScore)
	}
	return scores
}

type preferenceProfile struct {
	artists         map[string]float64
	genres          map[string]float64
	negativeArtists map[string]float64
	negativeGenres  map[string]float64
}

func (e *Engine) preferenceProfile(byTrackID map[string]Track, interactions []Interaction, req RecommendRequest) preferenceProfile {
	profile := preferenceProfile{
		artists:         make(map[string]float64),
		genres:          make(map[string]float64),
		negativeArtists: make(map[string]float64),
		negativeGenres:  make(map[string]float64),
	}

	for _, seedID := range req.SeedTrackIDs {
		if track, ok := byTrackID[seedID]; ok {
			addTrackPreference(profile.artists, profile.genres, track, 1.25)
		}
	}

	for _, interaction := range interactions {
		track, ok := byTrackID[interaction.TrackID]
		if !ok {
			continue
		}
		weight := interactionWeight(interaction) * timeDecay(e.now(), interaction.OccurredAt)
		switch {
		case weight > 0:
			addTrackPreference(profile.artists, profile.genres, track, weight)
		case weight < 0:
			addTrackPreference(profile.negativeArtists, profile.negativeGenres, track, math.Abs(weight))
		}
	}

	normalizeMap(profile.artists)
	normalizeMap(profile.genres)
	normalizeMap(profile.negativeArtists)
	normalizeMap(profile.negativeGenres)
	return profile
}

func addTrackPreference(artists, genres map[string]float64, track Track, weight float64) {
	if artist := normalizedToken(track.Artist); artist != "" {
		artists[artist] += weight
	}
	for _, genre := range track.Genres {
		if genre = normalizedToken(genre); genre != "" {
			genres[genre] += weight
		}
	}
}

func normalizeMap(values map[string]float64) {
	maxValue := 0.0
	for _, value := range values {
		maxValue = math.Max(maxValue, value)
	}
	if maxValue == 0 {
		return
	}
	for key, value := range values {
		values[key] = clamp01(value / maxValue)
	}
}

func metadataAffinity(track Track, profile preferenceProfile) float64 {
	artistScore := profile.artists[normalizedToken(track.Artist)]
	genreScore := genreAffinity(track.Genres, profile.genres)

	switch {
	case artistScore == 0 && genreScore == 0:
		return 0.42
	case artistScore == 0:
		return clamp01(0.32 + 0.68*genreScore)
	case genreScore == 0:
		return clamp01(0.38 + 0.62*artistScore)
	default:
		return clamp01(0.48*artistScore + 0.52*genreScore)
	}
}

func negativeAffinity(track Track, profile preferenceProfile) float64 {
	artistScore := profile.negativeArtists[normalizedToken(track.Artist)]
	genreScore := genreAffinity(track.Genres, profile.negativeGenres)
	return clamp01(math.Max(artistScore*0.9, genreScore*0.7))
}

func genreAffinity(genres []string, profile map[string]float64) float64 {
	best := 0.0
	for _, genre := range genres {
		best = math.Max(best, profile[normalizedToken(genre)])
	}
	return best
}

func normalizedToken(value string) string {
	return strings.ToLower(strings.TrimSpace(value))
}

func popularityFit(popularity float64, mode string) float64 {
	popularity = clamp01(popularity)
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "comfort":
		return clamp01(0.35 + 0.65*popularity)
	case "discovery":
		return clamp01(1 - math.Abs(popularity-0.52)*1.25)
	default:
		familiarity := popularity
		midTail := clamp01(1 - math.Abs(popularity-0.62)*1.15)
		return clamp01(0.55*familiarity + 0.45*midTail)
	}
}

func (e *Engine) explorationScore(track Track, trackVector, tasteVector [featureCount]float64, req RecommendRequest) float64 {
	target := req.ExplorationTarget
	if target == 0 {
		target = 0.22
	}
	if strings.EqualFold(req.Mode, "discovery") {
		target = math.Max(target, 0.34)
	}
	if strings.EqualFold(req.Mode, "comfort") {
		target = math.Min(target, 0.10)
	}

	d := distance(trackVector, tasteVector)
	return clamp01(1 - math.Abs(d-target))
}

func (e *Engine) diversify(candidates []Recommendation, limit int, b featureBounds) []Recommendation {
	if len(candidates) <= limit {
		return candidates
	}

	selected := make([]Recommendation, 0, limit)
	remaining := slices.Clone(candidates)
	for len(selected) < limit && len(remaining) > 0 {
		bestIndex := 0
		bestScore := math.Inf(-1)
		for i, candidate := range remaining {
			diversity := minDistanceToSelected(candidate.Track, selected, b)
			score := e.diversityLambda*candidate.Score + (1-e.diversityLambda)*diversity
			if score > bestScore {
				bestScore = score
				bestIndex = i
			}
		}
		chosen := remaining[bestIndex]
		chosen.ScoreBreakdown.Diversity = round(minDistanceToSelected(chosen.Track, selected, b))
		selected = append(selected, chosen)
		remaining = append(remaining[:bestIndex], remaining[bestIndex+1:]...)
	}
	return selected
}

func indexTracks(tracks []Track) map[string]Track {
	out := make(map[string]Track, len(tracks))
	for _, track := range tracks {
		out[track.ID] = track
	}
	return out
}

func interactionsForUser(interactions []Interaction, userID string) []Interaction {
	out := make([]Interaction, 0)
	for _, interaction := range interactions {
		if interaction.UserID == userID {
			out = append(out, interaction)
		}
	}
	return out
}

func interactionWeight(interaction Interaction) float64 {
	if interaction.Weight != 0 {
		return interaction.Weight
	}
	switch interaction.Type {
	case InteractionLike:
		return 1
	case InteractionSave:
		return 0.9
	case InteractionPlay:
		if interaction.CompletedMS > 30_000 {
			return 0.45
		}
		return 0.20
	case InteractionSkip:
		return -0.55
	case InteractionDislike:
		return -1
	case InteractionHide:
		return -1.25
	default:
		return 0
	}
}

func timeDecay(now, occurredAt time.Time) float64 {
	if occurredAt.IsZero() {
		return 0.7
	}
	days := now.Sub(occurredAt).Hours() / 24
	if days <= 0 {
		return 1
	}
	return math.Exp(-days / 120)
}

func addWeighted(sum *[featureCount]float64, value [featureCount]float64, weight float64) {
	for i := range featureCount {
		sum[i] += value[i] * weight
	}
}

func catalogCentroid(tracks []Track, b featureBounds) [featureCount]float64 {
	var sum [featureCount]float64
	count := 0
	for _, track := range tracks {
		if !hasAudioFeatures(track.Features) {
			continue
		}
		addWeighted(&sum, normalize(track.Features, b), 1)
		count++
	}
	if count == 0 {
		return sum
	}
	for i := range featureCount {
		sum[i] /= float64(count)
	}
	return sum
}

func pearson(a, b map[string]float64) (float64, int) {
	common := make([]string, 0)
	for trackID := range a {
		if _, ok := b[trackID]; ok {
			common = append(common, trackID)
		}
	}
	if len(common) < 2 {
		return 0, len(common)
	}

	var meanA, meanB float64
	for _, trackID := range common {
		meanA += a[trackID]
		meanB += b[trackID]
	}
	meanA /= float64(len(common))
	meanB /= float64(len(common))

	var numerator, denomA, denomB float64
	for _, trackID := range common {
		da := a[trackID] - meanA
		db := b[trackID] - meanB
		numerator += da * db
		denomA += da * da
		denomB += db * db
	}
	if denomA == 0 || denomB == 0 {
		return 0, len(common)
	}
	return numerator / (math.Sqrt(denomA) * math.Sqrt(denomB)), len(common)
}

func shouldFilter(track Track, positive map[string]struct{}, controls UserControls, req RecommendRequest) bool {
	if _, known := positive[track.ID]; known {
		return true
	}
	if req.MinPopularity != nil && track.Popularity < *req.MinPopularity {
		return true
	}
	if req.MaxPopularity != nil && track.Popularity > *req.MaxPopularity {
		return true
	}
	includeExplicit := controls.AllowExplicit
	if req.IncludeExplicit != nil {
		includeExplicit = *req.IncludeExplicit
	}
	if track.Explicit && !includeExplicit {
		return true
	}
	if contains(controls.ExcludedTracks, track.ID) || contains(controls.PostponedTracks, track.ID) {
		return true
	}
	if contains(controls.ExcludedArtists, track.Artist) {
		return true
	}
	for _, genre := range track.Genres {
		if containsFold(controls.ExcludedGenres, genre) {
			return true
		}
	}
	return false
}

func mergeControls(controls UserControls, req RecommendRequest) UserControls {
	if controls.UserID == "" {
		controls.UserID = req.UserID
		controls.AllowExplicit = true
	}
	controls.ExcludedTracks = append(controls.ExcludedTracks, req.ExcludedTrackIDs...)
	controls.ExcludedArtists = append(controls.ExcludedArtists, req.ExcludedArtistIDs...)
	controls.ExcludedGenres = append(controls.ExcludedGenres, req.ExcludedGenres...)
	return controls
}

func safetyScore(track Track, controls UserControls) float64 {
	if track.QualityPenalty > 0 {
		return clamp01(1 - track.QualityPenalty)
	}
	if !controls.AllowExplicit && track.Explicit {
		return 0
	}
	return 1
}

func commercialScore(track Track, contentScore float64) float64 {
	if !track.DiscoveryAllowed || track.CommercialBoost <= 0 || contentScore < 0.72 {
		return 0
	}
	return math.Min(track.CommercialBoost, 0.035)
}

func reason(contentScore, collabScore, explorationScore, metadataScore float64, hasAudioFeatures bool) string {
	if !hasAudioFeatures && metadataScore >= 0.65 {
		return "matched by genre, artist, and catalog signals while audio features were limited"
	}
	if !hasAudioFeatures {
		return "balanced catalog match while audio features were limited"
	}
	switch {
	case collabScore >= 0.65:
		return "listeners with overlapping taste responded strongly to this track"
	case explorationScore >= 0.82 && contentScore >= 0.58:
		return "close enough to your taste profile while adding useful variety"
	case contentScore >= 0.78:
		return "audio features closely match your current taste profile"
	default:
		return "balanced recommendation from catalog, taste, and diversity signals"
	}
}

func featureExplanation(taste, track [featureCount]float64) map[string]float64 {
	out := make(map[string]float64, featureCount)
	for i, name := range featureNames {
		out[name] = round(1 - math.Abs(taste[i]-track[i]))
	}
	return out
}

func minDistanceToSelected(track Track, selected []Recommendation, b featureBounds) float64 {
	if len(selected) == 0 {
		return 1
	}
	minDistance := math.Inf(1)
	for _, other := range selected {
		d := trackDistance(track, other.Track, b)
		if d < minDistance {
			minDistance = d
		}
	}
	return clamp01(minDistance)
}

func trackDistance(a, b Track, bounds featureBounds) float64 {
	if hasAudioFeatures(a.Features) && hasAudioFeatures(b.Features) {
		return distance(normalize(a.Features, bounds), normalize(b.Features, bounds))
	}
	if strings.EqualFold(a.Artist, b.Artist) && a.Artist != "" {
		return 0.12
	}
	if genreOverlap(a.Genres, b.Genres) {
		return 0.38
	}
	return 0.78
}

func genreOverlap(a, b []string) bool {
	seen := make(map[string]struct{}, len(a))
	for _, genre := range a {
		if genre = normalizedToken(genre); genre != "" {
			seen[genre] = struct{}{}
		}
	}
	for _, genre := range b {
		if _, ok := seen[normalizedToken(genre)]; ok {
			return true
		}
	}
	return false
}

func topGenres(tracks []Track, byTrackID map[string]Track, interactions []Interaction) map[string]float64 {
	scores := make(map[string]float64)
	for _, interaction := range interactions {
		track, ok := byTrackID[interaction.TrackID]
		if !ok {
			continue
		}
		weight := interactionWeight(interaction)
		if weight <= 0 {
			continue
		}
		for _, genre := range track.Genres {
			scores[strings.ToLower(genre)] += weight
		}
	}
	maxScore := 0.0
	for _, score := range scores {
		maxScore = math.Max(maxScore, score)
	}
	if maxScore == 0 {
		return scores
	}
	for genre, score := range scores {
		scores[genre] = round(score / maxScore)
	}
	return scores
}

func explorationReadiness(confidence float64, interactions []Interaction) float64 {
	negative := 0.0
	for _, interaction := range interactions {
		if interactionWeight(interaction) < 0 {
			negative++
		}
	}
	friction := 0.0
	if len(interactions) > 0 {
		friction = negative / float64(len(interactions))
	}
	return clamp01((0.45 + confidence*0.55) * (1 - friction*0.6))
}

func arrayToSlice(value [featureCount]float64) []float64 {
	out := make([]float64, featureCount)
	for i := range value {
		out[i] = round(value[i])
	}
	return out
}

func contains(values []string, value string) bool {
	return slices.Contains(values, value)
}

func containsFold(values []string, value string) bool {
	for _, candidate := range values {
		if strings.EqualFold(candidate, value) {
			return true
		}
	}
	return false
}

func round(value float64) float64 {
	return math.Round(value*10000) / 10000
}

func ValidateTrack(track Track) error {
	if strings.TrimSpace(track.ID) == "" {
		return fmt.Errorf("track id is required")
	}
	if strings.TrimSpace(track.Title) == "" {
		return fmt.Errorf("track title is required")
	}
	if strings.TrimSpace(track.Artist) == "" {
		return fmt.Errorf("track artist is required")
	}
	return nil
}
