const API_BASE = window.location.hostname === 'localhost' ? 'http://localhost:8080' : '';

const urlInput = document.getElementById('urlInput');
const submitBtn = document.getElementById('submitBtn');
const loading = document.getElementById('loading');
const error = document.getElementById('error');
const seedTrack = document.getElementById('seedTrack');
const recommendations = document.getElementById('recommendations');
const emptyState = document.getElementById('emptyState');

const seedArtwork = document.getElementById('seedArtwork');
const seedTitle = document.getElementById('seedTitle');
const seedArtist = document.getElementById('seedArtist');
const recList = document.getElementById('recList');

const STREAMING_SERVICES = [
    { id: 'spotify', name: 'Spotify', color: '#1DB954' },
    { id: 'apple', name: 'Apple', color: '#FA2D48' },
    { id: 'youtube', name: 'YouTube', color: '#FF0000' },
    { id: 'tidal', name: 'Tidal', color: '#00D4FF' },
    { id: 'deezer', name: 'Deezer', color: '#FF0092' },
    { id: 'soundcloud', name: 'SoundCloud', color: '#FF5500' },
];

function showError(msg) {
    error.textContent = msg;
    error.classList.add('visible');
}

function hideError() {
    error.classList.remove('visible');
}

function setLoading(isLoading) {
    loading.classList.toggle('visible', isLoading);
    submitBtn.disabled = isLoading;
}

function parseUrl(url) {
    const trimmed = url.trim();
    if (!trimmed) return null;

    const uriMatch = trimmed.match(/^spotify:(track|album|playlist|artist):([a-zA-Z0-9]+)$/i);
    if (uriMatch) {
        return { type: 'spotify', itemType: uriMatch[1].toLowerCase(), id: uriMatch[2], url: trimmed };
    }

    let parsed;
    try {
        parsed = new URL(trimmed.includes('://') ? trimmed : `https://${trimmed}`);
    } catch {
        return null;
    }

    const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
    const parts = parsed.pathname.split('/').filter(Boolean);
    if (parts[0]?.startsWith('intl-')) parts.shift();
    if (parts[0] === 'embed') parts.shift();

    if ((host === 'open.spotify.com' || host === 'play.spotify.com') && parts.length >= 2) {
        const itemType = parts[0].toLowerCase();
        if (['track', 'album', 'playlist', 'artist'].includes(itemType)) {
            return { type: 'spotify', itemType, id: parts[1], url: trimmed };
        }
    }

    if (host === 'music.apple.com' && parts.length >= 3) {
        const itemType = parts.includes('playlist') ? 'playlist' : parsed.searchParams.has('i') ? 'song' : parts[2];
        const id = parsed.searchParams.get('i') || parts.at(-1);
        return { type: 'apple', itemType, id, url: trimmed };
    }

    if (host === 'music.youtube.com' || host === 'youtube.com' || host === 'm.youtube.com' || host === 'youtu.be') {
        const id = parsed.searchParams.get('v') || parsed.searchParams.get('list') || parts[0];
        const itemType = parsed.searchParams.has('list') && !parsed.searchParams.has('v') ? 'playlist' : 'video';
        if (id) return { type: host === 'music.youtube.com' ? 'youtube_music' : 'youtube', itemType, id, url: trimmed };
    }

    if (host === 'tidal.com' || host === 'listen.tidal.com') {
        const itemIndex = parts.findIndex(part => ['track', 'album', 'playlist', 'artist'].includes(part));
        if (itemIndex >= 0 && parts[itemIndex + 1]) {
            return { type: 'tidal', itemType: parts[itemIndex], id: parts[itemIndex + 1], url: trimmed };
        }
    }

    if (host.endsWith('deezer.com')) {
        const itemIndex = parts.findIndex(part => ['track', 'album', 'playlist', 'artist'].includes(part));
        if (itemIndex >= 0 && parts[itemIndex + 1]) {
            return { type: 'deezer', itemType: parts[itemIndex], id: parts[itemIndex + 1], url: trimmed };
        }
    }

    if (host === 'soundcloud.com' && parts.length >= 2) {
        const itemType = parts[1] === 'sets' ? 'playlist' : 'track';
        const id = itemType === 'playlist' ? `${parts[0]}/sets/${parts[2] || ''}` : `${parts[0]}/${parts[1]}`;
        return { type: 'soundcloud', itemType, id, url: trimmed };
    }

    if (host.endsWith('bandcamp.com') && parts.length >= 2 && ['track', 'album'].includes(parts[0])) {
        return { type: 'bandcamp', itemType: parts[0], id: `${host.split('.')[0]}/${parts.slice(1).join('/')}`, url: trimmed };
    }

    return null;
}

async function importTrack(parsed) {
    // The backend imports Spotify directly and resolves other supported song URLs through Song.link.
    const resp = await fetch(`${API_BASE}/v1/providers/spotify/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            source: { type: 'url', value: parsed.url },
            market: 'US',
            enrich_musicbrainz: true,
            persist: true
        })
    });

    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to import track');
    }

    return await resp.json();
}

async function getRecommendations(seedTrackId, limit = 10) {
    const resp = await fetch(`${API_BASE}/v1/recommendations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            user_id: 'anonymous-user',
            seed_track_ids: [seedTrackId],
            limit: limit,
            mode: 'balanced'
        })
    });

    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to get recommendations');
    }

    const data = await resp.json();
    return data.data;
}

