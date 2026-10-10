"""Verrous par ampli — le R-N301 ne traite qu'une requête à la fois.

Quirk firmware : toute requête concurrente (YNC ou SOAP) tombe sur un ampli
« occupé » → HTTP 400 / RC=3. Dès que plusieurs objets parlent au même ampli
(RN301 + Renderer + webapp + scripts…), il faut sérialiser côté client.

Un seul verrou par ampli (clé = IP/hôte normalisé), partagé entre le client
YNC (RN301._request) et le client SOAP AVTransport (Renderer._soap) : les deux
emplacent la même machine. Réentrant (RLock) pour qu'un objet puisse rappeler
une méthode verrouillée sans se bloquer lui-même.

Le client GENA (gena.py) n'a PAS besoin de verrou : SUBSCRIBE/UNSUBSCRIBE sont
des requêtes UPnP indépendantes et les NOTIFY arrivent en push (l'ampli ne
« subit » pas de requête d'état).
"""
from __future__ import annotations

import threading

_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()


def _normalize(host: str) -> str:
    """'http://192.168.1.91:80/x' / '192.168.1.91' → '192.168.1.91'."""
    return host.split("//")[-1].split("/")[0].split(":")[0].strip().lower()


def amp_lock(host: str) -> threading.RLock:
    """Verrou unique d'un ampli : même hôte (quelle que soit sa forme) → même lock."""
    key = _normalize(host)
    with _GUARD:
        return _LOCKS.setdefault(key, threading.RLock())
