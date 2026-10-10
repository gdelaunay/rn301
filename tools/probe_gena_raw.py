"""Sonde GENA : body BRUT des NOTIFY pendant manipulation physique de l'ampli.

Complément de `probe_gena.py` (qui valide le protocole et dumpt le SCPD).
Ici on s'abonne à `RenderingControl/evt` + `AVTransport/evt`, on loggue le
**body brut** de chaque NOTIFY horodaté, et on sonde le YNC en parallèle toutes
les 0,5 s — ce qui permet de corréler les deux piles et de répondre à :

  « qu'est-ce que GENA voit vraiment, et qu'est-ce qu'il ne voit jamais ? »

Résultats mesurés en live le 2025-10-10 (firmware 1.13/0.05, fenêtre de 420 s,
manipulations au bouton physique + télécommande) :

  ✅ RenderingControl notifie Volume/Mute — 26 events, valeurs exactes,
     ~30 ms AVANT que le polling YNC ne voie le changement.
  ✅ Échelle UPnP == échelle YNC : au maximum les deux renvoient 80. Le
     `allowedValueRange 0..100` du SCPD est déclaratif, le plafond réel vient
     du réglage « Max Volume » de l'ampli.
  ❌ Aucun event sur : changement d'`Input_Sel` (LINE1↔COAXIAL↔LINE2↔SERVER),
     `Power` On↔Standby, lecture SERVER. AVTransport : 1 seul event (l'initial)
     — il ne reflète que le renderer DLNA, pas la lecture réseau YNC.
  ⚠️ `LastChange` est INCRÉMENTAL : seul SEQ=0 donne l'état complet, les
     suivants ne contiennent que la variable modifiée.
  ⚠️ Events MODÉRÉS (coalescés) : en tournant vite le bouton, GENA envoie moins
     d'events que de pas réels et peut sauter des valeurs intermédiaires. Il
     donne la valeur finale fiable, pas un historique exhaustif.
  ⚠️ `TIMEOUT: Second-600` demandé → `Second-300` rendu : plafond fixe,
     renouvellement du SID à prévoir vers 250 s.

Usage: python tools/probe_gena_raw.py [duree_sec] [ip_ampli]
       → manipule l'ampli pendant la fenêtre, puis lis la sortie.
"""
from __future__ import annotations

import html
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DUREE = int(sys.argv[1]) if len(sys.argv) > 1 else 120
AMP = sys.argv[2] if len(sys.argv) > 2 else "192.168.1.91"
PORT = 8080
YNC = f"http://{AMP}/YamahaRemoteControl/ctrl"
SERVICES = ("RenderingControl", "AVTransport")

t0 = time.time()
sids: dict[str, str] = {}
lock = threading.Lock()


def log(msg: str) -> None:
    with lock:
        print(f"[{time.time() - t0:7.2f}s] {msg}", flush=True)


def local_ip() -> str:
    """IP de cette machine vue depuis l'ampli (le callback doit être joignable)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((AMP, PORT))
        return s.getsockname()[0]
    finally:
        s.close()


class Callback(BaseHTTPRequestHandler):
    """Récepteur des NOTIFY. Doit répondre 200 sinon l'ampli oublie l'abonné."""

    protocol_version = "HTTP/1.1"
    server_version = "RN301Probe/1.0"

    def log_message(self, *args):
        pass

    def do_NOTIFY(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf8", "replace") if n else ""
        svc = sids.get(self.headers.get("SID"), "?")
        seq = self.headers.get("SEQ", "?")
        log(f"NOTIFY {svc} SEQ={seq} — {decode_lastchange(body)}")
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def decode_lastchange(body: str) -> str:
    """LastChange = XML échappé dans du XML → décodage puis extraction des valeurs.

    Attention à l'ordre des attributs : `<Volume channel="Master" val="50"/>`,
    `channel` vient AVANT `val`.
    """
    dec = html.unescape(body)
    out = []
    for name, chan, val in re.findall(
            r'<(\w+)(?:\s+channel="([^"]*)")?\s+val="([^"]*)"', dec):
        if name in ("InstanceID", "Event"):
            continue
        out.append(f"{name}[{chan}]" if chan else name)
        out[-1] += f"={val}"
    return ", ".join(out) or " ".join(body.split())[:300]


def req(url, method="GET", data=None, headers=None):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=6) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf8", "replace")
    except Exception as e:
        return None, {}, str(e)


def poll_ync():
    """Sonde le YNC en parallèle pour corréler : GENA voit-il la même chose ?"""
    last = None
    body = ('<YAMAHA_AV cmd="GET"><Main_Zone><Basic_Status>GetParam'
            "</Basic_Status></Main_Zone></YAMAHA_AV>")
    while time.time() - t0 < DUREE:
        st, _, resp = req(YNC, "POST", body.encode(),
                          {"Content-Type": "text/xml; charset=utf-8"})
        if st == 200:
            cur = {t: (re.search(rf"<{t}>([^<]*)</{t}>", resp) or (None, None))[1]
                   for t in ("Power", "Val", "Mute", "Input_Sel")}
        else:
            cur = {"_http": st}
        k = str(sorted(cur.items()))
        if k != last:
            log(f"   YNC Basic_Status -> {cur}")
            last = k
        time.sleep(0.5)


def main():
    ip = local_ip()
    srv = ThreadingHTTPServer((ip, 0), Callback)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f"<http://{ip}:{port}/cb>"
    log(f"callback = {cb}")

    for name in SERVICES:
        st, h, _ = req(f"http://{AMP}:{PORT}/{name}/evt", "SUBSCRIBE", None,
                       {"CALLBACK": cb, "NT": "upnp:event",
                        "TIMEOUT": "Second-600", "Host": f"{AMP}:{PORT}"})
        if h.get("SID"):
            sids[h["SID"]] = name
        log(f"SUBSCRIBE {name:<18} -> HTTP {st}  TIMEOUT={h.get('TIMEOUT')}"
            f"{'  SID=' + h['SID'][:20] if h.get('SID') else '  ÉCHEC'}")

    log(f"=== MANIPULE L'AMPLI PENDANT {DUREE} s ===")
    log("    volume physique, mute, changement de source, power, lecture")
    threading.Thread(target=poll_ync, daemon=True).start()
    time.sleep(DUREE)

    for sid, name in sids.items():
        st, _, _ = req(f"http://{AMP}:{PORT}/{name}/evt", "UNSUBSCRIBE", None,
                       {"SID": sid, "Host": f"{AMP}:{PORT}"})
        log(f"UNSUBSCRIBE {name} -> {st}")
    srv.shutdown()
    srv.server_close()
    log("fin")


if __name__ == "__main__":
    main()
