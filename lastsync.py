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
    QLineEdit, QMessageBox, QSystemTrayIcon, QMenu, QScrollArea
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QBrush, QIcon, QPixmap, QPen,
    QFontDatabase, QPainterPath
)
import spotipy
from spotipy.oauth2 import SpotifyOAuth

# ── Constants ──────────────────────────────────────────────────
POLL_ACTIVE     = 2
POLL_IDLE       = 5
CONFIG_FILE     = Path.home() / ".lastsync_config.json"
REDIRECT_URI    = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPE   = "user-modify-playback-state user-read-playback-state"
MAX_LOG_ENTRIES = 60
FALLBACK_URI    = "spotify:track:4yur1GSBfuS1VADyUYocqd"

# ── Palette ────────────────────────────────────────────────────
C = {
    "green":       "#1db954",
    "green_hi":    "#1ed760",
    "green_lo":    "#17a349",
    "green_dim":   "#0c2e17",
    "green_text":  "#153320",
    "bg":          "#0a0a0a",
    "bg1":         "#0d0d0d",
    "bg2":         "#111111",
    "bg3":         "#161616",
    "border":      "#1e1e1e",
    "border2":     "#252525",
    "border3":     "#303030",
    "text":        "#e8e8e8",
    "text_dim":    "#aaaaaa",
    "text_mid":    "#666666",
    "text_muted":  "#444444",
    "text_ghost":  "#2a2a2a",
    "warn":        "#b08020",
    "error":       "#9b3a3a",
    "fallback":    "#6a4a9a",
}

# ── Default config ─────────────────────────────────────────────
_DEFAULT_CONFIG = {
    "lastfm_user":           "",
    "lastfm_api_key":        "",
    "spotify_client_id":     "",
    "spotify_client_secret": "",
}

# ── Config helpers ─────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return {**_DEFAULT_CONFIG, **data}
        except Exception as e:
            print(f"[lastsync] config load error: {e}")
    return dict(_DEFAULT_CONFIG)

def save_config(cfg: dict):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        print(f"[lastsync] config save error: {e}")


# ── Spotify search helper ──────────────────────────────────────
def spotify_search(sp, track: str, artist: str):
    """
    Two-pass search so regional/special-char titles are still found:
      Pass 1 — strict quoted:  track:"X" artist:"Y"
      Pass 2 — loose unquoted: X Y
    Returns items list or [].
    """
    for q in (
        f'track:"{track}" artist:"{artist}"',
        f'{track} {artist}',
    ):
        try:
            res   = sp.search(q=q, type="track", limit=1)
            items = res["tracks"]["items"]
            if items:
                return items
        except Exception:
            pass
    return []


