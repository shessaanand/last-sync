import sys
import json
import time
import threading
import requests
from datetime import datetime
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QFrame, QDialog,
    QLineEdit, QFormLayout, QDialogButtonBox, QMessageBox,
    QSystemTrayIcon, QMenu, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QBrush, QIcon, QPixmap, QPen, QFontDatabase
)
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ── Constants ──────────────────────────────────────────────────
POLL_ACTIVE     = 5            # seconds — while someone is actively playing
POLL_IDLE       = 30           # seconds — while nothing is playing
CONFIG_FILE     = Path.home() / ".lastsync_config.json"
REDIRECT_URI    = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPE   = "user-modify-playback-state user-read-playback-state"
MAX_LOG_ENTRIES = 60

# ── Default credentials ────────────────────────────────────────
_DEFAULT_CONFIG = {
    "lastfm_user":           "Sparkleeee27",
    "lastfm_api_key":        "c1f3d1398fc34d29f67bf6db804906bb",
    "spotify_client_id":     "a4c88918726247d492e2a8ecc7c47667",
    "spotify_client_secret": "f3b4bb3c083c4148b8f37ad6c741316e",
}

# ── Config helpers ─────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return dict(_DEFAULT_CONFIG)

def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ── Sync worker ────────────────────────────────────────────────
class SyncWorker(QThread):
    # (track, artist, position_ms) — position_ms is how far in they are
    track_changed         = pyqtSignal(str, str, int)
    status_changed        = pyqtSignal(str, str)   # message, level
    idle_signal           = pyqtSignal()
    poll_interval_changed = pyqtSignal(int)         # current interval in seconds

    def __init__(self, sp, cfg: dict):
        super().__init__()
        self._sp         = sp
        self._cfg        = cfg
        self._stop_event = threading.Event()
        self.last_played = None

    def get_now_playing(self):
        """
        Returns (track, artist, started_at_unix) if playing, else (None, None, None).
        Last.fm omits 'date' for the live nowplaying entry; we fall back to
        time.time() and rely on our own wall-clock tracking for elapsed calculation.
        """
        url = (
            "https://ws.audioscrobbler.com/2.0/"
            f"?method=user.getrecenttracks"
            f"&user={self._cfg['lastfm_user']}"
            f"&api_key={self._cfg['lastfm_api_key']}"
            f"&format=json&limit=1"
        )
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data   = resp.json()
        tracks = data["recenttracks"]["track"]
        if isinstance(tracks, list) and tracks[0].get("@attr", {}).get("nowplaying"):
            t = tracks[0]
            # uts is available on the previous (non-live) track; for live it may be absent.
            started_at = int(t.get("date", {}).get("uts", int(time.time())))
            return t["name"], t["artist"]["#text"], started_at
        return None, None, None

    def search_and_play(self, track: str, artist: str, started_at: int) -> bool:
        results = self._sp.search(
            q=f'track:"{track}" artist:"{artist}"', type="track", limit=1
        )
        items = results["tracks"]["items"]
        if not items:
            return False

        item        = items[0]
        uri         = item["uri"]
        duration_ms = item["duration_ms"]

        # Seek to the approximate position they're at.
        # We compute from started_at (Last.fm unix timestamp) and clamp to
        # [0, duration - 2s] so we never seek past the end.
        elapsed_ms  = int((time.time() - started_at) * 1000)
        position_ms = max(0, min(elapsed_ms, duration_ms - 2000))

        self._sp.start_playback(uris=[uri], position_ms=position_ms)
        return True

    def run(self):
        self._stop_event.clear()
        # Wall-clock time when we first saw the current nowplaying key,
        # used to compute elapsed even if Last.fm omits the started_at timestamp.
        self._first_seen: float = None
        self._first_seen_key: tuple = None

        while not self._stop_event.is_set():
            interval = POLL_IDLE
            try:
                track, artist, started_at = self.get_now_playing()

                if track:
                    interval = POLL_ACTIVE
                    key = (track, artist)

                    if key != self.last_played:
                        # New track detected
                        self._first_seen     = time.time()
                        self._first_seen_key = key

                        found = self.search_and_play(track, artist, started_at)
                        if found:
                            self.last_played = key
                            elapsed_ms = int((time.time() - started_at) * 1000)
                            self.track_changed.emit(track, artist, max(0, elapsed_ms))
                        else:
                            self.status_changed.emit(f"Not on Spotify: {track}", "warn")
                    else:
                        # Same track — heartbeat
                        self.status_changed.emit("Synced ✓", "info")

                else:
                    self._first_seen     = None
                    self._first_seen_key = None
                    interval = POLL_IDLE
                    self.idle_signal.emit()

            except spotipy.SpotifyException as e:
                if e.http_status in (401, 403):
                    self.status_changed.emit("Spotify auth error — check Settings", "auth")
                    break
                self.status_changed.emit(f"Spotify: {e}", "error")
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    self.status_changed.emit("Last.fm auth error — check API key in Settings", "auth")
                    break
                self.status_changed.emit(f"Last.fm: {e}", "error")
            except requests.RequestException as e:
                self.status_changed.emit(f"Network (will retry): {e}", "error")
            except Exception as e:
                self.status_changed.emit(str(e), "error")

            self.poll_interval_changed.emit(interval)
            # Interruptible sleep — stop() wakes this immediately
            self._stop_event.wait(timeout=interval)

    def stop(self):
        self._stop_event.set()


