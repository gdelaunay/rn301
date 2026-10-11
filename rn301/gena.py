"""Eventing GENA (UPnP SUBSCRIBE/NOTIFY) pour le MediaRenderer du R-N301.

Protocole validé en live le 2025-10-10 (tools/probe_gena.py, tools/probe_gena_raw.py,
voir docs/DLNA_PUSH.md §Eventing GENA) :

  SUBSCRIBE /AVTransport/evt   -> 200 + SID + TIMEOUT: Second-300 (plafond firmware,
                                  Second-600 demandé -> 300 rendu)
  NOTIFY initial immédiat      -> NTS: upnp:propchange, SEQ: 0, LastChange complet
  UNSUBSCRIBE + SID            -> 200

Périmètre réel mesuré sur le R-N301 : GENA notifie le volume/mute (RenderingControl,
y compris depuis la façade et la télécommande, ~30 ms plus vite que le polling)
et le transport du push DLNA (AVTransport). Il ne voit JAMAIS Power, Input_Sel
ni la lecture réseau YNC -> il complète le polling, ne le remplace pas.

Pièges d'implémentation couverts ici (mesurés en live) :

  1. LastChange est INCRÉMENTAL : seul SEQ=0 porte l'état complet, les suivants
     seulement la variable modifiée -> on accumule un état local (self.state()).
  2. Events MODÉRÉS/coalescés : la valeur finale est fiable, pas l'historique.
  3. Attributs dans l'ordre inverse (<Volume channel="Master" val="50"/>) :
     parsing ElementTree, jamais de regex sur l'ordre.
  4. TIMEOUT plafonné à 300 s par le firmware : renouvellement du SID à ~240 s,
     re-SUBSCRIBE complet si le renouvellement échoue (404/412 = SID périmé).

Le callback doit répondre 200 aux NOTIFY, sinon l'ampli déclare l'abonné mort :
le serveur HTTP intégré le fait (il doit être joignable depuis l'ampli — IP LAN,
pare-feu).

Usage:
    from rn301 import GenaClient

    gena = GenaClient("192.168.1.91")
    gena.subscribe("RenderingControl", lambda svc, changed: print(svc, changed))
    gena.subscribe("AVTransport")

    gena.state("RenderingControl")     # {'Volume': {'channel': 'Master', 'val': '50'}, ...}
    gena.var("RenderingControl", "Volume")            # {'channel': 'Master', 'val': '50'}
    gena.wait_for("AVTransport",                      # bloque jusqu'à l'event
                  lambda st: st.get("TransportState", {}).get("val") == "STOPPED",
                  timeout=30)
    gena.close()                       # UNSUBSCRIBE propre + arrêt du serveur

    with GenaClient("192.168.1.91") as gena:          # idem, close() automatique
        ...

Self-test live (sans ampli : s'arrêter au message d'erreur) :
    python -m rn301.gena [durée_sec] [ip_ampli]

100 % stdlib.
"""
from __future__ import annotations

import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RENDERER_PORT = 8080
EVENT_NS = "urn:schemas-upnp-org:event-1-0"

# Chemins eventSubURL des trois services du renderer (desc.xml archivé dans
# amp/upnp_desc.xml). ConnectionManager existe mais n'a rien d'intéressant à
# notifier sur ce firmware — laissé pour complétude.
SERVICE_PATHS = {
    "AVTransport": "/AVTransport/evt",
    "RenderingControl": "/RenderingControl/evt",
    "ConnectionManager": "/ConnectionManager/evt",
}

DEFAULT_TIMEOUT = 300   # plafond mesuré du firmware (Second-600 demandé -> 300 rendu)
RENEW_MARGIN = 60       # renouveler le SID 60 s avant l'expiration (échéance ~240 s)

#: signature des callbacks : (service, variables_changées) -> None
EventCallback = Callable[[str, "dict[str, dict[str, str]]"], None]
ErrorCallback = Callable[[str, str], None]