# ── Sync worker ────────────────────────────────────────────────
class SyncWorker(QThread):
    track_changed         = pyqtSignal(str, str, int, int, str)
    status_changed        = pyqtSignal(str, str)
    idle_signal           = pyqtSignal()
    poll_interval_changed = pyqtSignal(int)

    def __init__(self, sp, cfg: dict):
        super().__init__()
        self._sp             = sp
        self._cfg            = cfg
        self._stop_event     = threading.Event()
        self.last_played     = None
        self._first_seen     = None
        self._first_seen_key = None

    def get_now_playing(self):
        url = (
            "https://ws.audioscrobbler.com/2.0/"
            f"?method=user.getrecenttracks"
            f"&user={self._cfg['lastfm_user']}"
            f"&api_key={self._cfg['lastfm_api_key']}"
            f"&format=json&limit=1"
        )
        resp   = requests.get(url, timeout=8)
        resp.raise_for_status()
        tracks = resp.json()["recenttracks"]["track"]
        if isinstance(tracks, list) and tracks[0].get("@attr", {}).get("nowplaying"):
            t          = tracks[0]
            started_at = int(t.get("date", {}).get("uts", int(time.time())))
            return t["name"], t["artist"]["#text"], started_at
        return None, None, None

    def search_and_play(self, track: str, artist: str, first_seen: float):
        items = spotify_search(self._sp, track, artist)

        if not items:
            try:
                self._sp.start_playback(uris=[FALLBACK_URI])
            except Exception:
                pass
            return False, 0, ""

        item        = items[0]
        uri         = item["uri"]
        duration_ms = item["duration_ms"]

        art_url = ""
        images  = item.get("album", {}).get("images", [])
        if images:
            sized   = [i for i in images if i.get("width", 0) >= 64]
            art_url = (sized[-1] if sized else images[-1]).get("url", "")

        elapsed_ms  = int((time.time() - first_seen) * 1000)
        position_ms = max(0, min(elapsed_ms, duration_ms - 2000))
        self._sp.start_playback(uris=[uri], position_ms=position_ms)
        return True, duration_ms, art_url

    def run(self):
        self._stop_event.clear()
        while not self._stop_event.is_set():
            interval = POLL_IDLE
            try:
                track, artist, started_at = self.get_now_playing()

                if track:
                    interval = POLL_ACTIVE
                    key      = (track, artist)

                    if key != self.last_played:
                        self._first_seen     = time.time()
                        self._first_seen_key = key

                        found, dur_ms, art_url = self.search_and_play(
                            track, artist, self._first_seen
                        )
                        if found:
                            self.last_played = key
                            elapsed_ms = int((time.time() - self._first_seen) * 1000)
                            self.track_changed.emit(
                                track, artist, max(0, elapsed_ms), dur_ms, art_url
                            )
                        else:
                            self.status_changed.emit(f"Not on Spotify: {track}", "warn")
                    else:
                        self.status_changed.emit("Synced ✓", "info")
                else:
                    self._first_seen     = None
                    self._first_seen_key = None
                    self.idle_signal.emit()

            except spotipy.SpotifyException as e:
                if e.http_status in (401, 403):
                    self.status_changed.emit("Spotify auth error — check Settings", "auth")
                    break
                self.status_changed.emit(f"Spotify: {e}", "error")
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code in (401, 403):
                    self.status_changed.emit("Last.fm auth error — check Settings", "auth")
                    break
                self.status_changed.emit(f"Last.fm: {e}", "error")
            except requests.RequestException as e:
                self.status_changed.emit(f"Network (will retry): {e}", "error")
            except Exception as e:
                self.status_changed.emit(str(e), "error")

            self.poll_interval_changed.emit(interval)
            self._stop_event.wait(timeout=interval)

    def stop(self):
        self._stop_event.set()


# ── Auth worker ────────────────────────────────────────────────
class AuthWorker(QThread):
    auth_success = pyqtSignal(object)
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
            sp.current_user()
            self.auth_success.emit(sp)
        except spotipy.SpotifyOauthError as e:
            self.auth_failed.emit(f"Spotify OAuth: {e}")
        except Exception as e:
            self.auth_failed.emit(str(e))


# ── Art loader ─────────────────────────────────────────────────
class ArtLoader(QThread):
    art_ready = pyqtSignal(QPixmap)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        try:
            resp = requests.get(self._url, timeout=6)
            resp.raise_for_status()
            px = QPixmap()
            px.loadFromData(resp.content)
            if not px.isNull():
                self.art_ready.emit(px)
        except Exception:
            pass