# ── Auth worker ────────────────────────────────────────────────
class AuthWorker(QThread):
    auth_success = pyqtSignal(object)   # spotipy.Spotify
    auth_failed  = pyqtSignal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg

    def run(self):
        try:
            sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=self._cfg["spotify_client_id"],
                client_secret=self._cfg["spotify_client_secret"],
                redirect_uri=REDIRECT_URI,
                scope=SPOTIFY_SCOPE,
                cache_path=str(Path.home() / ".lastsync_spotify_cache"),
            ))
            sp.current_user()   # lightweight call to confirm auth
            self.auth_success.emit(sp)
        except spotipy.SpotifyOauthError as e:
            self.auth_failed.emit(f"Spotify OAuth: {e}")
        except Exception as e:
            self.auth_failed.emit(str(e))


# ── Pulsing dot ────────────────────────────────────────────────
class PulseDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._alpha  = 80
        self._active = False
        self._step   = -6
        self._timer  = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def set_active(self, active: bool):
        self._active = active
        self._timer.setInterval(30 if active else 1000)
        if not active:
            self._alpha = 80
            self.update()

    def _tick(self):
        if not self._active:
            return
        self._alpha += self._step
        if self._alpha <= 50:
            self._step = 6
        elif self._alpha >= 255:
            self._step = -6
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = (QColor(29, 185, 84, self._alpha) if self._active
               else QColor(60, 60, 60, self._alpha))
        p.setBrush(QBrush(col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, 10, 10)


# ── Slim progress bar ──────────────────────────────────────────
class ProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self._progress = 0.0

    def set_progress(self, v: float):
        self._progress = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        # track
        p.setBrush(QBrush(QColor(35, 35, 35)))
        p.drawRoundedRect(0, 0, w, h, 1, 1)
        # fill
        fw = int(w * self._progress)
        if fw > 0:
            p.setBrush(QBrush(QColor(29, 185, 84)))
            p.drawRoundedRect(0, 0, fw, h, 1, 1)


# ── Log row widget ─────────────────────────────────────────────
class LogRow(QWidget):
    _FG = {
        "track": "#1db954",
        "info":  "#383838",
        "warn":  "#a07820",
        "error": "#8b2a2a",
        "auth":  "#8b2a2a",
    }

    def __init__(self, text: str, level: str, ts: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        fg = self._FG.get(level, self._FG["info"])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)

        t = QLabel(ts)
        t.setFixedWidth(38)
        t.setStyleSheet(f"color: #252525; font-family: Courier New; font-size: 8px;")
        t.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        dot = QLabel("•")
        dot.setFixedWidth(8)
        dot.setStyleSheet(f"color: {fg}; font-size: 9px;")
        dot.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        msg = QLabel(text)
        msg.setStyleSheet(f"color: {fg}; font-family: Courier New; font-size: 9px;")
        msg.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        lay.addWidget(t)
        lay.addWidget(dot)
        lay.addWidget(msg, 1)


# ── Log panel ──────────────────────────────────────────────────
class LogPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logPanel")
        self.setStyleSheet("""
            QFrame#logPanel {
                background-color: #0c0c0c;
                border: 1px solid #191919;
                border-radius: 14px;
            }
        """)
        self.setFixedHeight(108)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 4)
        outer.setSpacing(2)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel("ACTIVITY")
        lbl.setStyleSheet("color: #202020; font-family: Courier New; font-size: 8px; font-weight: bold; letter-spacing: 3px; border: none;")
        self._cnt = QLabel("")
        self._cnt.setStyleSheet("color: #1a1a1a; font-family: Courier New; font-size: 8px; border: none;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(self._cnt)
        outer.addLayout(hdr)

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        self._rows = QVBoxLayout(inner)
        self._rows.setContentsMargins(0, 2, 0, 0)
        self._rows.setSpacing(0)
        self._rows.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll = scroll
        outer.addWidget(scroll)

        self._entries = []
        self._total   = 0

    def add_entry(self, text: str, level: str = "info"):
        self._total += 1
        ts  = datetime.now().strftime("%H:%M")
        row = LogRow(text, level, ts)
        self._rows.insertWidget(self._rows.count() - 1, row)
        self._entries.append(row)
        while len(self._entries) > MAX_LOG_ENTRIES:
            old = self._entries.pop(0)
            self._rows.removeWidget(old)
            old.deleteLater()
        self._cnt.setText(f"{self._total}")
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))