async function getSonglinkUrls(title, artist) {
    try {
        const resp = await fetch(`https://api.song.link/v1-alpha.1/links?userCountry=US&songTitle=${encodeURIComponent(title)}&artistName=${encodeURIComponent(artist)}`);
        if (!resp.ok) return {};
        const data = await resp.json();
        return data.linksByPlatform || {};
    } catch {
        return {};
    }
}

function displaySeedTrack(track) {
    seedTitle.textContent = track.title;
    seedArtist.textContent = track.artist;
    
    if (track.external?.spotify) {
        const img = document.createElement('img');
        img.src = `https://open.spotify.com/oembed?url=${encodeURIComponent(track.external.spotify)}`;
        // Note: In production, use proper album art from the API
        seedArtwork.innerHTML = '♪';
    }
    
    seedTrack.classList.add('visible');
}

function createServiceLinks(songlinkData, track) {
    const links = [];
    
    const serviceMap = {
        spotify: 'Spotify',
        appleMusic: 'Apple',
        youtubeMusic: 'YouTube',
        youtube: 'YouTube',
        tidal: 'Tidal',
        deezer: 'Deezer',
        soundcloud: 'SoundCloud'
    };

    for (const [platform, label] of Object.entries(serviceMap)) {
        const data = songlinkData[platform];
        if (data?.url) {
            const svc = STREAMING_SERVICES.find(s => 
                s.id === platform.toLowerCase().replace('music', '') || 
                s.name === label
            ) || { name: label, color: '#666' };
            
            links.push({
                name: svc.name,
                url: data.url,
                color: svc.color
            });
        }
    }

    // Fallback: always include search links
    const query = encodeURIComponent(`${track.title} ${track.artist}`);
    const fallbacks = [
        { name: 'Spotify', url: `https://open.spotify.com/search/${query}` },
        { name: 'YouTube', url: `https://music.youtube.com/search?q=${query}` },
        { name: 'Apple', url: `https://music.apple.com/us/search?term=${query}` },
        { name: 'Tidal', url: `https://tidal.com/search?q=${query}` },
    ];

    // Add any missing services
    for (const fb of fallbacks) {
        if (!links.find(l => l.name === fb.name)) {
            const svc = STREAMING_SERVICES.find(s => s.name === fb.name);
            links.push({ ...fb, color: svc?.color || '#666' });
        }
    }

    return links.slice(0, 6);
}

function displayRecommendations(recs) {
    recList.innerHTML = '';
    
    recs.forEach((rec, i) => {
        const item = document.createElement('div');
        item.className = 'rec-item';
        
        const rank = document.createElement('div');
        rank.className = 'rec-rank';
        rank.textContent = rec.rank || i + 1;
        
        const info = document.createElement('div');
        info.className = 'rec-info';
        
        const title = document.createElement('div');
        title.className = 'rec-title';
        title.textContent = rec.track.title;
        
        const artist = document.createElement('div');
        artist.className = 'rec-artist';
        artist.textContent = rec.track.artist;
        
        const reason = document.createElement('div');
        reason.className = 'rec-reason';
        reason.textContent = rec.reason || '';
        
        info.appendChild(title);
        info.appendChild(artist);
        if (rec.reason) info.appendChild(reason);
        
        const links = document.createElement('div');
        links.className = 'rec-links';
        
        // Generate links for this track
        const serviceLinks = createServiceLinks({}, rec.track);
        serviceLinks.forEach(svc => {
            const a = document.createElement('a');
            a.className = 'service-link';
            a.href = svc.url;
            a.target = '_blank';
            a.rel = 'noopener';
            a.title = `Open in ${svc.name}`;
            a.textContent = svc.name.charAt(0);
            a.style.borderColor = svc.color + '40';
            a.style.color = svc.color;
            links.appendChild(a);
        });
        
        item.appendChild(rank);
        item.appendChild(info);
        item.appendChild(links);
        recList.appendChild(item);
    });
    
    recommendations.classList.add('visible');
}

async function handleSubmit() {
    const url = urlInput.value.trim();
    if (!url) {
        showError('Please enter a song URL');
        return;
    }

    const parsed = parseUrl(url);
    if (!parsed) {
        showError('Unsupported URL format. Try a Spotify, Apple Music, or YouTube Music link.');
        return;
    }

    hideError();
    setLoading(true);
    emptyState.style.display = 'none';
    seedTrack.classList.remove('visible');
    recommendations.classList.remove('visible');

    try {
        // Import the track
        const imported = await importTrack(parsed);
        
        if (!imported.track) {
            throw new Error('Could not extract track information');
        }

        displaySeedTrack(imported.track);

        // Get recommendations
        const recs = await getRecommendations(imported.track.id, 10);
        displayRecommendations(recs);

    } catch (err) {
        showError(err.message || 'Something went wrong. Please try again.');
        console.error(err);
    } finally {
        setLoading(false);
    }
}

submitBtn.addEventListener('click', handleSubmit);

urlInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        handleSubmit();
    }
});

// Focus input on load
urlInput.focus();
