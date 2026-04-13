# SpotifyRecAlg Backend

Go recommendation API for music catalogs. It combines the approaches from `project.md` and the included papers:

- content-based exploration over normalized audio features
- weighted Spotify-style audio similarity over fixed feature ranges
- metadata affinity for genre/artist fallback when audio features are missing
- collaborative exploitation using Pearson-style neighborhood scores
- seed-track and manual feature targeting
- explicit user controls for hidden tracks, genres, artists, and explicit content
- popularity dampening, safety penalties, constrained commercial boosts, and diversity reranking
- response explanations so clients can show why a track was recommended

## Authentication Options

**Option 1: Auth-free (default)** - Native Go webplayer client
No Spotify API credentials needed. The backend includes a native webplayer client that generates TOTP tokens (same method as official Web Player) to get anonymous access. No external services required.

```bash
cd apps/backend
STORE_DRIVER=memory SEED_DEMO_DATA=true go run ./cmd/api
```

**Option 2: Official Spotify API** - Set credentials
```bash
export SPOTIFY_CLIENT_ID=...
export SPOTIFY_CLIENT_SECRET=...
cd apps/backend && go run ./cmd/api
```

The backend automatically falls back to the native webplayer client if Spotify credentials are not configured.

## Run Locally

Memory mode, with demo data:

```bash
cd apps/backend
STORE_DRIVER=memory SEED_DEMO_DATA=true go run ./cmd/api
```

Postgres mode:

```bash
docker compose -f infra/docker-compose.yml up postgres -d
cd apps/backend
go install github.com/pressly/goose/v3/cmd/goose@latest
goose -dir migrations postgres "postgres://spotify:spotify@localhost:5432/spotifyrec?sslmode=disable" up
go run ./cmd/api
```

Request recommendations:

```bash
curl -s http://localhost:8080/v1/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-user","limit":5,"mode":"balanced"}'
```

Import one Spotify track (works with unlocker or official API):

```bash
curl -s http://localhost:8080/v1/providers/spotify/import \
  -H 'Content-Type: application/json' \
  -d '{"source":{"type":"url","value":"https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp"}, "market":"US", "enrich_musicbrainz":true, "persist":true}'
```

## API Surface

- `POST /v1/tracks` upsert one track
- `PUT /v1/tracks/batch` upsert up to 1000 tracks
- `POST /v1/interactions` ingest play, skip, like, dislike, save, or hide events
- `POST /v1/recommendations` create explainable ranked recommendations
- `GET /v1/users/{user_id}/taste-profile` inspect the computed profile
- `GET /v1/users/{user_id}/controls` read taste and safety controls
- `PUT /v1/users/{user_id}/controls` update controls
- `POST /v1/providers/spotify/import` import Spotify track, album, playlist, or artist tracks
- `POST /v1/providers/spotify/search` search Spotify tracks with limit capped at 10
- `POST /v1/providers/musicbrainz/enrich` enrich existing tracks by ISRC or title/artist search
- `GET /v1/providers/status` inspect provider configuration, availability, and cache stats
- `GET /healthz` liveness
- `GET /readyz` storage readiness

See `docs/openapi.yaml` for the contract.

## Architecture

The HTTP layer depends on a small storage interface and the recommendation engine depends only on a snapshot provider. That keeps this service wireable to another backend: you can replace Postgres with your own catalog, data lake, event stream, or user service without changing the scorer.

Core scoring:

```text
final =
  content_weight * weighted_audio_and_metadata_similarity
  + collaborative_weight * overlap_shrunk_neighbor_score
  + popularity_weight * mode_aware_popularity_fit
  + exploration_weight * target_distance_score
  + constrained_commercial_boost

final *= safety_score * negative_feedback_penalty
```

Candidates are then reranked with a Maximal Marginal Relevance style diversity pass so the top results are not duplicates of the same audio neighborhood.

## Production Notes

- Set `API_KEYS` for backend-to-backend API key protection.
- Set `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` for Spotify client credentials auth, or `SPOTIFY_BEARER_TOKEN` for a short-lived externally managed bearer token.
- Set `SPOTIFY_MARKET` to the default two-letter market, for example `US`.
- Set `MUSICBRAINZ_APP_NAME` and `MUSICBRAINZ_CONTACT`; MusicBrainz requires an identifying User-Agent.
- Set `PROVIDER_CACHE_TTL_HOURS` to control provider payload cache freshness. Expired cache entries may be used as stale fallback when an upstream provider fails.
- Keep user authentication in the parent product and pass stable opaque `user_id` values to this service.
- Run goose migrations before starting Postgres mode.
- Use bulk ingestion for catalog updates and append-only interaction events.
- For large catalogs, replace full snapshots with vector indexes or precomputed candidate sets while keeping the same engine contract.
- When Spotify API credentials are provided, the backend uses the official Web API. Otherwise, it uses the native Go webplayer client which generates TOTP tokens for anonymous access (no user account required).
