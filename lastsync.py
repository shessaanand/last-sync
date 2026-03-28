import sys
import requests
import time
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ── Credentials ───────────────────────────────────────────────
# Fill these in with your own values (see .env.example)
LASTFM_API_KEY = "your_lastfm_api_key"
LASTFM_USER    = "the_lastfm_username_to_sync_with"

sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
    client_id="your_spotify_client_id",
    client_secret="your_spotify_client_secret",
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="user-modify-playback-state"
))

# ── Worker thread ──────────────────────────────────────────────
class SyncWorker(QThread):
    track_changed  = pyqtSignal(str, str)
    status_changed = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running     = False
        self.last_played = None

    def get_now_playing(self):
        url = (
            f"https://ws.audioscrobbler.com/2.0/"
            f"?method=user.getrecenttracks"
            f"&user={LASTFM_USER}"
            f"&api_key={LASTFM_API_KEY}"
            f"&format=json&limit=1"
        )
        data   = requests.get(url, timeout=8).json()
        tracks = data["recenttracks"]["track"]
        if isinstance(tracks, list) and tracks[0].get("@attr", {}).get("nowplaying"):
            t = tracks[0]
            return t["name"], t["artist"]["#text"]
        return None, None

    def search_and_play(self, track, artist):
        results = sp.search(q=f'track:"{track}" artist:"{artist}"', type="track", limit=1)
        items   = results["tracks"]["items"]
        if items:
            sp.start_playback(uris=[items[0]["uri"]])
            return True
        return False

    def run(self):
        self.running = True
        while self.running:
            try:
                track, artist = self.get_now_playing()
                if track:
                    if (track, artist) != self.last_played:
                        found = self.search_and_play(track, artist)
                        if found:
                            self.last_played = (track, artist)
                            self.track_changed.emit(track, artist)
                        else:
                            self.error_occurred.emit(f"Not on Spotify: {track}")
                else:
                    self.status_changed.emit("idle")
            except Exception as e:
                self.error_occurred.emit(str(e))
            time.sleep(30)

    def stop(self):
        self.running = False


# ── Pulsing dot widget ─────────────────────────────────────────
class PulseDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._alpha= 255
        self._active= False
        self._step= -8
        self._timer=QTimer(self)
        self._timer.timeout.connect(self._pulse)
        self._timer.start(30)

    def set_active(self, active):
        self._active=active

    def _pulse(self):
        if not self._active:
            self._alpha=80
            self.update()
            return
        self._alpha +=self._step
        if self._alpha<=60:
            self._step=8
        elif self._alpha>=255:
            self._step=-8
        self.update()

    def paintEvent(self, event):
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(29, 185, 84, self._alpha) if self._active else QColor(100, 100, 100, self._alpha)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, 12, 12)


# ── Main window ────────────────────────────────────────────────
class LastSyncWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker  = None
        self.syncing = False
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle("LastSync")
        self.setFixedSize(440, 560)
        self.setStyleSheet("background-color: #111111;")

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(36, 36, 36, 36)
        root.setSpacing(0)

        # ── Header ──
        header    = QHBoxLayout()
        app_label = QLabel("LASTSYNC")
        app_label.setFont(QFont("Courier New", 13, QFont.Weight.Bold))
        app_label.setStyleSheet("color: #ffffff; letter-spacing: 6px;")
        self.pulse = PulseDot()
        header.addWidget(app_label)
        header.addStretch()
        header.addWidget(self.pulse)
        root.addLayout(header)

        root.addSpacing(8)

        sub = QLabel(f"listening with  {LASTFM_USER}")
        sub.setFont(QFont("Courier New", 9))
        sub.setStyleSheet("color: #888888; letter-spacing: 2px;")
        root.addWidget(sub)

        root.addSpacing(36)

        # ── Now playing card ──
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #2e2e2e;
                border-radius: 18px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 26, 28, 28)
        card_layout.setSpacing(10)

        now_label = QLabel("NOW PLAYING")
        now_label.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        now_label.setStyleSheet("color: #1db954; letter-spacing: 4px; border: none;")
        card_layout.addWidget(now_label)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("background-color: #2e2e2e; border: none; max-height: 1px;")
        card_layout.addWidget(line)

        card_layout.addSpacing(6)

        self.track_label = QLabel("—")
        self.track_label.setFont(QFont("Georgia", 24, QFont.Weight.Bold))
        self.track_label.setStyleSheet("color: #ffffff; border: none;")
        self.track_label.setWordWrap(True)
        card_layout.addWidget(self.track_label)

        self.artist_label = QLabel("")
        self.artist_label.setFont(QFont("Georgia", 14))
        self.artist_label.setStyleSheet("color: #aaaaaa; border: none;")
        self.artist_label.setWordWrap(True)
        card_layout.addWidget(self.artist_label)

        root.addWidget(card)
        root.addSpacing(20)

        # ── Status line ──
        self.status_label = QLabel("Ready to sync")
        self.status_label.setFont(QFont("Courier New", 9))
        self.status_label.setStyleSheet("color: #777777; letter-spacing: 1px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.status_label)

        root.addStretch()

        # ── Sync button ──
        self.sync_btn = QPushButton("START SYNC")
        self.sync_btn.setFixedHeight(56)
        self.sync_btn.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._set_btn_start_style()
        self.sync_btn.clicked.connect(self.toggle_sync)
        root.addWidget(self.sync_btn)

        root.addSpacing(16)

        # ── Footer ──
        footer = QLabel(f"checks every 30s  ·  last.fm/{LASTFM_USER}")
        footer.setFont(QFont("Courier New", 8))
        footer.setStyleSheet("color: #444444; letter-spacing: 1px;")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(footer)

    def _set_btn_start_style(self):
        self.sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #1db954;
                color: #000000;
                border: none;
                border-radius: 28px;
                letter-spacing: 4px;
            }
            QPushButton:hover  { background-color: #1ed760; }
            QPushButton:pressed { background-color: #17a349; }
        """)

    def _set_btn_stop_style(self):
        self.sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #1a1a1a;
                color: #ffffff;
                border: 1px solid #333333;
                border-radius: 28px;
                letter-spacing: 4px;
            }
            QPushButton:hover  { background-color: #222222; }
            QPushButton:pressed { background-color: #111111; }
        """)

    def toggle_sync(self):
        if not self.syncing:
            self.start_sync()
        else:
            self.stop_sync()

    def start_sync(self):
        self.syncing = True
        self.pulse.set_active(True)
        self.sync_btn.setText("STOP SYNC")
        self._set_btn_stop_style()
        self.set_status("Syncing…")

        self.worker = SyncWorker()
        self.worker.track_changed.connect(self.on_track_changed)
        self.worker.status_changed.connect(self.on_status_changed)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.start()

    def stop_sync(self):
        self.syncing = False
        self.pulse.set_active(False)
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.sync_btn.setText("START SYNC")
        self._set_btn_start_style()
        self.track_label.setText("—")
        self.artist_label.setText("")
        self.set_status("Stopped")

    def on_track_changed(self, track, artist):
        self.track_label.setText(track)
        self.artist_label.setText(artist)
        self.set_status("Synced ✓")

    def on_status_changed(self, status):
        if status == "idle":
            self.set_status(f"{LASTFM_USER} isn't playing anything…")

    def on_error(self, msg):
        self.set_status(f"⚠ {msg}")

    def set_status(self, msg):
        self.status_label.setText(msg)

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
        event.accept()


# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = LastSyncWindow()
    window.show()
    sys.exit(app.exec())
