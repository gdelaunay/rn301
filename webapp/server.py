"""Serveur web de contrôle pour le Yamaha R-N301.

100 % stdlib. API JSON + page de contrôle intégrée.
Volume/mute en push instantané via GENA → SSE (`/api/events`) ; le reste de
l'état est pollé toutes les 4 s (GENA ne notifie ni Power ni Input_Sel).

Usage:
    .venv/Scripts/python -m webapp.server [--amp 192.168.1.91] [--port 8080]
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from rn301 import RN301, YNCError, GenaClient
from rn301.push import PushServer, PushError

amp: RN301      # initialisé dans main()
push: PushServer | None = None
gena: GenaClient | None = None
music_dir: str = "music"


class EventHub:
    """Hub SSE : les callbacks GENA publient, chaque navigateur connecté reçoit."""

    def __init__(self):
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def publish(self, data: dict) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass                       # client trop lent : tant pis, le polling rattrape

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


hub = EventHub()


def on_gena_event(service: str, changed: dict) -> None:
    """Callback GENA (thread du serveur de callback) → hub SSE.

    RenderingControl notifie Volume/Mute (échelle identique au YNC, mesuré) ;
    attributs dans l'ordre channel-avant-val déjà gérés par GenaClient.
    """
    out: dict = {}
    v = changed.get("Volume")
    if v and v.get("channel") == "Master" and v.get("val", "").isdigit():
        out["volume"] = int(v["val"])
    m = changed.get("Mute")
    if m and m.get("channel") == "Master":
        out["mute"] = "On" if m.get("val") == "1" else "Off"
    if out:
        hub.publish(out)

# L'UI vit dans un vrai fichier statique (webapp/static/index.html), charge
# au demarrage. On garde la variable INDEX_HTML : le reste du serveur est
# inchange (routeur, Cache-Control, etc.).
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _load_index() -> str:
    with open(os.path.join(_STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


INDEX_HTML = _load_index()


class Handler(BaseHTTPRequestHandler):
    server_version = "RN301Web/1.0"
    protocol_version = "HTTP/1.1"      # keep-alive : nécessaire au flux SSE

    def log_message(self, fmt, *args):
        pass  # silencieux

    # ---------------------------------------------------------------- helpers

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    _ASSET_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
                    ".otf": "font/otf", ".css": "text/css; charset=utf-8",
                    ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                    ".png": "image/png", ".json": "application/json"}

    def serve_asset(self, rel: str):
        """Sert un fichier statique de webapp/static/assets/ (fonts, etc.).
        basename() = anti path-traversal ; extension en liste blanche ; cache long
        (fichiers immuables, l'UI ne change de toute façon pas de nom)."""
        name = os.path.basename(rel)
        ctype = self._ASSET_TYPES.get(os.path.splitext(name)[1].lower())
        fp = os.path.join(_STATIC_DIR, "assets", name)
        if not ctype or not os.path.isfile(fp):
            return self._json({"error": "not found"}, 404)
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def full_state(self) -> dict:
        """Agrège l'état ampli pour l'UI (plusieurs GET, un par chemin)."""
        try:
            st = amp.basic_status()
        except YNCError as e:
            return {"error": str(e)}
        out = dict(st)
        try:
            cfg = amp.system_config()
            out["model"], out["version"] = cfg["model"], cfg["version"]
        except YNCError:
            pass
        # inputs (cache : la liste ne change pas)
        if not getattr(amp, "_inputs_cache", None):
            try:
                amp._inputs_cache = amp.list_inputs()
            except YNCError:
                amp._inputs_cache = []
        out["inputs"] = amp._inputs_cache
        # infos source réseau / tuner (Input_Sel renvoie 'NET RADIO', 'TUNER'... en majuscules)
        net_sources = ("NET RADIO", "SERVER", "SPOTIFY", "AIRPLAY", "TUNER")
        if (out.get("input") or "").upper() in net_sources:
            try:
                out["play_info"] = amp.play_info(out["input"])
            except YNCError:
                out["play_info"] = None
            if out["input"].upper() in ("SERVER", "NET RADIO"):
                try:
                    out["list_info"] = amp.list_info(out["input"])
                except YNCError:
                    out["list_info"] = None
        # réglages système (Network_Standby/Name sous System/Misc/Network sur ce firmware)
        for key, fn in (("net_standby", amp.get_network_standby),
                        ("net_name", amp.get_network_name)):
            try:
                out[key] = fn()
            except YNCError:
                out[key] = None
        # état du renderer DLNA (push)
        if push is not None:
            try:
                out["push_state"] = push.state()
                out["push_meta"] = push.last_meta
            except PushError:
                out["push_state"] = None
        return out

    # ---------------------------------------------------------------- routes

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
        elif path.startswith("/assets/"):
            self.serve_asset(path[len("/assets/"):])
        elif path == "/api/state":
            self._json({"state": self.full_state()})
        elif path == "/api/basic":
            # Amorçage UI : l'état minimal en 1 à 2 GET YNC (basic_status +
            # liste des sources, mise en cache) au lieu des 6-8 GET séquentiels
            # de /api/state → power/volume/mute/sources affichés en ~200 ms.
            # Les cartes riches (now playing, tuner, liste, push) attendent
            # /api/state : elles dépendent de play_info/list_info.
            try:
                st = amp.basic_status()
            except YNCError as e:
                self._json({"error": str(e)}, 502)
                return
            if not getattr(amp, "_inputs_cache", None):
                try:
                    amp._inputs_cache = amp.list_inputs()
                except YNCError:
                    amp._inputs_cache = []
            st["inputs"] = amp._inputs_cache
            st["partial"] = True
            self._json({"state": st})
        elif path == "/api/now":
            # Sondage léger « lecture en cours » : 2 GET YNC (Play_Info +
            # List_Info) au lieu des 6-8 de /api/state. C'est ce qui permet à
            # l'UI de faire suivre la carte Lecture et le marqueur « en lecture »
            # du navigateur (fin de morceau, next/prev) toutes les ~2,5 s sans
            # saturer le bus YNC. GENA ne sert à rien ici : le renderer ne
            # notifie que le push DLNA, jamais la lecture réseau SERVER
            # (mesuré — docs/DLNA_PUSH.md §5).
            # La source est passée par le client (il la connaît) : pas de GET
            # Input_Sel supplémentaire. Réponse = mêmes clés que full_state().
            src = (parse_qs(urlparse(self.path).query).get("source") or [""])[0]
            out: dict = {"ok": True, "now": True}
            try:
                src = src or amp.get_input()
                up = src.upper()
                if up in ("SERVER", "NET RADIO", "SPOTIFY", "AIRPLAY", "TUNER"):
                    try:
                        out["play_info"] = amp.play_info(src)
                    except YNCError:
                        out["play_info"] = None
                if up in ("SERVER", "NET RADIO"):
                    try:
                        out["list_info"] = amp.list_info(src)
                    except YNCError:
                        out["list_info"] = None
                out["source"] = src
                self._json(out)
            except YNCError as e:
                self._json({"error": str(e)}, 502)
        elif path == "/api/events":
            self.sse_stream()
        elif path == "/api/inputs":
            try:
                self._json({"inputs": amp.list_inputs()})
            except YNCError as e:
                self._json({"error": str(e)}, 502)
        elif path == "/api/config":
            try:
                self._json(amp.system_config())
            except YNCError as e:
                self._json({"error": str(e)}, 502)
        elif path == "/pushart":
            # pochette intégrée du fichier en cours de push
            if push is not None and push.server.cover_data:
                self._send(200, push.server.cover_data, push.server.cover_mime)
            else:
                self._json({"error": "pas de pochette"}, 404)
        elif path == "/art":
            # proxy pochette d'album (l'URL est relative à l'ampli)
            from urllib.parse import urlparse as _up, parse_qs as _pq
            url = (_pq(_up(self.path).query).get("url") or [""])[0]
            if not url:
                self._json({"error": "url requise"}, 400)
                return
            if url.startswith("/"):
                url = f"http://{amp.url.split('/')[2]}{url}"
            try:
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = r.read()
                    ctype = r.headers.get("Content-Type", "image/jpeg")
                self._send(200, data, ctype)
            except OSError:
                self._json({"error": "image injoignable"}, 502)
        else:
            self._json({"error": "not found"}, 404)

    def sse_stream(self):
        """Flux Server-Sent Events : volume/mute poussés par GENA en direct.

        Un thread par client (ThreadingHTTPServer) ; le flux reste ouvert,
        commentaires `:ping` toutes les 15 s pour maintenir la connexion.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = hub.register()
        try:
            # état courant immédiat (le client n'attend pas le premier changement)
            if gena is not None:
                seed = {}
                v = gena.var("RenderingControl", "Volume")
                if v and v.get("val", "").isdigit():
                    seed["volume"] = int(v["val"])
                m = gena.var("RenderingControl", "Mute")
                if m:
                    seed["mute"] = "On" if m.get("val") == "1" else "Off"
                if seed:
                    self.wfile.write(f"data: {json.dumps(seed)}\n\n".encode())
                    self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b":ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()
        except (OSError, ValueError):
            pass                              # client parti : BrokenPipe/ConnectionReset
        finally:
            hub.unregister(q)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/upload":
            self.handle_upload()
            return
        body = self._body()
        try:
            result = self.dispatch(path, body)
        except YNCError as e:
            self._json({"error": f"ampli: {e}"}, 502)
            return
        except PushError as e:
            self._json({"error": f"push: {e}"}, 502)
            return
        except (ValueError, KeyError, TypeError) as e:
            self._json({"error": f"paramètre invalide: {e}"}, 400)
            return
        except Exception:
            traceback.print_exc()
            self._json({"error": "erreur interne"}, 500)
            return
        if isinstance(result, dict):
            self._json(result)
        else:
            self._json({"ok": True, "state": self.full_state()})

    def handle_upload(self):
        """Reçoit un fichier audio brut (body binaire) dans music_dir/ pour push."""
        qs = parse_qs(urlparse(self.path).query)
        name = (qs.get("name") or ["upload.bin"])[0]
        name = re.sub(r"[^\w.\- ]", "_", os.path.basename(name))[:120] or "upload.bin"
        os.makedirs(music_dir, exist_ok=True)
        dest = os.path.join(music_dir, name)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            self._json({"error": "corps vide"}, 400)
            return
        with open(dest, "wb") as f:
            remaining = n
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 16, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        self._json({"ok": True, "path": os.path.abspath(dest), "name": name})

    def _nav(self, body: dict, action) -> dict:
        """Navigation dans le menu : réponse LÉGÈRE (1 PUT + 1 GET List_Info).

        Le wrapper générique renverrait full_state() (6-8 GET YNC séquentiels,
        ~1 s) à chaque clic sur une ligne du navigateur. L'UI n'a besoin que du
        menu : elle garde l'ancien contenu grisé et ré-interroge /api/list tant
        que Menu_Status != 'Ready'. Pas de clé "state" → pas de re-render complet
        (donc pas de cartes qui bougent pendant la navigation).
        """
        src = body.get("source") or amp.get_input()
        action(src)
        try:
            lst = amp.list_info(src)
        except YNCError:
            lst = None                      # menu pas encore prêt : l'UI relance
        return {"ok": True, "source": src, "list": lst}

    def dispatch(self, path: str, body: dict):
        """Une route = une commande documentée. Retourne dict brut ou None (+state)."""
        match path:
            # --- zone principale
            case "/api/power":
                if "on" in body:
                    amp.set_power(bool(body["on"]))
                else:
                    return {"ok": True, "power": amp.toggle_power(), "state": self.full_state()}
            case "/api/volume":
                if "val" in body:
                    amp.set_volume(int(body["val"]))
                elif "delta" in body:
                    d = int(body["delta"])
                    amp.volume_up(d) if d > 0 else amp.volume_down(-d)
                else:
                    raise ValueError("volume: 'val' ou 'delta' requis")
                # Ack léger : relecture volume/mute en UN seul GET YNC au lieu du
                # full_state() du wrapper générique (6-8 GET séquentiels, ~1 s).
                # L'UI s'en sert comme confirmation fraîche juste après le set
                # (notamment pour caler net un clamp du firmware, ex. 100 → 80).
                # Pas de clé "state" : l'UI ne remplace pas son état complet ici.
                st = amp.basic_status()
                return {"ok": True, "volume": st["volume"], "mute": st["mute"]}
            case "/api/mute":
                if "on" in body:
                    amp.set_mute(bool(body["on"]))
                else:
                    return {"ok": True, "mute": amp.toggle_mute(), "state": self.full_state()}
            case "/api/input":
                amp.set_input(body["val"])
            # --- lecture
            case "/api/playback":
                amp.playback(body.get("source", amp.get_input()), body.get("action", "Play"))
            case "/api/playmode":
                src = body.get("source", amp.get_input())
                if body.get("what") == "shuffle":
                    amp.set_shuffle(src, str(body.get("value", "On")).lower() == "on")
                else:
                    amp.set_repeat(src, body.get("value", "Off"))
            case "/api/list":
                src = body.get("source") or amp.get_input()
                try:
                    lst = amp.list_info(src)
                except YNCError:
                    lst = None          # menu pas prêt : l'UI relance d'elle-même
                return {"ok": True, "source": src, "list": lst}
            case "/api/cursor":
                return self._nav(body, lambda src: amp.cursor(src, body.get("dir", "Down")))
            case "/api/page":
                return self._nav(body, lambda src: amp.page(src, body.get("dir", "Down")))
            case "/api/line":
                return self._nav(body, lambda src: amp.select_line(src, int(body["n"])))
            case "/api/jump":
                # non supporté par le firmware (HTTP 400) — conservé pour mémoire
                amp.jump_line(body.get("source", amp.get_input()), int(body["line"]))
            # --- tuner
            case "/api/tuner":
                op = body.get("op", "tune")
                if op == "tune":
                    amp.tune(body.get("band", "FM"), body["freq"])
                elif op == "band":
                    amp.set_band(body.get("band", "FM"))
                elif op == "step":
                    amp.tuning_step(body.get("dir", "Up"))
                elif op == "preset":
                    amp.select_preset(int(body["n"]))
                elif op == "preset_step":
                    amp.select_preset(body.get("dir", "Up"))
                elif op == "search":
                    amp.auto_search(body.get("band", "FM"), body.get("dir", "Up"))
                else:
                    raise ValueError(f"op tuner inconnue: {op}")
            # --- système (sleep/gapless/device_name : non supportés par ce firmware)
            case "/api/netstandby":
                cur = amp.get_network_standby()
                on = bool(body["on"]) if "on" in body else cur != "On"
                amp.set_network_standby(on)
                return {"ok": True, "net_standby": "On" if on else "Off", "state": self.full_state()}
            case "/api/netname":
                amp.set_network_name(body["name"])
            case "/api/macfilter":
                amp.set_mac_filter(body.get("mode", "Off"),
                                   *[body.get(f"a{i}") for i in range(1, 7)])
            case "/api/update":
                amp.firmware_update_start()
            case "/api/systempower":
                amp.system_power(bool(body.get("on", True)))
            # --- IR virtuel
            case "/api/remote":
                if "code" in body:
                    amp.remote_signal(body["code"])
                else:
                    amp.remote_key(body["key"])
            # --- push DLNA direct (MediaRenderer :8080)
            case "/api/push":
                if push is None:
                    return {"error": "push désactivé (--no-push)"}
                # le renderer ne joue que sur l'entrée réseau SERVER :
                # basculer dessus si besoin (sinon PLAYING mais muet !)
                import time
                try:
                    cur = amp.get_input()
                except YNCError:
                    cur = None
                if cur != "SERVER":
                    amp.set_input("SERVER")
                    time.sleep(4)   # RC=3 "occupé" après changement de source réseau
                kw = {k: body[k] for k in ("title", "artist", "album") if body.get(k)}
                if body.get("file"):
                    r = push.push_file(body["file"], **kw)
                elif body.get("url"):
                    r = push.push_url(body["url"], mime=body.get("mime", "audio/mpeg"), **kw)
                else:
                    raise ValueError("file ou url requis")
                return {"ok": True, "push": r, "state": self.full_state()}
            case "/api/pushctl":
                if push is None:
                    return {"error": "push désactivé (--no-push)"}
                action = body.get("action", "Play")
                if action == "Play":
                    push.play()
                elif action == "Pause":
                    push.pause()
                elif action == "Stop":
                    push.stop()
                elif action == "Seek":
                    push.seek(body.get("target", "0:00:00"))
                else:
                    raise ValueError(f"action inconnue: {action}")
                return {"ok": True, "push": push.state(), "state": self.full_state()}
            # --- passe-plat YNC brut (pour explorer)
            case "/api/raw":
                root = amp._request(body.get("cmd", "GET").upper(), body["xml"])
                import xml.etree.ElementTree as ET
                return {"ok": True, "rsp": ET.tostring(root, encoding="unicode")}
            case _:
                return {"error": "not found"}
        return None


def main():
    p = argparse.ArgumentParser(description="Serveur web Yamaha R-N301")
    p.add_argument("--amp", default="192.168.1.91", help="IP de l'ampli")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--push-port", type=int, default=9000, help="port HTTP servant les fichiers poussés")
    p.add_argument("--music", default="music", help="dossier des uploads")
    p.add_argument("--no-push", action="store_true", help="désactive le push DLNA")
    p.add_argument("--no-gena", action="store_true",
                   help="désactive les events GENA (volume/mute en push SSE)")
    args = p.parse_args()

    global amp, push, gena, music_dir
    amp = RN301(args.amp)
    music_dir = args.music
    if not args.no_push:
        push = PushServer(args.amp, serve_port=args.push_port)
        push.start()
        print(f"push DLNA: fichiers servis sur :{args.push_port} (uploads -> {music_dir}/)")
    if not args.no_gena:
        # Volume/mute instantanés : GENA RenderingControl -> hub -> SSE /api/events.
        # Dégradé gracieux : si l'abonnement échoue, le polling 4 s suffit.
        try:
            gena = GenaClient(args.amp,
                              on_error=lambda svc, msg: print(f"GENA {svc}: {msg}"))
            gena.subscribe("RenderingControl", on_gena_event)
            print("GENA: volume/mute en push (RenderingControl abonné)")
        except Exception as e:
            gena = None
            print(f"GENA indisponible ({e}) — polling 4 s uniquement")

    class _QuietServer(ThreadingHTTPServer):
        """ThreadingHTTPServer qui avale le bruit des fermetures de connexions.

        Quand un navigateur ferme un flux SSE (ou change d'onglet), le recv du
        thread serveur lève ConnectionAborted/Reset (WinError 10053/10054) ;
        socketserver print alors un traceback complet alors que le serveur se
        porte très bien. On log seulement ce qui n'est pas une déconnexion.
        """
        daemon_threads = True    # ne pas bloquer le shutdown sur un client fantôme

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            # Déconnexion client (fermeture d'un flux SSE, onglet qui change…) :
            # ConnectionError couvre 10053/10054 ; on filtre aussi par errno pour
            # ne rien avaler d'autre qu'un aborted/reset.
            aborted = getattr(exc, "errno", None) in (10053, 10054)  # WSAECONNABORTED/RESET
            if isinstance(exc, (ConnectionError, BrokenPipeError)) or aborted:
                return                 # déconnexion normale : silencieux
            super().handle_error(request, client_address)

    srv = _QuietServer((args.host, args.port), Handler)
    print(f"R-N301 web: http://localhost:{args.port}  (ampli: {args.amp})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        if gena is not None:
            gena.close()
        if push is not None:
            push.close()


if __name__ == "__main__":
    main()
