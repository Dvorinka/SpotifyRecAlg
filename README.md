# Flow — Music Discovery

A Spotify-style recommendation system with a clean, Tidal-inspired web interface.

**Architecture:**
- `apps/backend` — Go API with content-based + collaborative filtering recommendation engine
  - `internal/provider/webplayer` — Native Go auth-free Spotify client (TOTP-based)
  - `internal/provider/songlink` — Cross-platform music URL mapping
- `apps/web` — Minimal black/cyan UI for pasting song links and discovering music
- `swingmusic/` — Reference Python implementation with advanced features

## Quick Start (No API Keys Required)

The backend now includes native Go implementation for auth-free Spotify access - no Python service needed!

```bash
# 1. Start the backend (includes native auth-free Spotify client)
cd apps/backend
STORE_DRIVER=memory SEED_DEMO_DATA=true go run ./cmd/api

# 2. In another terminal, start the web UI
cd apps/web
python3 -m http.server 3000

# 3. Open http://localhost:3000
```

Or with Docker Compose (coming soon):
```bash
docker compose up
```

## Features

- Paste any Spotify, Apple Music, YouTube Music, Tidal, Deezer, or SoundCloud link
- Backend imports tracks, resolves supported music URLs to Spotify when possible, extracts audio features, and runs the recommendation algorithm
- Recommendations use weighted audio similarity + metadata affinity + collaborative filtering + diversity reranking
- Results include links to all major streaming services

## Design

Tidal-inspired: black background (#000), cyan accent (#00d4ff), generous whitespace, Inter typography, minimal UI with no card clutter.

## Documentation

- [Backend API](apps/backend/README.md) — Go recommendation engine, endpoints, configuration
- [Web UI](apps/web/README.md) — Frontend structure and development

## Auth Options

**No authentication (default):**
The backend includes a native Go webplayer client that generates TOTP tokens (same method as official Web Player) to get anonymous access. No Spotify account, no API keys, no Python service required.

**With official Spotify API (optional):**
Set `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` env vars for official API access. Falls back to native webplayer if not configured.

## Algorithm

The recommendation engine combines:
1. **Content-based**: Weighted cosine similarity over Spotify-style audio feature ranges
2. **Metadata affinity**: Genre and artist matching for cold-start and missing-feature imports
3. **Collaborative**: Pearson-style neighborhood scores with overlap shrinkage
4. **Exploration**: Controlled distance from taste vector for comfort, balanced, and discovery modes
5. **Diversity**: Maximal Marginal Relevance reranking to avoid similar recommendations
6. **Safety/Controls**: Explicit filters, artist/track exclusions, skip/dislike suppression, popularity dampening

See [project.md](project.md) for deep dive into Spotify's algorithm architecture.