class GenaError(Exception):
    """Erreur d'abonnement GENA (SUBSCRIBE/UNSUBSCRIBE refusé, réseau…)."""

    def __init__(self, message: str, http: int = 0):
        super().__init__(message)
        self.http = http


# ---------------------------------------------------------------------------
# parsing du LastChange (double décodage : XML échappé dans une balise XML)
# ---------------------------------------------------------------------------

def parse_propertyset(body: bytes | str) -> dict[str, dict[str, str]]:
    """Body brut d'un NOTIFY -> {nom_variable: {attributs}}.

    Le NOTIFY contient <e:propertyset><e:property><LastChange>&lt;Event…&gt;
    </LastChange></e:property></e:propertyset> : le texte de LastChange est un
    document XML échappé que le premier fromstring dé-échappe automatiquement.
    R-N301 = instance unique (InstanceID val="0") -> état à plat.

    Les attributs sont rendus tels quels : {'Volume': {'channel': 'Master',
    'val': '50'}} — insensible à l'ordre (piège mesuré : channel avant val).
    """
    if isinstance(body, str):
        body = body.encode("utf-8", "replace")
    root = ET.fromstring(body)
    out: dict[str, dict[str, str]] = {}
    for prop in root.iter(f"{{{EVENT_NS}}}property"):
        for child in prop:
            text = child.text or ""
            if child.tag.split("}")[-1] != "LastChange" or not text.strip():
                continue
            try:
                event = ET.fromstring(text.strip())
            except ET.ParseError:
                continue
            for instance in event:                 # <InstanceID val="0">
                for var in instance:               # <Volume channel=… val=…/>
                    name = var.tag.split("}")[-1]
                    out[name] = dict(var.attrib)
    return out


# ---------------------------------------------------------------------------
# serveur de callback (répond 200 aux NOTIFY, sinon l'ampli oublie l'abonné)
# ---------------------------------------------------------------------------

