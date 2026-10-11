"""Push DLNA direct vers le MediaRenderer du R-N301 (port 8080).

Réplique fidèle du module `localfilecontroller` de l'app officielle :
un mini serveur HTTP (stdlib) sert le fichier audio, et un client SOAP
AVTransport pousse l'URL vers l'ampli qui vient la chercher lui-même.

Validé en live le 2025-10-10 : WAV/LPCM 44.1 kHz stéréo → PLAYING,
l'ampli fait `GET /…` sur notre serveur (logs 200 + Range au seek).
Le renderer accepte officiellement (GetProtocolInfo Sink) :
LPCM (audio/L16 44.1/48k), MP3 (audio/mpeg), WMA (audio/x-ms-wma).
Les autres formats (FLAC, MP4…) peuvent passer mais ne sont pas déclarés ;
en cas d'échec, transcoder en WAV via ffmpeg.

Usage:
    from rn301.push import PushServer
    push = PushServer(amp="192.168.1.91")     # serveur fichier auto sur 0.0.0.0:9000
    push.start()
    push.push_file("morceau.wav", title="Mon morceau", artist="Moi")
    print(push.state())                       # {'state': 'PLAYING', ...}
    push.pause(); push.play(); push.stop()
    push.push_url("http://nas.local/radio.mp3", title="Webradio")  # URL directe, pas de serveur local

100 % stdlib.
"""
from __future__ import annotations

import os
import re
import socket
import threading
import urllib.error
import urllib.request
import xml.sax.saxutils as sx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ._locks import amp_lock
from .gena import GenaClient

RENDERER_PORT = 8080
AVT_NS = "urn:schemas-upnp-org:service:AVTransport:1"

# DLNA.ORG_OP déclaré par format — recopié de l'app officielle (p.java) :
# 00 = pas de seek (le renderer redémarre de 0 après pause), 01 = seekable.
# Déclarer OP=01 sur un format que le renderer ne sait pas seeker → ERROR_OCCURRED.
DLNA_OP_BY_MIME = {
    "audio/3gpp": "01",
    "audio/mp4": "01",
    "audio/mpeg": "00",
    "audio/wav": "00",
    "audio/flac": "00",
    "audio/x-ms-wma": "00",
    "audio/ogg": "00",
}

MIME_BY_EXT = {
    ".wav": "audio/wav", ".wave": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac", ".fla": "audio/flac",
    ".wma": "audio/x-ms-wma", ".asf": "audio/x-ms-wma",
    ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".aac": "audio/mp4",
    ".3gp": "audio/3gpp", ".3g2": "audio/3gpp",
    ".ogg": "audio/ogg", ".opus": "audio/ogg",
}


