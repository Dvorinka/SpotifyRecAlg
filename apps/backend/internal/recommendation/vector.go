package recommendation

import "math"

const featureCount = 12

var featureNames = []string{
	"danceability",
	"energy",
	"loudness",
	"speechiness",
	"acousticness",
	"instrumentalness",
	"liveness",
	"valence",
	"tempo",
	"time_signature",
	"key",
	"mode",
}

type featureSpec struct {
	min    float64
	max    float64
	weight float64
}

var featureSpecs = [featureCount]featureSpec{
	{min: 0, max: 1, weight: 1.12},    // danceability
	{min: 0, max: 1, weight: 1.18},    // energy
	{min: -60, max: 0, weight: 0.78},  // loudness
	{min: 0, max: 1, weight: 0.72},    // speechiness
	{min: 0, max: 1, weight: 1.02},    // acousticness
	{min: 0, max: 1, weight: 0.82},    // instrumentalness
	{min: 0, max: 1, weight: 0.44},    // liveness
	{min: 0, max: 1, weight: 1.08},    // valence
	{min: 40, max: 220, weight: 0.92}, // tempo
	{min: 1, max: 7, weight: 0.22},    // time signature
	{min: 0, max: 11, weight: 0.20},   // key
	{min: 0, max: 1, weight: 0.16},    // mode
}

type featureBounds struct {
	min [featureCount]float64
	max [featureCount]float64
}

func vector(features AudioFeatures) [featureCount]float64 {
	return [featureCount]float64{
		features.Danceability,
		features.Energy,
		features.Loudness,
		features.Speechiness,
		features.Acousticness,
		features.Instrumentalness,
		features.Liveness,
		features.Valence,
		features.Tempo,
		features.TimeSignature,
		features.Key,
		features.Mode,
	}
}

func bounds(tracks []Track) featureBounds {
	var b featureBounds
	for i := range featureCount {
		b.min[i] = featureSpecs[i].min
		b.max[i] = featureSpecs[i].max
	}

	for _, track := range tracks {
		if !hasAudioFeatures(track.Features) {
			continue
		}
		v := vector(track.Features)
		for i, value := range v {
			if value < b.min[i] {
				b.min[i] = value
			}
			if value > b.max[i] {
				b.max[i] = value
			}
		}
	}

	for i := range featureCount {
		if math.IsInf(b.min[i], 0) || math.IsInf(b.max[i], 0) || b.min[i] == b.max[i] {
			b.min[i] = 0
			b.max[i] = 1
		}
	}
	return b
}

func normalize(features AudioFeatures, b featureBounds) [featureCount]float64 {
	raw := vector(features)
	var out [featureCount]float64
	for i, value := range raw {
		denominator := b.max[i] - b.min[i]
		if denominator == 0 {
			out[i] = 0
			continue
		}
		out[i] = clamp01((value - b.min[i]) / denominator)
	}
	return out
}

func cosine(a, b [featureCount]float64) float64 {
	var dot, normA, normB float64
	for i := range featureCount {
		weight := featureSpecs[i].weight
		dot += weight * a[i] * b[i]
		normA += weight * a[i] * a[i]
		normB += weight * b[i] * b[i]
	}
	if normA == 0 || normB == 0 {
		return 0
	}
	return dot / (math.Sqrt(normA) * math.Sqrt(normB))
}

func distance(a, b [featureCount]float64) float64 {
	return 1 - cosine(a, b)
}

func clamp01(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 1 {
		return 1
	}
	return value
}

func hasAudioFeatures(features AudioFeatures) bool {
	raw := vector(features)
	nonZero := 0
	for _, value := range raw {
		if value != 0 {
			nonZero++
		}
	}
	return nonZero >= 4 && features.Tempo > 0 && features.TimeSignature > 0
}
