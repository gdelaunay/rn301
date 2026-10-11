"""Découverte SSDP/UPnP du R-N301 sur le LAN — sans IP fixe.

Chaîne validée sur le matériel (le desc.xml réel est archivé dans
amp/upnp_desc.xml) :

    M-SEARCH 239.255.255.250:1900 (ST=MediaRenderer:1 + upnp:rootdevice,
                                   MX=2, TTL=2, collecte ~4,5 s)
      → LOCATION  http://<ip>:8080/MediaRenderer/desc.xml
      → filtre    manufacturer="Yamaha Corporation" && modelName="R-N301"
                  (dédoublonnage par UDN, LOCATION sur la même IP que
                   l'émetteur du datagramme)
      → desc.xml  yamaha:X_URLBase    = http://<ip>:80/     ← l'endpoint YNC
                  yamaha:X_controlURL = /YamahaRemoteControl/ctrl
                  + friendlyName, UDN, serialNumber, iconList, presentationURL

Un seul scan donne donc TOUT : l'IP, le renderer UPnP (:8080), l'endpoint YNC
(:80) et la page de config embarquée. De quoi construire RN301 / PushServer
sans jamais connaître l'adresse de l'ampli.

Usage:
    from rn301 import discover, discover_one, RN301, PushServer

    for amp in discover():                      # tous les R-N301 du LAN
        print(amp["host"], amp["model"], amp["ync_url"])

    found = discover_one()                      # le premier, ou None
    if found:
        amp = RN301(found["host"])              # client YNC
        push = PushServer(found["host"])        # push DLNA
        # found["presentation_url"]             # page de config de l'ampli
        # found["icons"]                        # URLs des icônes officielles

    discover(model=None)                        # tous les Yamaha, tous modèles
    discover(manufacturer=None)                 # tous les MediaRenderers du LAN

Self-test live : python -m rn301.discover [timeout_sec]

100 % stdlib.
"""
from __future__ import annotations

import json
import socket
import struct
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
# deux ST : certains firmwares ne répondent qu'à l'un des deux
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "upnp:rootdevice",
)
DEFAULT_MODEL = "R-N301"
DEFAULT_MANUFACTURER = "Yamaha"

_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: {st}\r\n"
    "\r\n"
)


class DiscoverError(Exception):
    """Erreur de découverte (réseau, desc.xml illisible…)."""


# ---------------------------------------------------------------------------
# parsing (fonctions pures — testables hors-ligne sur amp/upnp_desc.xml)
# ---------------------------------------------------------------------------

def _ln(tag: str) -> str:
    """Localname d'un tag ElementTree : '{urn:…}modelName' → 'modelName'."""
    return tag.split("}")[-1]


def parse_ssdp_response(data: bytes) -> dict[str, str]:
    """Réponse UDP M-SEARCH → {'location', 'server', 'st', 'usn'} (clés basses)."""
    head = data.decode("utf-8", "replace")
    out: dict[str, str] = {}
    for line in head.split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def parse_device_desc(xml: bytes | str, base_url: str = "") -> dict:
    """desc.xml MediaRenderer → dict plat, URLs relatives résolues contre base_url.

    Insensible aux namespaces (yamaha:, dlna:, pnpx:… varient selon les
    firmwares) : tout est lu par localname.
    """
    if isinstance(xml, str):
        xml = xml.encode("utf-8", "replace")
    root = ET.fromstring(xml)

    out: dict = {
        "icons": [], "services": [],
        "ync_url": None, "ync_host": None,
    }
    device = None
    for el in root.iter():
        name = _ln(el.tag)
        if name == "device" and device is None:
            device = el                                  # premier <device> = racine
        elif name == "X_URLBase":
            base = (el.text or "").strip()
            if base:
                out["ync_host"] = urlparse(base).hostname
        elif name == "X_controlURL" and out["ync_url"] is None:
            ctrl = (el.text or "").strip()
            urlbase = next((b.text for b in root.iter() if _ln(b.tag) == "X_URLBase"), "")
            if ctrl and urlbase:
                out["ync_url"] = urlbase.strip().rstrip("/") + ctrl

    simple = {
        "device_type": "deviceType", "friendly_name": "friendlyName",
        "manufacturer": "manufacturer", "model": "modelName",
        "model_number": "modelNumber", "model_description": "modelDescription",
        "serial": "serialNumber", "udn": "UDN", "presentation_url": "presentationURL",
    }
    # <device> est dans le namespace par défaut urn:schemas-upnp-org:device-1-0
    # → find("modelName") ne matcherait pas ; lecture par localname, première
    # occurrence dans l'ordre du document.
    texts: dict[str, str] = {}
    if device is not None:
        for el in device.iter():
            texts.setdefault(_ln(el.tag), (el.text or "").strip())
    for key, tag in simple.items():
        out[key] = texts.get(tag) or None

    if device is not None:
        for icon in device.iter():
            if _ln(icon.tag) != "icon":
                continue
            url = next((c.text for c in icon if _ln(c.tag) == "url"), None)
            if url:
                out["icons"].append({
                    "url": urljoin(base_url, url.strip()),
                    "mimetype": next((c.text for c in icon if _ln(c.tag) == "mimetype"), ""),
                    "width": next((c.text for c in icon if _ln(c.tag) == "width"), ""),
                })
        for svc in device.iter():
            if _ln(svc.tag) != "service":
                continue
            get = lambda t: next((c.text for c in svc if _ln(c.tag) == t), None)
            if get("serviceType"):
                out["services"].append({
                    "type": get("serviceType"),
                    "control_url": urljoin(base_url, get("controlURL") or ""),
                    "event_url": urljoin(base_url, get("eventSubURL") or ""),
                    "scpd_url": urljoin(base_url, get("SCPDURL") or ""),
                })
    return out


