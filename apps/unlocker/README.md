# Music Unlocker

Auth-free music metadata service using swingmusic's reverse-engineered clients.

## What it does

- Uses TOTP with hardcoded secret (same as official Spotify Web Player)
- No user authentication required
- Gets track/album/playlist metadata from Spotify
- Cross-platform links via Song.link API

## Run

```bash
cd apps/unlocker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

Service runs on port 5000.

## Endpoints

- `POST /parse` - Parse any music URL
- `GET /spotify/track/<id>` - Get track metadata
- `GET /spotify/album/<id>` - Get album with tracks
- `GET /spotify/playlist/<id>` - Get playlist with tracks
- `POST /spotify/search` - Search tracks
- `GET /links/<spotify_id>` - Get cross-platform links
- `POST /import` - Import from any URL (universal)

## Integration with Go backend

The Go backend can call this service when `SPOTIFY_CLIENT_ID` is not set.
