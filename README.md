---

# LastSync

Listen along with your friends.

LastSync is a desktop app that automatically plays whatever someone is listening to on Last.fm — on your Spotify — in near real time.

No manual searching. No switching tracks yourself. Just press start.

---

## What this is for

If you’ve ever wanted to:

* listen to the exact same music as a friend
* follow someone’s music taste live
* or just passively vibe with what someone is playing

This app does that automatically.

---

## How it works (simple)

* You enter someone’s Last.fm username
* The app checks what they’re currently playing
* When they play a song → your Spotify plays the same song
* It even jumps to roughly the same timestamp

That’s it.

---

## What you need before starting

You only need to set this up once.

### 1. Spotify (required)

* You must have a Spotify account
* Spotify must be open on your device (phone, desktop, or browser)

---

### 2. Last.fm API key (free)

Get it here: [https://www.last.fm/api/account/create](https://www.last.fm/api/account/create)

* Fill the form (anything works)
* Copy the API key

---

### 3. Spotify Developer App (free)

Go here: [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)

Steps:

1. Click **Create App**
2. Name it anything
3. Add this EXACT redirect URI:

   ```
   http://127.0.0.1:8888/callback
   ```
4. Save
5. Copy:

   * Client ID
   * Client Secret

---

## Installation

### Step 1 — Download / clone

```bash
git clone https://github.com/YOURUSERNAME/LastSync.git
cd LastSync
```

---

### Step 2 — Install dependencies

```bash
pip install pyqt6 spotipy requests
```

---

## Running the app

```bash
python lastsync.py
```

---

## First-time setup (IMPORTANT)

When the app opens:

1. Click the **⚙ (settings icon)**
2. Fill in:

   * Last.fm username (the person you want to sync with)
   * Last.fm API key
   * Spotify Client ID
   * Spotify Client Secret
3. Click **SAVE**

---

## Using the app

1. Make sure Spotify is open on your device
2. Click **START SYNC**
3. A browser window will open → log into Spotify
4. Approve access

After that:

* The app will start syncing automatically
* You’ll see the track name + progress bar
* It keeps updating in the background

You can minimize it — it runs in the system tray.

---

## What you’ll see

* Current track + artist
* Progress bar (shows where you are in the song)
* Live indicator (green pulse = syncing)
* Activity log (what’s happening)
* Poll speed (how often it checks)

---

## Important things to know

* Spotify MUST already be open
* The app will override whatever you're currently playing
* There will always be a small delay (Last.fm limitation)
* Some songs may not exist on Spotify
* The person’s Last.fm profile must be public

---

## If something doesn’t work

### Nothing is playing

* Check if the person is actually listening to something
* Last.fm only shows “now playing” sometimes

---

### Spotify doesn’t play anything

* Open Spotify manually
* Play any song once
* Then try again

---

### Login issues

* Make sure your redirect URI is exactly:

  ```
  http://127.0.0.1:8888/callback
  ```

---

### App crashes

* Make sure you installed:

  ```
  pip install pyqt6 spotipy requests
  ```

---

## Files created by the app

After running, you’ll see:

* `.lastsync_config.json` → your saved credentials
* `.lastsync_spotify_cache` → Spotify login cache

These are stored locally on your system.

---

## Safety note

Do NOT share:

* your Spotify Client Secret
* your config file

They give access to your account.

---

## What’s next (planned)

* Better song matching
* Album art
* Multiple friend sync
* Device selector
* .exe version (no Python needed)

---

## That’s it

Start the app, hit sync, and you’re listening along.

---
