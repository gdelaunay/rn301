"""Sonde READ-ONLY du renderer UPnP : SCPD AVTransport + capacités + eventing GENA.

Ne modifie AUCUN état de l'ampli : uniquement des actions SOAP de lecture
(GetDeviceCapabilities / GetCurrentTransportActions / GetTransportInfo) et un
abonnement GENA rendu proprement par UNSUBSCRIBE. Pas de SetAVTransportURI,
pas de Play/Pause/Stop/Seek.

Vérifie trois choses :
  1. les capacités réelles du renderer (actions valides à l'instant t, médias)
  2. le transport courant (état + statut)
  3. l'eventing GENA : SUBSCRIBE sur /AVTransport/evt -> NOTIFY initial (SEQ 0)

Résultats validés en live le 2025-10-10 sur firmware 1.13/0.05 :
  SUBSCRIBE -> 200 + SID + TIMEOUT honoré, SERVER: KnOS/3.2 UPnP/1.0 DMP/3.5
  1 NOTIFY immédiat (NTS: upnp:propchange, SEQ: 0) avec LastChange complet
  UNSUBSCRIBE -> 200

Usage: .venv/Scripts/python tools/probe_gena.py [ip_ampli]
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AMP = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.91"
PORT = 8080
EVT = f"http://{AMP}:{PORT}/AVTransport/evt"
CTRL = f"http://{AMP}:{PORT}/AVTransport/ctrl"
SCPD = f"http://{AMP}:{PORT}/AVTransport/desc.xml"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
SCPD_NS = "urn:schemas-upnp-org:service-1-0"

# actions de lecture uniquement — ne rien ajouter qui écrive dans l'ampli
READONLY_ACTIONS = ("GetDeviceCapabilities", "GetCurrentTransportActions",
                    "GetTransportInfo", "GetMediaInfo", "GetPositionInfo",
                    "GetTransportSettings")

events: list[tuple[dict, str]] = []


class _Callback(BaseHTTPRequestHandler):
    """Récepteur des NOTIFY GENA (l'ampli POSTE ici à chaque changement)."""

    protocol_version = "HTTP/1.1"
    server_version = "RN301Probe/1.0"

    def log_message(self, *args):
        pass

    def do_NOTIFY(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf8", "replace") if n else ""
        events.append((dict(self.headers), body))
        # UPnP exige un 200 ; sinon l'appareil considère l'abonné mort
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def local_ip() -> str:
    """IP de cette machine vue depuis l'ampli (le callback doit être joignable)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((AMP, PORT))
        return s.getsockname()[0]
    finally:
        s.close()


def soap(action: str, body: str = "<InstanceID>0</InstanceID>"):
    env = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{AVT}">{body}</u:{action}>'
        "</s:Body></s:Envelope>"
    )
    req = urllib.request.Request(CTRL, data=env.encode("utf8"), headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{AVT}#{action}"',
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.read().decode("utf8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf8", "replace")
    except Exception as e:                       # timeout, refus, injoignable
        return None, str(e)


def _body_only(resp: str) -> str:
    if "<s:Body>" in resp:
        resp = resp.split("<s:Body>", 1)[1].split("</s:Body>", 1)[0]
    return " ".join(resp.split())


def subscribe(method: str, headers: dict):
    req = urllib.request.Request(EVT, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)
    except Exception as e:
        return None, str(e)


def dump_scpd():
    """Liste actions + variables d'état déclarées par le service (contrat UPnP)."""
    try:
        with urllib.request.urlopen(SCPD, timeout=8) as r:
            raw = r.read().decode("utf8", "replace")
    except Exception as e:
        print(f"SCPD illisible : {e}")
        return

    def L(tag):
        return f"{{{SCPD_NS}}}{tag}"

    root = ET.fromstring(raw)
    print("\n--- SCPD AVTransport : actions déclarées ---")
    for a in root.iter(L("action")):
        ins, outs = [], []
        for arg in a.iter(L("argument")):
            (ins if arg.findtext(L("direction")) == "in" else outs).append(
                arg.findtext(L("name")))
        print(f"  {a.findtext(L('name')):<28} in=[{','.join(ins)}]"
              f"{'  out=[' + ','.join(outs) + ']' if outs else ''}")

    print("\n--- SCPD AVTransport : variables d'état ---")
    for sv in root.iter(L("stateVariable")):
        dt = sv.find(L("dataType"))
        rng = sv.find(L("allowedValueRange"))
        vals = [v.text for v in sv.iter(L("allowedValue"))]
        extra = ""
        if rng is not None:
            extra = (f" range={rng.findtext(L('minimum'))}.."
                     f"{rng.findtext(L('maximum'))}")
        if vals:
            shown = ",".join(vals[:6]) + ("…" if len(vals) > 6 else "")
            extra += f" values=[{shown}]"
        ev = " EVENTED" if sv.get("sendEvents") == "yes" else ""
        print(f"  {sv.findtext(L('name')):<30}"
              f"{dt.text if dt is not None else '?':<8}{ev}{extra}")


def main():
    dump_scpd()

    print("\n--- capacités live (actions SOAP en lecture seule) ---")
    for action in READONLY_ACTIONS:
        st, resp = soap(action)
        print(f"  [{action}] HTTP {st}")
        print(f"      {_body_only(resp)[:400]}")

    ip = local_ip()
    srv = ThreadingHTTPServer((ip, 0), _Callback)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f"<http://{ip}:{port}/cb>"

    print(f"\n--- SUBSCRIBE GENA (callback {cb}) ---")
    st, hdrs = subscribe("SUBSCRIBE", {
        "CALLBACK": cb, "NT": "upnp:event",
        "TIMEOUT": "Second-300", "Host": f"{AMP}:{PORT}",
    })
    print(f"  HTTP {st}")
    if isinstance(hdrs, dict):
        for k in ("SID", "TIMEOUT", "SERVER"):
            if k in hdrs:
                print(f"  {k}: {hdrs[k]}")
    else:
        print(f"  erreur : {hdrs}")

    sid = hdrs.get("SID") if isinstance(hdrs, dict) else None
    print("\n--- attente du NOTIFY initial (8 s) ---")
    time.sleep(8)
    print(f"  NOTIFY reçus : {len(events)}")
    for h, b in events[:3]:
        keys = {k: v for k, v in h.items()
                if k.lower() in ("nt", "nts", "sid", "seq", "content-type")}
        print(f"  headers : {keys}")
        print(f"  body    : {' '.join(b.split())[:700]}")

    if sid:
        st, _ = subscribe("UNSUBSCRIBE", {"SID": sid, "Host": f"{AMP}:{PORT}"})
        print(f"\n--- UNSUBSCRIBE (nettoyage) -> HTTP {st} ---")

    srv.shutdown()
    srv.server_close()


if __name__ == "__main__":
    main()