# ── Pulse dot ──────────────────────────────────────────────────
class PulseDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._alpha  = 80
        self._active = False
        self._step   = -5
        self._timer  = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def set_active(self, v: bool):
        self._active = v
        self._timer.setInterval(25 if v else 1000)
        if not v:
            self._alpha = 80
            self.update()

    def _tick(self):
        if not self._active:
            return
        self._alpha += self._step
        if self._alpha <= 40:
            self._step = 5
        elif self._alpha >= 255:
            self._step = -5
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(29, 185, 84, self._alpha) if self._active else QColor(50, 50, 50, 120)
        p.setBrush(QBrush(col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, 8, 8)


# ── Album art widget ───────────────────────────────────────────
class ArtWidget(QWidget):
    SIZE = 64

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self._px = None

    def set_pixmap(self, px: QPixmap):
        self._px = px.scaled(
            self.SIZE, self.SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.update()

    def clear(self):
        self._px = None
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.SIZE, self.SIZE, 10, 10)
        p.setClipPath(path)
        if self._px:
            p.drawPixmap(0, 0, self._px)
        else:
            p.fillRect(0, 0, self.SIZE, self.SIZE, QColor(22, 22, 22))
            p.setPen(QColor(45, 45, 45))
            p.setFont(QFont("Courier New", 20))
            p.drawText(0, 0, self.SIZE, self.SIZE, Qt.AlignmentFlag.AlignCenter, "♪")


# ── Progress bar ───────────────────────────────────────────────
class ProgressBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(4)
        self._v = 0.0

    def set_progress(self, v: float):
        self._v = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(28, 28, 28)))
        p.drawRoundedRect(0, 0, w, h, 2, 2)
        fw = int(w * self._v)
        if fw > 1:
            p.setBrush(QBrush(QColor(29, 185, 84)))
            p.drawRoundedRect(0, 0, fw, h, 2, 2)


# ── Log row ────────────────────────────────────────────────────
class LogRow(QWidget):
    _FG = {
        "track":    C["green"],
        "info":     "#3a3a3a",
        "warn":     C["warn"],
        "error":    C["error"],
        "auth":     C["error"],
        "fallback": C["fallback"],
    }

    def __init__(self, text: str, level: str, ts: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)
        fg = self._FG.get(level, self._FG["info"])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(7)

        t = QLabel(ts)
        t.setFixedWidth(54)
        t.setStyleSheet("color: #282828; font-family: Courier New; font-size: 8px;")
        t.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        dot = QLabel("●")
        dot.setFixedWidth(8)
        dot.setStyleSheet(f"color: {fg}; font-size: 6px;")
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
        self.setStyleSheet(f"""
            QFrame#logPanel {{
                background-color: {C['bg1']};
                border: 1px solid {C['border']};
                border-radius: 14px;
            }}
        """)
        self.setFixedHeight(106)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 7, 0, 4)
        outer.setSpacing(1)

        hdr = QHBoxLayout()
        hdr.setContentsMargins(12, 0, 12, 0)
        lbl = QLabel("ACTIVITY")
        lbl.setStyleSheet("color: #282828; font-family: Courier New; font-size: 8px; "
                          "font-weight: bold; letter-spacing: 3px; border: none;")
        self._cnt = QLabel("")
        self._cnt.setStyleSheet("color: #303030; font-family: Courier New; font-size: 8px; border: none;")
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
        ts  = datetime.now().strftime("%H:%M:%S")
        row = LogRow(text, level, ts)
        self._rows.insertWidget(self._rows.count() - 1, row)
        self._entries.append(row)
        while len(self._entries) > MAX_LOG_ENTRIES:
            old = self._entries.pop(0)
            self._rows.removeWidget(old)
            old.deleteLater()
        self._cnt.setText(str(self._total))
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))