# ---------------------------------------------------------------------------
# scan SSDP
# ---------------------------------------------------------------------------

def _ssdp_search(timeout: float) -> dict[str, dict]:
    """M-SEARCH multicast → {location: {'server', 'usn', 'st'}} dédoublonné.

    Garde-fou mesuré sur le matériel : la LOCATION doit pointer sur la même IP
    que l'émetteur du datagramme (sinon artefacts multi-homing/proxy).

    Lève DiscoverError si le socket ne peut pas être ouvert/émis (pas de
    multicast, pare-feu…) : l'appelant distingue ainsi « réseau HS » de
    « aucun ampli trouvé » (liste vide).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2))
            sock.bind(("", 0))
            for st in SEARCH_TARGETS:
                sock.sendto(_MSEARCH.format(st=st).encode(), (SSDP_ADDR, SSDP_PORT))
        except OSError as e:
            raise DiscoverError(f"socket SSDP indisponible : {e}") from e
        found: dict[str, dict] = {}
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            except OSError:
                continue
            resp = parse_ssdp_response(data)
            location = resp.get("location", "")
            if not location:
                continue
            host = urlparse(location).hostname
            if host != addr[0]:                      # LOCATION ≠ émetteur : suspect
                continue
            found.setdefault(location, {
                "server": resp.get("server", ""),
                "usn": resp.get("usn", ""),
                "st": resp.get("st", ""),
            })
    finally:
        sock.close()
    return found


def _fetch_desc(url: str, timeout: float = 5.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def discover(timeout: float = 4.5, model: str | None = DEFAULT_MODEL,
             manufacturer: str | None = DEFAULT_MANUFACTURER,
             fetch_timeout: float = 5.0) -> list[dict]:
    """Scanne le LAN et renvoie la liste des appareils correspondants.

    Chaque entrée : host, renderer_port, model, friendly_name, udn, serial,
    ync_url, ync_host, presentation_url, icons, services, location, server.

    model=None → tous les modèles ; manufacturer=None → toutes marques.
    """
    results: list[dict] = []
    seen_udn: set[str] = set()
    for location, meta in _ssdp_search(timeout).items():
        try:
            desc = parse_device_desc(_fetch_desc(location, fetch_timeout), location)
        except Exception:                            # desc.xml illisible/injoignable
            continue
        if manufacturer and manufacturer.lower() not in (desc.get("manufacturer") or "").lower():
            continue
        if model and (desc.get("model") or "") != model:
            continue
        udn = desc.get("udn") or location
        if udn in seen_udn:                            # même ampli, 2ᵉ LOCATION
            continue
        seen_udn.add(udn)
        parsed = urlparse(location)
        results.append({
            "host": parsed.hostname,
            "renderer_port": parsed.port or 80,
            **desc,
            "location": location,
            "server": meta.get("server", ""),
        })
    return results


def discover_one(timeout: float = 4.5, model: str | None = DEFAULT_MODEL,
                 **kw) -> dict | None:
    """Le premier appareil trouvé (None si aucun). Raccourci le plus courant :

        found = discover_one()
        amp = RN301(found["host"]) if found else None
    """
    found = discover(timeout=timeout, model=model, **kw)
    return found[0] if found else None


# ---------------------------------------------------------------------------
# self-test live : python -m rn301.discover [timeout]
# ---------------------------------------------------------------------------

def _main() -> None:
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 4.5
    print(f"scan SSDP ({timeout} s, MediaRenderer + rootdevice)…", flush=True)
    found = discover(timeout=timeout)
    if not found:
        print("aucun R-N301 trouvé (même réseau ? pare-feu multicast ?)")
        return
    print(json.dumps(found, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    _main()