# ── Settings dialog ────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedWidth(390)
        self.setStyleSheet("QDialog { background: #0a0a0a; }")

        FIELD = """
            QLineEdit {
                background: #141414; border: 1px solid #222222;
                border-radius: 8px; color: #dddddd;
                padding: 9px 12px;
                font-family: Courier New; font-size: 11px;
                selection-background-color: #1db954;
            }
            QLineEdit:focus { border-color: #1db954; }
            QLineEdit:hover { border-color: #2e2e2e; }
        """

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 26, 26, 26)
        lay.setSpacing(0)

        hdr = QLabel("SETTINGS")
        hdr.setStyleSheet("color: #ffffff; font-family: Courier New; font-size: 11px; font-weight: bold; letter-spacing: 5px;")
        lay.addWidget(hdr)
        lay.addSpacing(22)

        def section(txt):
            l = QLabel(txt)
            l.setStyleSheet("color: #333333; font-family: Courier New; font-size: 8px; letter-spacing: 2px;")
            lay.addWidget(l)
            lay.addSpacing(6)

        def fld(ph, pwd=False):
            f = QLineEdit()
            f.setPlaceholderText(ph)
            f.setStyleSheet(FIELD)
            f.setMinimumHeight(38)
            if pwd:
                f.setEchoMode(QLineEdit.EchoMode.Password)
            return f

        section("LAST.FM")
        self.lfm_user = fld("username")
        self.lfm_key  = fld("api key", pwd=True)
        lay.addWidget(self.lfm_user)
        lay.addSpacing(8)
        lay.addWidget(self.lfm_key)
        lay.addSpacing(20)

        section("SPOTIFY")
        self.sp_id  = fld("client id",     pwd=True)
        self.sp_sec = fld("client secret", pwd=True)
        lay.addWidget(self.sp_id)
        lay.addSpacing(8)
        lay.addWidget(self.sp_sec)
        lay.addSpacing(22)

        # Pre-fill
        self.lfm_user.setText(cfg.get("lastfm_user", ""))
        self.lfm_key.setText(cfg.get("lastfm_api_key", ""))
        self.sp_id.setText(cfg.get("spotify_client_id", ""))
        self.sp_sec.setText(cfg.get("spotify_client_secret", ""))

        row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        save   = QPushButton("SAVE")
        for b in (cancel, save):
            b.setMinimumHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet("""
            QPushButton { background: #141414; color: #555555; border: 1px solid #1e1e1e; border-radius: 8px; font-family: Courier New; font-size: 9px; }
            QPushButton:hover { color: #888888; border-color: #2e2e2e; }
        """)
        save.setStyleSheet("""
            QPushButton { background: #1db954; color: #000000; border: none; border-radius: 8px; font-family: Courier New; font-size: 9px; font-weight: bold; letter-spacing: 2px; }
            QPushButton:hover { background: #1ed760; }
        """)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addLayout(row)

    def get_config(self) -> dict:
        return {
            "lastfm_user":           self.lfm_user.text().strip(),
            "lastfm_api_key":        self.lfm_key.text().strip(),
            "spotify_client_id":     self.sp_id.text().strip(),
            "spotify_client_secret": self.sp_sec.text().strip(),
        }

    def is_valid(self) -> bool:
        return all(self.get_config().values())