# ── Reveal button ──────────────────────────────────────────────
class RevealButton(QPushButton):
    def __init__(self, field: QLineEdit, parent=None):
        super().__init__("👁", parent)
        self._field = field
        self._shown = False
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton { background: transparent; border: none; color: #383838; font-size: 12px; }
            QPushButton:hover { color: #777777; }
        """)
        self.clicked.connect(self._toggle)

    def _toggle(self):
        self._shown = not self._shown
        self._field.setEchoMode(
            QLineEdit.EchoMode.Normal if self._shown else QLineEdit.EchoMode.Password
        )


# ── Settings dialog ────────────────────────────────────────────
class SettingsDialog(QDialog):
    _FIELD = f"""
        QLineEdit {{
            background: {C['bg3']}; border: 1px solid {C['border2']};
            border-radius: 8px; color: #dddddd;
            padding: 9px 38px 9px 12px;
            font-family: Courier New; font-size: 11px;
            selection-background-color: {C['green']};
        }}
        QLineEdit:focus {{ border-color: {C['green']}; }}
        QLineEdit:hover {{ border-color: {C['border3']}; }}
    """
    _FIELD_ERR = _FIELD.replace(f"border: 1px solid {C['border2']}", "border: 1px solid #7a2a2a")

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedWidth(400)
        self.setStyleSheet(f"QDialog {{ background: {C['bg']}; }}")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 26, 26, 26)
        lay.setSpacing(0)

        hdr = QLabel("SETTINGS")
        hdr.setStyleSheet(f"color: {C['text']}; font-family: Courier New; "
                          "font-size: 11px; font-weight: bold; letter-spacing: 5px;")
        lay.addWidget(hdr)
        lay.addSpacing(22)

        def section(txt):
            l = QLabel(txt)
            l.setStyleSheet("color: #383838; font-family: Courier New; font-size: 8px; letter-spacing: 2px;")
            lay.addWidget(l)
            lay.addSpacing(6)

        def field_row(ph, pwd=False):
            container = QWidget()
            container.setStyleSheet("background: transparent;")
            hl = QHBoxLayout(container)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(0)
            f = QLineEdit()
            f.setPlaceholderText(ph)
            f.setStyleSheet(self._FIELD)
            f.setMinimumHeight(40)
            hl.addWidget(f, 1)
            if pwd:
                rb = RevealButton(f)
                hl.addWidget(rb)
                hl.setAlignment(rb, Qt.AlignmentFlag.AlignVCenter)
            return container, f

        section("LAST.FM")
        w1, self.lfm_user = field_row("username")
        w2, self.lfm_key  = field_row("api key", pwd=True)
        lay.addWidget(w1)
        lay.addSpacing(8)
        lay.addWidget(w2)
        lay.addSpacing(20)

        section("SPOTIFY")
        w3, self.sp_id  = field_row("client id",     pwd=True)
        w4, self.sp_sec = field_row("client secret", pwd=True)
        lay.addWidget(w3)
        lay.addSpacing(8)
        lay.addWidget(w4)
        lay.addSpacing(8)

        hint = QLabel("A browser window will open to authorize Spotify on first connect.")
        hint.setStyleSheet("color: #2c2c2c; font-family: Courier New; font-size: 8px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addSpacing(22)

        self.lfm_user.setText(cfg.get("lastfm_user", ""))
        self.lfm_key.setText(cfg.get("lastfm_api_key", ""))
        self.sp_id.setText(cfg.get("spotify_client_id", ""))
        self.sp_sec.setText(cfg.get("spotify_client_secret", ""))

        btn_row = QHBoxLayout()
        cancel  = QPushButton("Cancel")
        save    = QPushButton("SAVE")
        for b in (cancel, save):
            b.setMinimumHeight(38)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: {C['bg3']}; color: {C['text_muted']}; border: 1px solid {C['border']};
                border-radius: 8px; font-family: Courier New; font-size: 9px; }}
            QPushButton:hover {{ color: #888888; border-color: {C['border3']}; }}
        """)
        save.setStyleSheet(f"""
            QPushButton {{ background: {C['green']}; color: #000000; border: none;
                border-radius: 8px; font-family: Courier New; font-size: 9px;
                font-weight: bold; letter-spacing: 2px; }}
            QPushButton:hover {{ background: {C['green_hi']}; }}
        """)
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._on_save)
        btn_row.addWidget(cancel)
        btn_row.addWidget(save)
        lay.addLayout(btn_row)

        self._fields = {
            "lastfm_user":           self.lfm_user,
            "lastfm_api_key":        self.lfm_key,
            "spotify_client_id":     self.sp_id,
            "spotify_client_secret": self.sp_sec,
        }

    def _on_save(self):
        any_empty = False
        for f in self._fields.values():
            if not f.text().strip():
                f.setStyleSheet(self._FIELD_ERR)
                any_empty = True
            else:
                f.setStyleSheet(self._FIELD)
        if not any_empty:
            self.accept()

    def get_config(self) -> dict:
        return {k: f.text().strip() for k, f in self._fields.items()}

    def is_valid(self) -> bool:
        return all(f.text().strip() for f in self._fields.values())


