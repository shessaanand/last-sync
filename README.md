# LastSync

A lightweight desktop app that syncs your Spotify playback to a friend's Last.fm scrobbles in real time so that you can listen along to exactly what they're hearing, automatically.

Built with Python, PyQt6, Spotipy, and the Last.fm API.

---

## What it does

LastSync polls a Last.fm user's profile every 30 seconds to see what they're currently scrobbling. When it detects a new track, it searches Spotify for that song and plays it on your active Spotify device, keeping you in sync with whatever they're listening to, hands-free.

---

## Features

- Real-time sync with any public Last.fm profile
- Clean dark-themed desktop UI built with PyQt6
- Pulsing indicator showing live sync status
- Displays the current track name and artist
- Gracefully handles tracks not available on Spotify
- Start and stop syncing at any time with one click

---

## Requirements

- Python 3.8 or higher
- A **Spotify account** with an active device (desktop app, mobile, or web player open)
- A **Spotify Developer app**: get one at [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
- A **Last.fm API key**: get one at [last.fm/api](https://www.last.fm/api)
- The person you're syncing with must have a **public Last.fm profile**

---

## Installation

### 1. Clone or download the repo

```
git clone https://github.com/YOURUSERNAME/LastSync.git
cd LastSync
```

### 2. Install dependencies

All required packages can be installed with:

```
pip install pyqt6 spotipy requests
```

| Package | Purpose |
|---|---|
| `pyqt6` | Desktop UI framework |
| `spotipy` | Spotify Web API wrapper |
| `requests` | HTTP requests to Last.fm API |

`sys` and `time` are part of Python's standard library and no installation is needed.

### 3. Set up your credentials

Open `lastsync.py` and fill in your credentials at the top of the file:

```python
LASTFM_API_KEY = "your_lastfm_api_key"
LASTFM_USER    = "the_lastfm_username_to_sync_with"

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id="your_spotify_client_id",
    client_secret="your_spotify_client_secret",
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="user-modify-playback-state"
))
```

See the **Getting Credentials** section below for how to obtain each one.

---

## Getting Credentials

### Last.fm API Key
1. Go to [last.fm/api/account/create](https://www.last.fm/api/account/create)
2. Fill in the form (the application name and description can be anything)
3. Copy the **API key** you're given

### Spotify Client ID & Secret
1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) and log in
2. Click **Create App**
3. Fill in any name and description
4. Set the Redirect URI to exactly: `http://127.0.0.1:8888/callback`
5. Select **Web API** under APIs used
6. After saving, click into your app and copy the **Client ID** and **Client Secret**

---

## Running the app

Make sure Spotify is open and playing on one of your devices first, then run:

```
python lastsync.py
```

On first run, a browser window will open asking you to log into Spotify and grant permission. After approving, you may see a "page not found" error in your browser — that's normal. The app will start running automatically.

Click **START SYNC** in the app window to begin syncing.

---

## How it works

1. Every 30 seconds, the app calls the Last.fm `user.getrecenttracks` API endpoint
2. It checks if the most recent track has a `nowplaying` flag (meaning it's actively being listened to)
3. If a new track is detected, it searches the Spotify API for a match by track name and artist
4. If found, it sends a playback command to your active Spotify device via the Spotify Web API
5. The UI updates to show the current track and artist

---

## Notes & Limitations

- **Spotify must be open** on a device before starting — the app cannot start playback from cold
- **The app will override** whatever you're currently listening to on Spotify
- **Scrobble delay**: Last.fm scrobbles fire at around the 50% mark of a song, so you'll always be slightly behind the person you're syncing with
- **Track availability**: if the track isn't on Spotify in your region, the app will show a warning and wait for the next song
- **Private profiles**: the Last.fm user must have their profile set to public for scrobbles to be visible

---

## Project structure

```
LastSync/
├── lastsync.py       ← main application
├── .env.example      ← credential template
├── .gitignore        ← excludes sensitive files from Git
└── README.md         ← this file
```