class PushError(Exception):
    """Erreur SOAP/HTTP du renderer."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


# ---------------------------------------------------------------------------
# serveur de fichiers (équivalent de h.java + i.java)
# ---------------------------------------------------------------------------

class _FileHandler(BaseHTTPRequestHandler):
    server_version = "RN301Push/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _serve(self, head_only: bool = False):
        # l'état servi (fichier, pochette) vit sur le FileServer (wrapper),
        # pas sur le ThreadingHTTPServer — d'où l'accès via .owner
        owner = getattr(self.server, "owner", None)
        if owner is None:
            self.send_error(404)
            return
        # /albumart/… → pochette intégrée extraite du fichier poussé
        if self.path.startswith("/albumart"):
            data = owner.cover_data if owner else None
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", owner.cover_mime or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
            return
        # chemin servi = /<n'importe quoi> → le fichier enregistré sur le serveur
        path: str | None = owner.file_path if owner else None
        if not path or not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        ctype = MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "application/octet-stream")

        rng = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        start, end = 0, size - 1
        partial = False
        if m and (m.group(1) or m.group(2)):
            partial = True
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            if not m.group(1) and m.group(2):          # suffix range
                start, end = max(size - int(m.group(2)), 0), size - 1
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("TransferMode.DLNA.ORG", "Streaming")
        length = end - start + 1
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        if head_only:
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def do_GET(self):
        self._serve()

    def do_HEAD(self):
        self._serve(head_only=True)


class FileServer:
    """Sert UN fichier à la fois sur un port du LAN (comme le téléphone de l'app)."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9000):
        self.host, self.port = host, port
        self.file_path: str | None = None
        self.cover_data: bytes | None = None
        self.cover_mime: str = "image/jpeg"
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if self._httpd:
            return
        self._httpd = ThreadingHTTPServer((self.host, self.port), _FileHandler)
        self._httpd.owner = self          # le handler lit l'état via .owner  # pyright: ignore[reportAttributeAccessIssue]
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def set_file(self, path: str):
        self.file_path = os.path.abspath(path)

    def set_cover(self, data: bytes | None, mime: str = "image/jpeg"):
        self.cover_data, self.cover_mime = data, mime

    def local_ip(self, toward: str) -> str:
        """IP de la machine vue depuis l'ampli (évite 127.0.0.1 et le multi-homing)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((toward, 80))
            return s.getsockname()[0]
        finally:
            s.close()


# ---------------------------------------------------------------------------
# pochette intégrée (ID3v2 APIC / FLAC PICTURE) — stdlib only
# ---------------------------------------------------------------------------

def _id3_frames(data: bytes, synchsafe: bool) -> dict:
    """Parse les frames texte ID3v2 (TIT2/TPE1/TALB…)."""
    out, i = {}, 0
    while i + 10 <= len(data):
        fid = data[i:i + 4]
        if not fid[:1].isalpha():
            break
        b = data[i + 4:i + 8]
        size = ((b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]) if synchsafe \
            else int.from_bytes(b, "big")
        if size <= 0:
            break
        if fid[:1] == b"T" and fid != b"TXXX":
            fr = data[i + 10:i + 10 + size]
            enc, txt = fr[0], fr[1:]
            codec = {0: "latin1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin1")
            try:
                out[fid.decode()] = txt.decode(codec).rstrip("\x00").strip()
            except (UnicodeDecodeError, LookupError):
                pass
        i += 10 + size
    return out


def extract_tags(path: str) -> dict:
    """Lit title/artist/album des tags intégrés (ID3v2, FLAC/Vorbis, MP4 atom)."""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if head[:3] == b"ID3" and head[3] >= 3:
                tag_size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
                fr = _id3_frames(f.read(tag_size), head[3] == 4)
                return {"title": fr.get("TIT2", ""), "artist": fr.get("TPE1", ""),
                        "album": fr.get("TALB", "")}
            if head[:4] == b"fLaC":
                f.seek(0)
                while True:
                    hdr = f.read(4)
                    if len(hdr) < 4:
                        break
                    btype, blen = hdr[0] & 0x7F, int.from_bytes(hdr[1:4], "big")
                    block = f.read(blen)
                    if btype == 4:                               # VORBIS_COMMENT
                        j = 4                                    # vendor string
                        n = int.from_bytes(block[j:j + 4], "little"); j += 4 + n
                        cnt = int.from_bytes(block[j:j + 4], "little"); j += 4
                        out = {}
                        for _ in range(cnt):
                            n = int.from_bytes(block[j:j + 4], "little"); j += 4
                            kv = block[j:j + n].decode("utf-8", "replace"); j += n
                            if "=" in kv:
                                k, v = kv.split("=", 1)
                                out[k.upper()] = v
                        return {"title": out.get("TITLE", ""), "artist": out.get("ARTIST", ""),
                                "album": out.get("ALBUM", "")}
                    if hdr[0] & 0x80 or blen == 0:
                        break
            if head[4:8] == b"ftyp":                             # MP4/M4A
                f.seek(0)
                data = f.read(1024 * 1024)                       # tags en début de fichier
                out = {}
                for atom, key in ((b"\xa9nam", "title"), (b"\xa9ART", "artist"), (b"\xa9alb", "album")):
                    p = data.find(atom)
                    if p > 0 and p + 16 < len(data):
                        n = int.from_bytes(data[p + 12:p + 14], "big")
                        out[key] = data[p + 16:p + 16 + n].decode("utf-8", "replace")
                return out
    except Exception:
        pass
    return {}


def extract_cover(path: str) -> tuple[bytes, str] | None:
    """Extrait la pochette intégrée d'un fichier audio → (octets, mime) ou None."""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if head[:3] == b"ID3" and head[3] >= 3:
                tag_size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
                data = f.read(tag_size)
                i = 0
                while i + 10 <= len(data):
                    fid = data[i:i + 4]
                    if not fid[:1].isalpha():
                        break                                    # padding
                    b = data[i + 4:i + 8]
                    fsize = ((b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]) if head[3] == 4 \
                        else int.from_bytes(b, "big")
                    if fsize <= 0:
                        break
                    if fid == b"APIC":
                        fr = data[i + 10:i + 10 + fsize]
                        mime_end = fr.index(b"\x00", 1)
                        mime = fr[1:mime_end].decode("latin1") or "image/jpeg"
                        if mime in ("image/", ""):
                            mime = "image/png" if b"\x89PNG" in fr[:mime_end + 20] else "image/jpeg"
                        desc_end = fr.index(b"\x00", mime_end + 2)
                        return fr[desc_end + 1:], mime
                    i += 10 + fsize
            elif head[:4] == b"fLaC":
                f.seek(0)
                while True:
                    hdr = f.read(4)
                    if len(hdr) < 4:
                        break
                    btype, blen = hdr[0] & 0x7F, int.from_bytes(hdr[1:4], "big")
                    block = f.read(blen)
                    if btype == 6:                               # PICTURE
                        j = 4                                    # picture type
                        n = int.from_bytes(block[j:j + 4], "big"); j += 4
                        mime = block[j:j + n].decode() or "image/jpeg"; j += n
                        n = int.from_bytes(block[j:j + 4], "big"); j += 4 + n   # description
                        j += 16                                  # w/h/depth/colors
                        n = int.from_bytes(block[j:j + 4], "big"); j += 4
                        return block[j:j + n], mime
                    if hdr[0] & 0x80 or blen == 0:
                        break
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# métadonnées DIDL-Lite (équivalent de p.java)
# ---------------------------------------------------------------------------

def didl_metadata(uri: str, mime: str, title: str = "", artist: str = "",
                  album: str = "", art_url: str = "", size: int | None = None,
                  op: str | None = None) -> str:
    parts = [
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"',
        ' xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"',
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"',
        ' xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">',
        '<item id="1" parentID="0" restricted="1">',
    ]
    if title:
        parts.append(f"<dc:title>{sx.escape(title)}</dc:title>")
    if artist:
        parts.append(f"<upnp:artist>{sx.escape(artist)}</upnp:artist>")
    if album:
        parts.append(f"<upnp:album>{sx.escape(album)}</upnp:album>")
    parts.append("<upnp:class>object.item.audioItem.musicTrack</upnp:class>")
    op = op or DLNA_OP_BY_MIME.get(mime, "01")
    attrs = (f'protocolInfo="http-get:*:{mime}:DLNA.ORG_OP={op};'
             'DLNA.ORG_FLAGS=01700000000000000000000000000000"')
    if size:
        attrs += f' size="{size}"'
    parts.append(f"<res {attrs}>{sx.escape(uri)}</res>")
    if art_url:
        parts.append(f'<upnp:albumArtURI>{sx.escape(art_url)}</upnp:albumArtURI>')
    parts.append("</item></DIDL-Lite>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# client SOAP AVTransport (équivalent de p.java côté ampli)
# ---------------------------------------------------------------------------

SOAP_ENVELOPE = ('<?xml version="1.0" encoding="utf-8"?>'
                 '<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
                 'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                 '<s:Body>{body}</s:Body></s:Envelope>')


class Renderer:
    """Client UPnP AVTransport du MediaRenderer (:8080/AVTransport/ctrl)."""

    def __init__(self, amp: str, port: int = RENDERER_PORT, timeout: float = 10.0):
        self.ctrl = f"http://{amp}:{port}/AVTransport/ctrl"
        self.timeout = timeout
        # même verrou que le client YNC du même ampli : une seule requête à la
        # fois, tous canaux confondus (quirk firmware, voir _locks.py)
        self._lock = amp_lock(amp)

    def _soap(self, action: str, body: str) -> str:
        req = urllib.request.Request(
            self.ctrl,
            data=SOAP_ENVELOPE.format(body=body).encode("utf-8"),
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPAction": f'"{AVT_NS}#{action}"'},
        )
        try:
            with self._lock, urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            desc = re.search(r"<errorDescription>([^<]*)</errorDescription>", detail)
            raise PushError(f"SOAP {action} refusé (HTTP {e.code}): "
                            f"{desc.group(1) if desc else 'voir détail'}", detail) from None
        except OSError as e:
            raise PushError(f"renderer injoignable ({self.ctrl}): {e}") from None

    def set_uri(self, uri: str, metadata: str = "NOT_IMPLEMENTED") -> None:
        self._soap("SetAVTransportURI",
                   f'<u:SetAVTransportURI xmlns:u="{AVT_NS}"><InstanceID>0</InstanceID>'
                   f'<CurrentURI>{sx.escape(uri)}</CurrentURI>'
                   f'<CurrentURIMetaData>{sx.escape(metadata)}</CurrentURIMetaData>'
                   f'</u:SetAVTransportURI>')

    def play(self) -> None:
        self._soap("Play", f'<u:Play xmlns:u="{AVT_NS}">'
                   '<InstanceID>0</InstanceID><Speed>1</Speed></u:Play>')

    def pause(self) -> None:
        self._soap("Pause", f'<u:Pause xmlns:u="{AVT_NS}"><InstanceID>0</InstanceID></u:Pause>')

    def stop(self) -> None:
        self._soap("Stop", f'<u:Stop xmlns:u="{AVT_NS}"><InstanceID>0</InstanceID></u:Stop>')

    def transport_info(self) -> dict:
        resp = self._soap("GetTransportInfo",
                          f'<u:GetTransportInfo xmlns:u="{AVT_NS}">'
                          '<InstanceID>0</InstanceID></u:GetTransportInfo>')
        get = lambda tag: (re.search(rf"<{tag}>([^<]*)</{tag}>", resp) or [None, None])[1]
        return {"state": get("CurrentTransportState"),
                "status": get("CurrentTransportStatus"),
                "speed": get("CurrentSpeed")}

    def position_info(self) -> dict:
        resp = self._soap("GetPositionInfo",
                          f'<u:GetPositionInfo xmlns:u="{AVT_NS}">'
                          '<InstanceID>0</InstanceID></u:GetPositionInfo>')
        get = lambda tag: (re.search(rf"<{tag}>([^<]*)</{tag}>", resp) or [None, None])[1]
        return {"uri": get("TrackURI"), "rel_time": get("RelTime"),
                "track_duration": get("TrackDuration")}

    def seek(self, target: str) -> None:
        """target = 'HH:MM:SS'."""
        self._soap("Seek", f'<u:Seek xmlns:u="{AVT_NS}"><InstanceID>0</InstanceID>'
                   f'<Unit>REL_TIME</Unit><Target>{sx.escape(target)}</Target></u:Seek>')


# ---------------------------------------------------------------------------
# façade : serveur de fichiers + renderer
# ---------------------------------------------------------------------------

class PushServer:
    """Pousse des fichiers locaux (ou des URLs) vers le R-N301.

    push_file()  : sert le fichier via le serveur HTTP intégré puis SetAVTransportURI+Play
    push_url()   : pousse une URL déjà servie sur le LAN (NAS, webradio MP3…)

    Événements de transport (GENA, optionnels et paresseux — rien n'est abonné
    tant qu'on ne les utilise pas, aucun comportement caché) :
        on_transport(cb) : cb(state, changed) à chaque NOTIFY du renderer
        wait_stopped()   : bloque jusqu'à la fin de la piste (scripts synchrones)
    Chaque push_file() reste indépendant : la lib ne gère AUCUNE file d'attente,
    c'est le client qui décide quoi pousser ensuite.
    """

    def __init__(self, amp: str, serve_host: str = "0.0.0.0", serve_port: int = 9000,
                 renderer_port: int = RENDERER_PORT):
        self.amp = amp
        self.renderer_port = renderer_port
        self.server = FileServer(serve_host, serve_port)
        self.renderer = Renderer(amp, renderer_port)
        self.last_meta: dict = {}
        self._gena: GenaClient | None = None

    # --- cycle de vie
    def start(self):
        self.server.start()

    def stop_server(self):
        self.server.stop()

    def close(self):
        """Libère tout : désabonnement GENA + arrêt du serveur de fichiers."""
        if self._gena is not None:
            self._gena.close()
            self._gena = None
        self.server.stop()

    # --- événements de transport (GENA) ---------------------------------
    # Optionnels, paresseux, passifs : la lib notifie, le client décide.

    _PLAYING = ("PLAYING", "TRANSITIONING", "PAUSED_PLAYBACK")
    _ENDED = ("STOPPED", "NO_MEDIA_PRESENT")

    def _ensure_gena(self) -> GenaClient:
        if self._gena is None:
            self._gena = GenaClient(self.amp, port=self.renderer_port)
        if not self._gena.is_subscribed("AVTransport"):
            self._gena.subscribe("AVTransport")
        return self._gena

    def on_transport(self, callback) -> None:
        """Abonne un callback aux events du transport push : callback(state, changed).

        state   = TransportState courant ('PLAYING', 'STOPPED', …) ou None
        changed = le delta du NOTIFY, ex {'TransportState': {'val': 'STOPPED'}}

        Le callback tourne dans le thread du serveur GENA : ne pas y bloquer.
        Rappel = remplace le callback précédent (un seul abonné AVTransport).
        """
        gena = self._ensure_gena()

        def forward(_service: str, changed: dict) -> None:
            state = (gena.var("AVTransport", "TransportState") or {}).get("val")
            callback(state, changed)

        gena.subscribe("AVTransport", forward)

    def transport_state(self) -> str | None:
        """État du transport vu par GENA (None si pas (encore) notifié — ne
        coûte aucune requête ; pour l'état renderer direct : state())."""
        if self._gena is None:
            return None
        return (self._gena.var("AVTransport", "TransportState") or {}).get("val")

    def wait_stopped(self, timeout: float | None = None, arm_timeout: float = 10.0) -> bool:
        """Bloque jusqu'à la fin de la piste poussée (event GENA, zéro polling).

        Deux phases, pour éviter la course « vieux STOPPED de la piste d'avant » :
        1. «armement» : attend que le transport entre en lecture (PLAYING/
           TRANSITIONING/PAUSED_PLAYBACK), au plus arm_timeout secondes ;
        2. attend STOPPED/NO_MEDIA_PRESENT, au plus timeout secondes.

        Renvoie True si la fin a été observée, False si timeout expiré.
        push_file(play=False) ne jouant jamais : phase 1 épuise arm_timeout
        puis renvoie True (état déjà STOPPED) — utiliser play=True en pratique.
        """
        gena = self._ensure_gena()
        playing = lambda st: (st.get("TransportState") or {}).get("val") in self._PLAYING
        ended = lambda st: (st.get("TransportState") or {}).get("val") in self._ENDED
        gena.wait_for("AVTransport", playing, timeout=arm_timeout)
        return gena.wait_for("AVTransport", ended, timeout=timeout)

    # --- push
    def push_file(self, path: str, title: str = "", artist: str = "", album: str = "",
                  play: bool = True) -> dict:
        if not os.path.isfile(path):
            raise PushError(f"fichier introuvable: {path}")
        self.start()
        self.server.set_file(path)
        cover = extract_cover(path)
        self.server.set_cover(cover[0] if cover else None, cover[1] if cover else "image/jpeg")
        ip = self.server.local_ip(self.amp)
        uri = f"http://{ip}:{self.server.port}/audio/1/"
        art_url = f"http://{ip}:{self.server.port}/albumart/1" if cover else ""
        mime = MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "audio/wav")
        if not (title and artist and album):
            tags = extract_tags(path)
            title = title or tags.get("title", "")
            artist = artist or tags.get("artist", "")
            album = album or tags.get("album", "")
        title = title or os.path.splitext(os.path.basename(path))[0]
        meta = didl_metadata(uri, mime, title=title, artist=artist, album=album,
                             art_url=art_url, size=os.path.getsize(path))
        self.last_meta = {"title": title, "artist": artist, "album": album, "cover": bool(cover)}
        self.renderer.set_uri(uri, meta)
        if play:
            self.renderer.play()
        return {"uri": uri, "file": path, "cover": bool(cover),
                "transport": self.renderer.transport_info()}

    def push_url(self, url: str, title: str = "", artist: str = "", album: str = "",
                 mime: str = "audio/mpeg", play: bool = True) -> dict:
        title = title or url.rsplit("/", 1)[-1]
        meta = didl_metadata(url, mime, title=title, artist=artist, album=album)
        self.last_meta = {"title": title, "artist": artist, "album": album, "cover": False}
        self.renderer.set_uri(url, meta)
        if play:
            self.renderer.play()
        return {"uri": url, "transport": self.renderer.transport_info()}

    # --- transport
    def play(self):
        self.renderer.play()

    def pause(self):
        self.renderer.pause()

    def stop(self):
        self.renderer.stop()

    def seek(self, target: str):
        self.renderer.seek(target)

    def state(self) -> dict:
        info = self.renderer.transport_info()
        try:
            info.update(self.renderer.position_info())
        except PushError:
            pass
        return info
