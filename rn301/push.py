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
import urllib.request
import urllib.error
import xml.sax.saxutils as sx
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
        # chemin servi = /<n'importe quoi> → le fichier enregistré sur le serveur
        path: str = getattr(self.server, "push_file_path", None)  # type: ignore[attr-defined]
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
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if self._httpd:
            return
        self._httpd = ThreadingHTTPServer((self.host, self.port), _FileHandler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def set_file(self, path: str):
        assert self._httpd is not None, "serveur non démarré"
        self._httpd.push_file_path = os.path.abspath(path)  # type: ignore[attr-defined]

    def local_ip(self, toward: str) -> str:
        """IP de la machine vue depuis l'ampli (évite 127.0.0.1 et le multi-homing)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((toward, 80))
            return s.getsockname()[0]
        finally:
            s.close()


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

    def _soap(self, action: str, body: str) -> str:
        req = urllib.request.Request(
            self.ctrl,
            data=SOAP_ENVELOPE.format(body=body).encode("utf-8"),
            headers={"Content-Type": 'text/xml; charset="utf-8"',
                     "SOAPAction": f'"{AVT_NS}#{action}"'},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
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
    """

    def __init__(self, amp: str, serve_host: str = "0.0.0.0", serve_port: int = 9000,
                 renderer_port: int = RENDERER_PORT):
        self.amp = amp
        self.server = FileServer(serve_host, serve_port)
        self.renderer = Renderer(amp, renderer_port)

    # --- cycle de vie
    def start(self):
        self.server.start()

    def stop_server(self):
        self.server.stop()

    # --- push
    def push_file(self, path: str, title: str = "", artist: str = "", album: str = "",
                  play: bool = True) -> dict:
        if not os.path.isfile(path):
            raise PushError(f"fichier introuvable: {path}")
        self.start()
        self.server.set_file(path)
        ip = self.server.local_ip(self.amp)
        uri = f"http://{ip}:{self.server.port}/audio/1/"
        mime = MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "audio/wav")
        meta = didl_metadata(uri, mime, title=title or os.path.basename(path),
                             artist=artist, album=album, size=os.path.getsize(path))
        self.renderer.set_uri(uri, meta)
        if play:
            self.renderer.play()
        return {"uri": uri, "file": path, "transport": self.renderer.transport_info()}

    def push_url(self, url: str, title: str = "", artist: str = "", album: str = "",
                 mime: str = "audio/mpeg", play: bool = True) -> dict:
        meta = didl_metadata(url, mime, title=title or url.rsplit("/", 1)[-1],
                             artist=artist, album=album)
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