class _NotifyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RN301GENA/1.0"

    def log_message(self, *args):
        pass

    def do_NOTIFY(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n else b""
        # 200 IMMÉDIAT : UPnP exige la réponse, sinon abonné déclaré mort
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()
        owner: GenaClient = self.server.owner  # type: ignore[attr-defined]
        owner._handle_notify(dict(self.headers), body)


# ---------------------------------------------------------------------------
# état d'un abonnement
# ---------------------------------------------------------------------------

class _Subscription:
    __slots__ = (
        "callback",
        "deadline",
        "last_seq",
        "missed",
        "renew_fails",
        "service",
        "sid",
        "state",
        "timeout",
    )

    def __init__(self, service: str, callback: EventCallback | None):
        self.service = service
        self.callback = callback
        self.sid: str | None = None
        self.timeout: int = DEFAULT_TIMEOUT
        self.deadline: float = 0.0        # renouveler avant cette échéance
        self.last_seq: int = -1
        self.missed: int = 0              # events perdus (trous de SEQ)
        self.renew_fails: int = 0
        # état ACCUMULÉ : LastChange est incrémental, on applique des deltas
        self.state: dict[str, dict[str, str]] = {}


def _local_ip(toward: str, port: int) -> str:
    """IP de cette machine vue depuis l'ampli (évite 127.0.0.1 / multi-homing)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((toward, port))
        return s.getsockname()[0]
    finally:
        s.close()


# ---------------------------------------------------------------------------
# client GENA
# ---------------------------------------------------------------------------

class GenaClient:
    """Abonné GENA aux services du MediaRenderer R-N301 (:8080).

    Un seul serveur de callback pour tous les services (dispatch par SID),
    un thread daemon renouvelle les abonnements avant l'expiration des SID.
    """

    def __init__(self, amp: str, port: int = RENDERER_PORT,
                 bind_ip: str | None = None,
                 request_timeout: float = 8.0,
                 on_error: ErrorCallback | None = None):
        self.amp = amp
        self.port = port
        self.request_timeout = request_timeout
        self.on_error = on_error            # (service, message) — erreurs de fond
        self._bind_ip = bind_ip
        self._subs: dict[str, _Subscription] = {}
        self._by_sid: dict[str, str] = {}
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._httpd: ThreadingHTTPServer | None = None
        self._serve_thread: threading.Thread | None = None
        self._renew_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.callback_url: str | None = None

    # ---------------------------------------------------------------- cycle de vie

    def start(self) -> None:
        """Démarre le serveur de callback + le thread de renouvellement."""
        if self._httpd is not None:
            return
        bind = self._bind_ip or _local_ip(self.amp, self.port)
        self._httpd = ThreadingHTTPServer((bind, 0), _NotifyHandler)
        self._httpd.owner = self  # type: ignore[attr-defined]
        self.callback_url = f"<http://{bind}:{self._httpd.server_address[1]}/cb>"
        self._stop.clear()
        self._serve_thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="gena-callback")
        self._serve_thread.start()
        self._renew_thread = threading.Thread(
            target=self._renew_loop, daemon=True, name="gena-renew")
        self._renew_thread.start()

    def close(self) -> None:
        """UNSUBSCRIBE (best effort) + arrêt du serveur et des threads."""
        for service in list(self._subs):
            try:
                self.unsubscribe(service)
            except GenaError:
                pass
        self._stop.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self.callback_url = None

    def __enter__(self) -> GenaClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------- abonnements

    def subscribe(self, service: str, callback: EventCallback | None = None) -> str:
        """SUBSCRIBE à un service (AVTransport / RenderingControl / ConnectionManager).

        Démarre le serveur de callback si besoin. Renvoie le SID.
        L'event initial (SEQ=0, état complet) arrive immédiatement en NOTIFY.
        Un callback existant est remplacé ; se réabonner est idempotent côté ampli
        (nouveau SID).
        """
        if service not in SERVICE_PATHS:
            raise ValueError(f"service inconnu: {service!r} "
                             f"(dispo: {', '.join(SERVICE_PATHS)})")
        self.start()
        sub = _Subscription(service, callback)
        headers = {
            "CALLBACK": self.callback_url or "",
            "NT": "upnp:event",
            "TIMEOUT": f"Second-{DEFAULT_TIMEOUT}",
        }
        status, rsp_headers = self._http("SUBSCRIBE", service, headers)
        if status != 200:
            raise GenaError(f"SUBSCRIBE {service} refusé (HTTP {status})", http=status)
        sid = rsp_headers.get("SID") or rsp_headers.get("sid")
        if not sid:
            raise GenaError(f"SUBSCRIBE {service} : pas de SID dans la réponse")
        sub.sid = sid
        sub.timeout = self._parse_timeout(rsp_headers.get("TIMEOUT") or rsp_headers.get("timeout"))
        sub.deadline = time.time() + max(sub.timeout - RENEW_MARGIN, sub.timeout / 2)
        with self._lock:
            old = self._subs.get(service)
            if old and old.sid:
                self._by_sid.pop(old.sid, None)
            self._subs[service] = sub
            self._by_sid[sid] = service
        return sid

    def unsubscribe(self, service: str) -> None:
        """UNSUBSCRIBE (SID) + oubli de l'état local. Silencieux si non abonné."""
        with self._lock:
            sub = self._subs.pop(service, None)
            if sub is None:
                return
            if sub.sid:
                self._by_sid.pop(sub.sid, None)
        if sub.sid:
            try:
                self._http("UNSUBSCRIBE", service, {"SID": sub.sid})
            except GenaError:
                pass  # déjà mort côté ampli : rien à faire

    def renew(self, service: str) -> None:
        """Renouvellement manuel du SID (le thread le fait déjà tout seul)."""
        with self._lock:
            sub = self._subs.get(service)
            if sub is None or sub.sid is None:
                raise GenaError(f"pas d'abonnement actif sur {service}")
        self._renew(sub)

    # ---------------------------------------------------------------- état / attente

    def is_subscribed(self, service: str) -> bool:
        """True si un abonnement actif existe sur ce service."""
        with self._lock:
            return service in self._subs and self._subs[service].sid is not None

    def state(self, service: str) -> dict[str, dict[str, str]]:
        """État ACCUMULÉ du service : copie de {variable: {attributs}}.

        Complet après le NOTIFY initial (SEQ=0), mis à jour par deltas ensuite
        (LastChange incrémental — piège n°1).
        """
        with self._lock:
            sub = self._subs.get(service)
            return {k: dict(v) for k, v in sub.state.items()} if sub else {}

    def var(self, service: str, name: str) -> dict[str, str] | None:
        """Une variable de l'état accumulé, ex var('RenderingControl', 'Volume')
        -> {'channel': 'Master', 'val': '50'} (None si pas encore notifiée)."""
        with self._lock:
            sub = self._subs.get(service)
            v = sub.state.get(name) if sub else None
            return dict(v) if v else None

    def missed(self, service: str) -> int:
        """Nombre d'events perdus (trous de SEQ détectés). >0 -> re-sync conseillée
        (GetTransportInfo / GetVolume en SOAP)."""
        with self._lock:
            sub = self._subs.get(service)
            return sub.missed if sub else 0

    def wait_for(self, service: str,
                 predicate: Callable[[dict[str, dict[str, str]]], bool],
                 timeout: float | None = None) -> bool:
        """Bloque jusqu'à ce que predicate(état_accumulé) soit vrai. Renvoie
        True si satisfait, False à l'expiration du timeout.

        Ex (fin d'une piste poussée, pour un futur speak()) :
            gena.wait_for("AVTransport",
                          lambda st: st.get("TransportState", {}).get("val")
                          in ("STOPPED", "NO_MEDIA_PRESENT"),
                          timeout=duree + 10)
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while True:
                sub = self._subs.get(service)
                if sub is not None and predicate(sub.state):
                    return True
                if deadline is None:
                    self._cond.wait(1.0)
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._cond.wait(min(remaining, 1.0))

    # ---------------------------------------------------------------- interne

    def _http(self, method: str, service: str,
              headers: dict[str, str]) -> tuple[int, dict[str, str]]:
        url = f"http://{self.amp}:{self.port}{SERVICE_PATHS[service]}"
        req = urllib.request.Request(
            url, method=method,
            headers={"HOST": f"{self.amp}:{self.port}", **headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout) as r:
                return r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)
        except OSError as e:
            raise GenaError(f"{method} {service} : renderer injoignable ({url}): {e}") from None

    @staticmethod
    def _parse_timeout(header: str | None) -> int:
        m = re.search(r"Second-(\d+)", header or "")
        return int(m.group(1)) if m else DEFAULT_TIMEOUT

    def _renew(self, sub: _Subscription) -> None:
        """SUBSCRIBE avec SID (pas de NT/CALLBACK) = renouvellement.
        404/412 = SID inconnu/périmé -> re-SUBSCRIBE complet ( SEQ=0 re-émis)."""
        assert sub.sid is not None
        status, rsp_headers = self._http("SUBSCRIBE", sub.service, {"SID": sub.sid})
        if status == 200:
            sub.timeout = self._parse_timeout(
                rsp_headers.get("TIMEOUT") or rsp_headers.get("timeout"))
            sub.deadline = time.time() + max(sub.timeout - RENEW_MARGIN, sub.timeout / 2)
            sub.renew_fails = 0
            return
        if status in (404, 412):
            self._resubscribe(sub)
            return
        raise GenaError(f"renouvellement SID {sub.service} refusé (HTTP {status})",
                        http=status)

    def _resubscribe(self, sub: _Subscription) -> None:
        """Réabonnement complet après SID périmé : l'état local est conservé
        (le nouveau SEQ=0 le rafraîchira)."""
        headers = {
            "CALLBACK": self.callback_url or "",
            "NT": "upnp:event",
            "TIMEOUT": f"Second-{DEFAULT_TIMEOUT}",
        }
        status, rsp_headers = self._http("SUBSCRIBE", sub.service, headers)
        if status != 200:
            raise GenaError(f"re-SUBSCRIBE {sub.service} refusé (HTTP {status})",
                            http=status)
        sid = rsp_headers.get("SID") or rsp_headers.get("sid")
        if not sid:
            raise GenaError(f"re-SUBSCRIBE {sub.service} : pas de SID")
        with self._lock:
            if sub.sid:
                self._by_sid.pop(sub.sid, None)
            sub.sid = sid
            self._by_sid[sid] = sub.service
        sub.timeout = self._parse_timeout(rsp_headers.get("TIMEOUT") or rsp_headers.get("timeout"))
        sub.deadline = time.time() + max(sub.timeout - RENEW_MARGIN, sub.timeout / 2)
        sub.renew_fails = 0

    def _renew_loop(self) -> None:
        """Thread daemon : renouvellement des SID avant échéance (~240 s)."""
        while not self._stop.wait(15):
            now = time.time()
            for sub in list(self._subs.values()):
                if sub.sid is None or now < sub.deadline:
                    continue
                try:
                    self._renew(sub)
                except GenaError as e:
                    sub.renew_fails += 1
                    if sub.renew_fails >= 3:
                        # trop d'échecs : on arrête de matraquer l'ampli
                        sub.deadline = now + 300
                    if self.on_error:
                        try:
                            self.on_error(sub.service, str(e))
                        except Exception:
                            pass

    def _handle_notify(self, headers: dict[str, str], body: bytes) -> None:
        """Dispatch d'un NOTIFY : SID -> abonnement, SEQ -> trous, LastChange -> état."""
        h = {k.lower(): v for k, v in headers.items()}
        if h.get("nts") and h["nts"] != "upnp:propchange":
            return
        with self._lock:
            service = self._by_sid.get(h.get("sid", ""))
            sub = self._subs.get(service) if service else None
            if sub is None:
                return                       # NOTIFY d'un ancien SID : ignorer
            try:
                seq = int(h.get("seq", "-1"))
            except ValueError:
                seq = -1
            if seq >= 0:
                if sub.last_seq >= 0 and seq > sub.last_seq + 1:
                    sub.missed += seq - sub.last_seq - 1   # trou = events perdus
                sub.last_seq = seq
            try:
                changed = parse_propertyset(body)
            except ET.ParseError:
                changed = {}
            sub.state.update(changed)        # delta : ne JAMAIS écraser l'état
            self._cond.notify_all()
            callback = sub.callback
        if callback and changed:
            try:
                callback(service or sub.service, changed)
            except Exception:
                pass                         # un callback cassé ne tue pas l'abonné


# ---------------------------------------------------------------------------
# self-test live : python -m rn301.gena [durée] [ip_ampli]
# ---------------------------------------------------------------------------

def _main() -> None:
    duree = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    amp = sys.argv[2] if len(sys.argv) > 2 else "192.168.1.91"
    t0 = time.time()

    def log(service: str, changed: dict) -> None:
        vals = ", ".join(f"{k}={v}" for k, v in changed.items())
        print(f"[{time.time() - t0:7.2f}s] {service}: {vals}", flush=True)

    gena = GenaClient(amp, on_error=lambda svc, msg: print(f"!! {svc}: {msg}", flush=True))
    try:
        for svc in ("RenderingControl", "AVTransport"):
            sid = gena.subscribe(svc, log)
            print(f"abonné {svc} -> {sid} (callback {gena.callback_url})", flush=True)
        print(f"écoute {duree} s — manipulez le volume/la façade, poussez un audio…",
              flush=True)
        time.sleep(duree)
        for svc in ("RenderingControl", "AVTransport"):
            st = gena.state(svc)
            print(f"\nétat final accumulé {svc} ({gena.missed(svc)} event(s) perdu(s)) :",
                  flush=True)
            for k, v in sorted(st.items()):
                print(f"  {k} = {v}")
    except GenaError as e:
        print(f"erreur GENA : {e}")
    finally:
        gena.close()
        print("UNSUBSCRIBE + fermeture OK")


if __name__ == "__main__":
    _main()
