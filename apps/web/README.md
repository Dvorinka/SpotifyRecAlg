# Flow Web UI

A minimal, Tidal-inspired music discovery interface. Paste a song link, get recommendations with links to all streaming services.

## Design

- **Colors**: Black background (#000), cyan accent (#00d4ff), subtle grays
- **Typography**: Inter, lightweight with careful tracking
- **Spacing**: Generous whitespace, breathable layout
- **Style**: No cards, minimal borders, focused on content

## Run

```bash
# Terminal 1: Start the backend
cd ../backend
STORE_DRIVER=memory SEED_DEMO_DATA=true go run ./cmd/api

# Terminal 2: Serve the frontend (any static server)
cd apps/web
npx serve . -p 3000
# or
python3 -m http.server 3000
```

Open http://localhost:3000

## Features

- Paste any Spotify, Apple Music, YouTube Music, Tidal, Deezer, or SoundCloud link
- Backend imports track and extracts audio features
- Recommendation engine uses cosine similarity + collaborative filtering
- Results link to all major streaming services via Songlink

## Architecture

- `index.html` — Structure and Tidal-inspired styling
- `app.js` — URL parsing, API calls, recommendation display

The backend handles all the heavy lifting: track import, feature extraction, and the recommendation algorithm.