# ── Main window ────────────────────────────────────────────────
class LastSyncWindow(QMainWindow):
    BTN_R = 25

    def __init__(self):
        super().__init__()
        self._cfg         = load_config()
        self._sp          = None
        self._worker      = None
        self._auth_worker = None
        self._art_loader  = None
        self._syncing     = False
        self._track_start = 0.0
        self._duration_ms = 0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick_progress)

        self._init_fonts()
        self._init_ui()
        self._init_tray()

        if not self._config_is_complete():
            QTimer.singleShot(300, self._prompt_settings)

    def _init_fonts(self):
        fams        = QFontDatabase.families()
        self._serif = "Georgia"     if "Georgia"     in fams else "DejaVu Serif"
        self._mono  = "Courier New" if "Courier New" in fams else "DejaVu Sans Mono"

    def _f(self, family, size, bold=False):
        return QFont(family, size, QFont.Weight.Bold if bold else QFont.Weight.Normal)

    def _init_ui(self):
        self.setWindowTitle("LastSync")
        self.setFixedSize(440, 680)
        self.setStyleSheet(f"background-color: {C['bg']};")

        # Window icon
        px = QPixmap(64, 64)
        px.fill(Qt.GlobalColor.transparent)
        ip = QPainter(px)
        ip.setRenderHint(QPainter.RenderHint.Antialiasing)
        ip.setBrush(QBrush(QColor(29, 185, 84)))
        ip.setPen(Qt.PenStyle.NoPen)
        ip.drawRoundedRect(4, 4, 56, 56, 14, 14)
        ip.setPen(QPen(QColor(0, 0, 0)))
        ip.setFont(QFont(self._mono, 16, QFont.Weight.Bold))
        ip.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "LS")
        ip.end()
        self.setWindowIcon(QIcon(px))

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 24, 24, 18)
        root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(0)

        badge = QLabel("LS")
        badge.setFixedSize(32, 32)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(f"""
            background: {C['green']}; color: #000000; border-radius: 8px;
            font-family: Courier New; font-size: 10px; font-weight: bold;
        """)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.setContentsMargins(10, 0, 0, 0)
        app_lbl = QLabel("LASTSYNC")
        app_lbl.setFont(self._f(self._mono, 11, bold=True))
        app_lbl.setStyleSheet(f"color: {C['text']}; letter-spacing: 5px;")
        self.sub_label = QLabel(f"with  {self._cfg.get('lastfm_user', '—')}")
        self.sub_label.setFont(self._f(self._mono, 8))
        self.sub_label.setStyleSheet("color: #2a2a2a; letter-spacing: 1px;")
        title_col.addWidget(app_lbl)
        title_col.addWidget(self.sub_label)

        gear_btn = QPushButton("⚙")
        gear_btn.setFixedSize(30, 30)
        gear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gear_btn.setToolTip("Settings")
        gear_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: #404040; border: none; font-size: 14px; border-radius: 7px; }}
            QPushButton:hover {{ color: #888888; background: {C['bg3']}; }}
        """)
        gear_btn.clicked.connect(self._open_settings)

        self.pulse = PulseDot()

        hdr.addWidget(badge)
        hdr.addLayout(title_col, 1)
        hdr.addWidget(gear_btn)
        hdr.addSpacing(8)
        hdr.addWidget(self.pulse)
        hdr.setAlignment(self.pulse, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(hdr)

        root.addSpacing(20)

        # ── Now-playing card ─────────────────────────────────────
        card = QFrame()
        card.setObjectName("npCard")
        card.setStyleSheet(f"""
            QFrame#npCard {{
                background-color: {C['bg2']};
                border: 1px solid {C['border']};
                border-radius: 18px;
            }}
        """)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 18, 20, 16)
        cv.setSpacing(0)

        # Card header
        ch = QHBoxLayout()
        np_lbl = QLabel("NOW PLAYING")
        np_lbl.setFont(self._f(self._mono, 7, bold=True))
        np_lbl.setStyleSheet("color: #1e1e1e; letter-spacing: 4px;")
        self.live_badge = QLabel("● LIVE")
        self.live_badge.setFont(self._f(self._mono, 7, bold=True))
        self.live_badge.setStyleSheet(f"color: {C['green']}; letter-spacing: 2px;")
        self.live_badge.hide()
        ch.addWidget(np_lbl)
        ch.addStretch()
        ch.addWidget(self.live_badge)
        cv.addLayout(ch)

        cv.addSpacing(14)

        # Art + text row — horizontal, art on the left
        art_row = QHBoxLayout()
        art_row.setSpacing(14)
        art_row.setContentsMargins(0, 0, 0, 0)

        self.art_widget = ArtWidget()
        art_row.addWidget(self.art_widget, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        text_col.setContentsMargins(0, 0, 0, 0)

        self.track_label = QLabel("Start sync to listen along")
        self.track_label.setFont(self._f(self._serif, 14, bold=True))
        self.track_label.setStyleSheet(f"color: {C['text']};")
        self.track_label.setWordWrap(True)
        self.track_label.setMaximumWidth(300)
        self.track_label.setMaximumHeight(62)

        self.artist_label = QLabel("")
        self.artist_label.setFont(self._f(self._serif, 11))
        self.artist_label.setStyleSheet(f"color: {C['text_muted']};")
        self.artist_label.setWordWrap(True)
        self.artist_label.setMaximumWidth(300)
        self.artist_label.setMaximumHeight(28)

        # Small inline badge shown when track not on Spotify
        self.unavail_badge = QLabel("⚠  not on Spotify — playing fallback")
        self.unavail_badge.setFont(self._f(self._mono, 7))
        self.unavail_badge.setStyleSheet(f"""
            color: {C['warn']}; background: #1a1200;
            border: 1px solid #2e2000; border-radius: 4px;
            padding: 2px 7px;
        """)
        self.unavail_badge.hide()

        text_col.addWidget(self.track_label)
        text_col.addWidget(self.artist_label)
        text_col.addSpacing(5)
        text_col.addWidget(self.unavail_badge)
        text_col.addStretch()

        art_row.addLayout(text_col, 1)
        cv.addLayout(art_row)

        cv.addSpacing(16)

        self.progress_bar = ProgressBar()
        cv.addWidget(self.progress_bar)
        cv.addSpacing(6)

        time_row = QHBoxLayout()
        self.elapsed_lbl  = QLabel("")
        self.duration_lbl = QLabel("")
        for lbl in (self.elapsed_lbl, self.duration_lbl):
            lbl.setFont(self._f(self._mono, 8))
            lbl.setStyleSheet(f"color: {C['text_muted']};")
        time_row.addWidget(self.elapsed_lbl)
        time_row.addStretch()
        time_row.addWidget(self.duration_lbl)
        cv.addLayout(time_row)

        root.addWidget(card)
        root.addSpacing(10)

        # ── Status row ───────────────────────────────────────────
        sr = QHBoxLayout()
        self.status_label = QLabel("Ready")
        self.status_label.setFont(self._f(self._mono, 8))
        self.status_label.setStyleSheet(f"color: {C['text_ghost']}; letter-spacing: 1px;")

        self.poll_label = QLabel("")
        self.poll_label.setFont(self._f(self._mono, 8))
        self.poll_label.setStyleSheet(f"color: {C['border']}; font-family: Courier New;")
        self.poll_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.poll_label.setToolTip("Sync check frequency")

        sr.addWidget(self.status_label, 1)
        sr.addWidget(self.poll_label)
        root.addLayout(sr)

        root.addSpacing(8)

        # ── Log ──────────────────────────────────────────────────
        self.log_panel = LogPanel()
        root.addWidget(self.log_panel)

        root.addSpacing(14)

        # ── Sync button ──────────────────────────────────────────
        self.sync_btn = QPushButton("START SYNC")
        self.sync_btn.setFixedHeight(48)
        self.sync_btn.setFont(self._f(self._mono, 10, bold=True))
        self.sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_btn.setShortcut("Return")
        self._btn_start()
        self.sync_btn.clicked.connect(self.toggle_sync)
        root.addWidget(self.sync_btn)

        root.addSpacing(10)

        # ── Footer ───────────────────────────────────────────────
        self.footer_label = QLabel(self._footer_text())
        self.footer_label.setFont(self._f(self._mono, 7))
        self.footer_label.setStyleSheet("color: #1c1c1c; letter-spacing: 1px;")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.footer_label)

    def _footer_text(self):
        user = self._cfg.get("lastfm_user", "—")
        return f"polls {POLL_ACTIVE}s active · {POLL_IDLE}s idle  ·  last.fm/{user}"

    # ── Tray ──
    def _init_tray(self):
        self._tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{ background: {C['bg3']}; color: #aaaaaa; font-family: Courier New;
                font-size: 10px; border: 1px solid {C['border']}; border-radius: 6px; }}
            QMenu::item:selected {{ background: {C['border']}; }}
        """)
        menu.addAction("Show LastSync").triggered.connect(self._show_window)
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(QApplication.quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda r: self._show_window()
            if r == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._tray.setToolTip("LastSync · idle")
        self._tray.show()

    def _show_window(self):
        self.showNormal()
        self.activateWindow()

    # ── Button styles ──
    def _btn_start(self):
        r = self.BTN_R
        self.sync_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['green']}; color: #000000; border: none;
                border-radius: {r}px; letter-spacing: 4px;
            }}
            QPushButton:hover   {{ background: {C['green_hi']}; }}
            QPushButton:pressed {{ background: {C['green_lo']}; }}
            QPushButton:disabled {{ background: {C['green_dim']}; color: {C['green_text']}; }}
        """)

    def _btn_stop(self):
        r = self.BTN_R
        self.sync_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['bg3']}; color: {C['text_dim']};
                border: 1px solid #222222; border-radius: {r}px; letter-spacing: 4px;
            }}
            QPushButton:hover   {{ background: #1c1c1c; border-color: {C['border3']}; }}
            QPushButton:pressed {{ background: {C['bg']}; }}
        """)

    def _btn_loading(self):
        r = self.BTN_R
        self.sync_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C['green_dim']}; color: {C['green_text']}; border: none;
                border-radius: {r}px; letter-spacing: 4px;
            }}
        """)

    # ── Config ──
    def _config_is_complete(self):
        return all(self._cfg.get(k, "").strip() for k in _DEFAULT_CONFIG)

    def _prompt_settings(self):
        QMessageBox.information(self, "Welcome to LastSync",
                                "Fill in your credentials in Settings to get started.")
        self._open_settings()

    def _open_settings(self):
        dlg = SettingsDialog(self._cfg, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
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
        self.sub_label.setText(f"with  {self._cfg.get('lastfm_user', '—')}")
        self.footer_label.setText(self._footer_text())

    # ── Progress ──
    def _tick_progress(self):
        if not self._track_start or not self._duration_ms:
            return
        elapsed_ms = min(int((time.time() - self._track_start) * 1000), self._duration_ms)
        self.progress_bar.set_progress(elapsed_ms / self._duration_ms)
        self.elapsed_lbl.setText(self._ms(elapsed_ms))

    @staticmethod
    def _ms(ms: int) -> str:
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"

    # ── Sync ──
    def toggle_sync(self):
        self.stop_sync() if self._syncing else self.start_sync()

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
        self.track_label.setText("Waiting to sync…")
        self.artist_label.setText("")
        self.unavail_badge.hide()

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
        self.set_status("Connected — polling…")
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
        self.track_label.setText("Start sync to listen along")
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
        self._clear_card("Start sync to listen along")
        self.poll_label.setText("")
        self.set_status("Stopped")
        self._tray.setToolTip("LastSync · idle")
        self.log_panel.add_entry("Sync stopped", "info")

    def _clear_card(self, placeholder=""):
        self.track_label.setText(placeholder)
        self.track_label.setToolTip("")
        self.artist_label.setText("")
        self.artist_label.setToolTip("")
        self.art_widget.clear()
        self.unavail_badge.hide()
        self.progress_bar.set_progress(0)
        self.elapsed_lbl.setText("")
        self.duration_lbl.setText("")
        self._track_start = 0.0
        self._duration_ms = 0

    # ── Handlers ──
    def _on_track(self, track: str, artist: str, pos_ms: int, dur_ms: int, art_url: str):
        display = track if len(track) <= 36 else track[:34] + "…"
        self.track_label.setText(display)
        self.track_label.setToolTip(track if len(track) > 36 else "")
        self.artist_label.setText(artist)
        self.artist_label.setToolTip(artist if len(artist) > 36 else "")
        self.unavail_badge.hide()
        self.set_status("Synced ✓")

        self._track_start = time.time() - (pos_ms / 1000)
        self._duration_ms = dur_ms
        self.progress_bar.set_progress(pos_ms / dur_ms if dur_ms else 0)
        self.elapsed_lbl.setText(self._ms(pos_ms))
        self.duration_lbl.setText(self._ms(dur_ms) if dur_ms else "")

        self.log_panel.add_entry(f"{track} — {artist}", "track")
        self._tray.setToolTip(f"LastSync · {track} — {artist}")

        self.art_widget.clear()
        if art_url:
            self._art_loader = ArtLoader(art_url)
            self._art_loader.art_ready.connect(self.art_widget.set_pixmap)
            self._art_loader.start()

    def _on_status(self, msg: str, level: str):
        if level == "auth":
            self.set_status(f"⚠ {msg}")
            self.log_panel.add_entry(msg, "auth")
            self.stop_sync()
        elif level == "warn":
            raw = msg.replace("Not on Spotify: ", "", 1)
            display = raw if len(raw) <= 36 else raw[:34] + "…"
            self.track_label.setText(display)
            self.track_label.setToolTip(raw)
            self.artist_label.setText("")
            self.art_widget.clear()
            self.unavail_badge.show()
            self.progress_bar.set_progress(0)
            self.elapsed_lbl.setText("")
            self.duration_lbl.setText("")
            self._track_start = 0.0
            self._duration_ms = 0
            self.set_status("⚠ not on Spotify — playing fallback")
            self.log_panel.add_entry(msg, "warn")
            self.log_panel.add_entry("↳ playing fallback track", "fallback")
        elif level == "error":
            self.log_panel.add_entry(msg, "error")
            if "Synced" not in self.status_label.text():
                self.set_status(f"⚠ {msg}")
        else:
            self.set_status(msg)

    def _on_idle(self):
        self._clear_card("Nothing playing")
        user = self._cfg.get("lastfm_user", "them")
        self.set_status(f"{user} isn't playing anything")
        self._tray.setToolTip("LastSync · idle")

    def _on_poll_interval(self, interval: int):
        self.poll_label.setText(f"↻ {interval}s")
        color = "#1a3d22" if interval == POLL_ACTIVE else C["border"]
        self.poll_label.setStyleSheet(
            f"color: {color}; font-family: Courier New; font-size: 8px;"
        )

    def set_status(self, msg: str):
        self.status_label.setText(msg)

    def closeEvent(self, event):
        self._tick_timer.stop()
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