# ── Main window ────────────────────────────────────────────────
class LastSyncWindow(QMainWindow):
    BTN_R = 26

    def __init__(self):
        super().__init__()
        self._cfg         = load_config()
        self._sp          = None
        self._worker      = None
        self._auth_worker = None
        self._syncing     = False
        self._track_start = 0.0   # wall-clock when current track started (adjusted for seek)
        self._duration_ms = 0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick_progress)

        self._init_fonts()
        self._init_ui()
        self._init_tray()

        if not self._config_is_complete():
            QTimer.singleShot(300, self._prompt_settings)

    # ── Fonts ──
    def _init_fonts(self):
        fams = QFontDatabase.families()
        self._serif = "Georgia"     if "Georgia"     in fams else "DejaVu Serif"
        self._mono  = "Courier New" if "Courier New" in fams else "DejaVu Sans Mono"
        
    def _f(self, family, size, bold=False):
        return QFont(family, size, QFont.Weight.Bold if bold else QFont.Weight.Normal)

    # ── UI ──
    def _init_ui(self):
        self.setWindowTitle("LastSync")
        self.setMinimumSize(420, 640)
        self.setMaximumSize(560, 820)
        self.resize(440, 670)
        self.setStyleSheet("background-color: #0a0a0a;")

        # Programmatic icon — green rounded square with "LS"
        px = QPixmap(64, 64)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(29, 185, 84)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(4, 4, 56, 56, 14, 14)
        p.setPen(QPen(QColor(0, 0, 0)))
        p.setFont(QFont(self._mono, 16, QFont.Weight.Bold))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "LS")
        p.end()
        self.setWindowIcon(QIcon(px))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 26, 26, 22)
        root.setSpacing(0)

        # ── Header ──
        hdr = QHBoxLayout()

        badge = QLabel("LS")
        badge.setFixedSize(34, 34)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet("""
            background: #1db954; color: #000000;
            border-radius: 9px;
            font-family: Courier New; font-size: 11px; font-weight: bold;
        """)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        app_lbl = QLabel("LASTSYNC")
        app_lbl.setFont(self._f(self._mono, 11, bold=True))
        app_lbl.setStyleSheet("color: #e8e8e8; letter-spacing: 5px;")
        self.sub_label = QLabel(f"with  {self._cfg.get('lastfm_user', '—')}")
        self.sub_label.setFont(self._f(self._mono, 8))
        self.sub_label.setStyleSheet("color: #2e2e2e; letter-spacing: 1px;")
        title_col.addWidget(app_lbl)
        title_col.addWidget(self.sub_label)

        gear_btn = QPushButton("⚙")
        gear_btn.setFixedSize(32, 32)
        gear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gear_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #242424; border: none; font-size: 15px; border-radius: 8px; }
            QPushButton:hover { color: #777777; background: #141414; }
        """)
        gear_btn.clicked.connect(self._open_settings)

        self.pulse = PulseDot()

        hdr.addWidget(badge)
        hdr.addSpacing(10)
        hdr.addLayout(title_col)
        hdr.addStretch()
        hdr.addWidget(gear_btn)
        hdr.addSpacing(10)
        hdr.addWidget(self.pulse)
        root.addLayout(hdr)

        root.addSpacing(22)

        # ── Now-playing card ──
        card = QFrame()
        card.setObjectName("npCard")
        card.setStyleSheet("""
            QFrame#npCard {
                background-color: #111111;
                border: 1px solid #1a1a1a;
                border-radius: 20px;
            }
        """)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(22, 20, 22, 18)
        cv.setSpacing(0)

        # Card header row
        ch = QHBoxLayout()
        np_lbl = QLabel("NOW PLAYING")
        np_lbl.setFont(self._f(self._mono, 7, bold=True))
        np_lbl.setStyleSheet("color: #222222; letter-spacing: 4px;")

        self.live_badge = QLabel("● LIVE")
        self.live_badge.setFont(self._f(self._mono, 7, bold=True))
        self.live_badge.setStyleSheet("""
            color: #1db954;
            font-family: Courier New;
            font-size: 7px;
            font-weight: bold;
            letter-spacing: 2px;
        """)
        self.live_badge.hide()

        ch.addWidget(np_lbl)
        ch.addStretch()
        ch.addWidget(self.live_badge)
        cv.addLayout(ch)

        cv.addSpacing(16)

        self.track_label = QLabel("Start sync to listen along")
        self.track_label.setFont(self._f(self._serif, 20, bold=True))
        self.track_label.setStyleSheet("color: #eeeeee;")
        self.track_label.setWordWrap(True)
        cv.addWidget(self.track_label)

        cv.addSpacing(5)

        self.artist_label = QLabel("")
        self.artist_label.setFont(self._f(self._serif, 13))
        self.artist_label.setStyleSheet("color: #444444;")
        self.artist_label.setWordWrap(True)
        cv.addWidget(self.artist_label)

        cv.addSpacing(18)

        self.progress_bar = ProgressBar()
        cv.addWidget(self.progress_bar)
        cv.addSpacing(7)

        time_row = QHBoxLayout()
        self.elapsed_lbl  = QLabel("")
        self.duration_lbl = QLabel("")
        for lbl in (self.elapsed_lbl, self.duration_lbl):
            lbl.setFont(self._f(self._mono, 8))
            lbl.setStyleSheet("color: #252525;")
        time_row.addWidget(self.elapsed_lbl)
        time_row.addStretch()
        time_row.addWidget(self.duration_lbl)
        cv.addLayout(time_row)

        root.addWidget(card)
        root.addSpacing(12)

        # ── Status row ──
        sr = QHBoxLayout()
        self.status_label = QLabel("Ready")
        self.status_label.setFont(self._f(self._mono, 8))
        self.status_label.setStyleSheet("color: #2a2a2a; letter-spacing: 1px;")
        self.status_label.setWordWrap(True)

        self.poll_label = QLabel("")
        self.poll_label.setFont(self._f(self._mono, 8))
        self.poll_label.setStyleSheet("color: #1a1a1a;")
        self.poll_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        sr.addWidget(self.status_label, 1)
        sr.addWidget(self.poll_label)
        root.addLayout(sr)

        root.addSpacing(10)

        # ── Activity log ──
        self.log_panel = LogPanel()
        root.addWidget(self.log_panel)

        root.addSpacing(16)

        # ── Sync button ──
        self.sync_btn = QPushButton("START SYNC")
        self.sync_btn.setFixedHeight(50)
        self.sync_btn.setFont(self._f(self._mono, 10, bold=True))
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start()
        self.sync_btn.clicked.connect(self.toggle_sync)
        root.addWidget(self.sync_btn)

        root.addSpacing(12)

        # ── Footer ──
        self.footer_label = QLabel(self._footer_text())
        self.footer_label.setFont(self._f(self._mono, 8))
        self.footer_label.setStyleSheet("color: #181818; letter-spacing: 1px;")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.footer_label)

    def _footer_text(self):
        user = self._cfg.get("lastfm_user", "—")
        return f"polls {POLL_ACTIVE}s active · {POLL_IDLE}s idle  ·  last.fm/{user}"

    # ── System tray ──
    def _init_tray(self):
        self._tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background: #141414; color: #aaaaaa; font-family: Courier New; font-size: 10px; border: 1px solid #1e1e1e; border-radius: 6px; }
            QMenu::item:selected { background: #1e1e1e; }
        """)
        menu.addAction("Show LastSync").triggered.connect(self.show)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(QApplication.quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda r: self.show() if r == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._tray.show()

    # ── Button styles — BTN_R is the single source of truth ──
    def _btn_start(self):
        r = self.BTN_R
        self.sync_btn.setStyleSheet(f"""
            QPushButton {{
                background: #1db954; color: #000000; border: none;
                border-radius: {r}px; letter-spacing: 4px;
            }}
            QPushButton:hover   {{ background: #1ed760; }}
            QPushButton:pressed {{ background: #17a349; }}
            QPushButton:disabled {{ background: #0c2e17; color: #153320; }}
        """)

    def _btn_stop(self):
        r = self.BTN_R
        self.sync_btn.setStyleSheet(f"""
            QPushButton {{
                background: #141414; color: #cccccc;
                border: 1px solid #232323; border-radius: {r}px; letter-spacing: 4px;
            }}
            QPushButton:hover   {{ background: #1a1a1a; border-color: #2e2e2e; }}
            QPushButton:pressed {{ background: #0a0a0a; }}
        """)

    def _btn_loading(self):
        r = self.BTN_R
        self.sync_btn.setStyleSheet(f"""
            QPushButton {{
                background: #0c2e17; color: #153320; border: none;
                border-radius: {r}px; letter-spacing: 4px;
            }}
        """)

    # ── Config ──
    def _config_is_complete(self):
        return all(self._cfg.get(k, "").strip() for k in
                   ["lastfm_user", "lastfm_api_key", "spotify_client_id", "spotify_client_secret"])

    def _prompt_settings(self):
        QMessageBox.information(self, "Welcome to LastSync",
                                "Fill in your credentials in Settings to get started.")
        self._open_settings()

    def _open_settings(self):
        dlg = SettingsDialog(self._cfg, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.is_valid():
            QMessageBox.warning(self, "Incomplete", "All fields are required.")
            return
        new = dlg.get_config()
        if (new["spotify_client_id"]     != self._cfg.get("spotify_client_id") or
                new["spotify_client_secret"] != self._cfg.get("spotify_client_secret")):
            cache = Path.home() / ".lastsync_spotify_cache"
            if cache.exists():
                cache.unlink()
            self._sp = None
        self._cfg = new
        save_config(self._cfg)
        self._refresh_labels()

    def _refresh_labels(self):
        user = self._cfg.get("lastfm_user", "—")
        self.sub_label.setText(f"with  {user}")
        self.footer_label.setText(self._footer_text())

    # ── Progress ticker ──
    def _tick_progress(self):
        if not self._track_start or not self._duration_ms:
            return
        elapsed_ms = int((time.time() - self._track_start) * 1000)
        elapsed_ms = min(elapsed_ms, self._duration_ms)
        self.progress_bar.set_progress(elapsed_ms / self._duration_ms)
        self.elapsed_lbl.setText(self._ms(elapsed_ms))

    @staticmethod
    def _ms(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"

    # ── Sync flow ──
    def toggle_sync(self):
        if self._syncing:
            self.stop_sync()
        else:
            self.start_sync()

    def start_sync(self):
        if not self._config_is_complete():
            self._prompt_settings()
            return
        self._syncing = True
        self.sync_btn.setText("CONNECTING…")
        self.sync_btn.setEnabled(False)
        self._btn_loading()
        self.pulse.set_active(True)
        self.set_status("Connecting to Spotify…")

        if self._sp is not None:
            self._on_auth_ok(self._sp)
            return

        self._auth_worker = AuthWorker(self._cfg)
        self._auth_worker.auth_success.connect(self._on_auth_ok)
        self._auth_worker.auth_failed.connect(self._on_auth_fail)
        self._auth_worker.start()

    def _on_auth_ok(self, sp):
        self._sp = sp
        self.sync_btn.setText("STOP SYNC")
        self.sync_btn.setEnabled(True)
        self._btn_stop()
        self.live_badge.show()
        self.set_status("Connected — first poll in progress…")
        self.log_panel.add_entry("Sync started", "info")

        self._worker = SyncWorker(self._sp, self._cfg)
        self._worker.track_changed.connect(self._on_track)
        self._worker.status_changed.connect(self._on_status)
        self._worker.idle_signal.connect(self._on_idle)
        self._worker.poll_interval_changed.connect(self._on_poll_interval)
        self._worker.start()
        self._tick_timer.start()

    def _on_auth_fail(self, msg: str):
        self._syncing = False
        self.pulse.set_active(False)
        self.sync_btn.setText("START SYNC")
        self.sync_btn.setEnabled(True)
        self._btn_start()
        self.set_status("⚠ Auth failed — check Settings")
        self.log_panel.add_entry(f"Auth failed: {msg}", "auth")

    def stop_sync(self):
        self._syncing = False
        self.pulse.set_active(False)
        self._tick_timer.stop()
        self.live_badge.hide()
        if self._worker:
            self._worker.stop()
            self._worker.wait()
            self._worker = None
        self.sync_btn.setText("START SYNC")
        self.sync_btn.setEnabled(True)
        self._btn_start()
        self.track_label.setText("Start sync to listen along")
        self.artist_label.setText("")
        self.progress_bar.set_progress(0)
        self.elapsed_lbl.setText("")
        self.duration_lbl.setText("")
        self.poll_label.setText("")
        self._track_start = 0.0
        self._duration_ms = 0
        self.set_status("Stopped")
        self.log_panel.add_entry("Sync stopped", "info")

    # ── Worker signal handlers ──
    def _on_track(self, track: str, artist: str, position_ms: int):
        self.track_label.setText(track)
        self.artist_label.setText(artist)
        self.set_status("Synced ✓")
        # Adjust start time so the progress bar ticks from the right position
        self._track_start = time.time() - (position_ms / 1000)
        self._duration_ms = 0   # will be set by _fetch_duration
        self.progress_bar.set_progress(0)
        self.elapsed_lbl.setText(self._ms(position_ms))
        self.duration_lbl.setText("")
        self.log_panel.add_entry(f"{track} — {artist}", "track")
        self._tray.setToolTip(f"LastSync · {track} — {artist}")
        self._fetch_duration(track, artist)

    def _fetch_duration(self, track: str, artist: str):
        """Fetch track duration from Spotify in a daemon thread."""
        def _run():
            try:
                res   = self._sp.search(q=f'track:"{track}" artist:"{artist}"', type="track", limit=1)
                items = res["tracks"]["items"]
                if items:
                    d = items[0]["duration_ms"]
                    QTimer.singleShot(0, lambda: self._set_duration(d))
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _set_duration(self, ms: int):
        self._duration_ms = ms
        self.duration_lbl.setText(self._ms(ms))

    def _on_status(self, msg: str, level: str):
        if level == "auth":
            self.set_status(f"⚠ {msg}")
            self.log_panel.add_entry(msg, "auth")
            self.stop_sync()
        elif level in ("warn", "error"):
            self.log_panel.add_entry(msg, level)
            if "Synced" not in self.status_label.text():
                self.set_status(f"⚠ {msg}")
        else:
            self.set_status(msg)

    def _on_idle(self):
        user = self._cfg.get("lastfm_user", "them")
        self.track_label.setText("Nothing playing")
        self.artist_label.setText("")
        self.progress_bar.set_progress(0)
        self.elapsed_lbl.setText("")
        self.duration_lbl.setText("")
        self._track_start = 0.0
        self._duration_ms = 0
        self.set_status(f"{user} isn't playing anything")

    def _on_poll_interval(self, interval: int):
        if interval == POLL_ACTIVE:
            self.poll_label.setText(f"↻ {interval}s")
            self.poll_label.setStyleSheet("color: #163a21; font-family: Courier New; font-size: 8px;")
        else:
            self.poll_label.setText(f"↻ {interval}s")
            self.poll_label.setStyleSheet("color: #181818; font-family: Courier New; font-size: 8px;")

    def set_status(self, msg: str):
        self.status_label.setText(msg)

    def closeEvent(self, event):
        if self._worker:
            self._worker.stop()
            self._worker.wait()
        if self._auth_worker and self._auth_worker.isRunning():
            self._auth_worker.wait()
        event.accept()


# ── Entry point ────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    window = LastSyncWindow()
    window.show()
    sys.exit(app.exec())
