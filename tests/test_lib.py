"""Tests hors-ligne de la lib (rn301.py + push.py) — aucun ampli requis.

Couvre :
  * clamp volume : échelle protocole 0..100 par défaut, plafond logiciel
    optionnel via RN301(max_volume=N) ;
  * verrou partagé par ampli (YNC + SOAP sérialisés) ;
  * serveur de fichiers push (pochette sur /albumart, range requests).

Lancer : python -m unittest discover tests -v
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from rn301 import RN301
from rn301._locks import amp_lock
from rn301.push import FileServer


class _CaptureYNC(RN301):
    """RN301 qui n'ouvre jamais de socket : capture le XML des requêtes."""

    def __init__(self, **kw):
        super().__init__("127.0.0.1", **kw)
        self.sent: list[tuple[str, str]] = []

    def _request(self, cmd, xml):
        self.sent.append((cmd, xml))
        return None


class VolumeClampTest(unittest.TestCase):
    def _xml_val(self, xml: str) -> int:
        import re
        return int(re.search(r"<Val>(\d+)</Val>", xml).group(1))

    def test_defaut_echelle_protocole(self):
        amp = _CaptureYNC()
        amp.set_volume(100)
        self.assertEqual(self._xml_val(amp.sent[-1][1]), 100)   # pas bridé à 80
        amp.set_volume(150)
        self.assertEqual(self._xml_val(amp.sent[-1][1]), 100)   # clampé à l'échelle
        amp.set_volume(-5)
        self.assertEqual(self._xml_val(amp.sent[-1][1]), 0)

    def test_max_volume_logiciel(self):
        amp = _CaptureYNC(max_volume=45)
        self.assertEqual(amp.set_volume(80), 45)                # renvoie l'effectif
        self.assertEqual(self._xml_val(amp.sent[-1][1]), 45)

    def test_volume_up_down_avec_plafond(self):
        amp = _CaptureYNC(max_volume=45)
        amp.basic_status = lambda: {"volume": 40}               # lecture simulée
        self.assertEqual(amp.volume_up(10), 45)                 # 40+10 clampé à 45
        self.assertEqual(amp.volume_down(100), 0)

    def test_verrou_partage_meme_ampli(self):
        amp1, amp2 = RN301("192.168.1.91"), RN301("http://192.168.1.91")
        self.assertIs(amp1._lock, amp2._lock)                   # même clé normalisée
        self.assertIsNot(amp1._lock, RN301("192.168.1.92")._lock)
        from rn301.push import Renderer
        self.assertIs(Renderer("192.168.1.91")._lock, amp1._lock)   # YNC + SOAP

    def test_verrou_serialise(self):
        lock = amp_lock("serial.test")
        order = []

        def worker(n):
            with lock:
                order.append(("in", n))
                threading.Event().wait(0.05)
                order.append(("out", n))

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        # aucune interleave : in/out toujours par paires adjacentes
        for i in range(0, 6, 2):
            self.assertEqual(order[i][1], order[i + 1][1])


class FileServerCoverTest(unittest.TestCase):
    """Vérifie que /albumart sert bien la pochette stockée sur le FileServer."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        self.tmp.write(b"RIFFxxxxWAVEfmt " + b"\x00" * 64)
        self.tmp.close()
        self.srv = FileServer("127.0.0.1", 0)
        self.srv.start()
        self.base = f"http://127.0.0.1:{self.srv.port}"

    def tearDown(self):
        self.srv.stop()
        os.unlink(self.tmp.name)

    def test_albumart(self):
        self.srv.set_cover(b"\x89PNG-fake", "image/png")
        with urllib.request.urlopen(f"{self.base}/albumart/1") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertEqual(r.read(), b"\x89PNG-fake")

    def test_albumart_404_sans_pochette(self):
        self.srv.set_cover(None)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.base}/albumart/1")
        self.assertEqual(ctx.exception.code, 404)

    def test_fichier_et_range(self):
        self.srv.set_file(self.tmp.name)
        with open(self.tmp.name, "rb") as f:
            data = f.read()
        with urllib.request.urlopen(f"{self.base}/audio/1/") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.read(), data)
            self.assertEqual(r.headers["Accept-Ranges"], "bytes")
        req = urllib.request.Request(f"{self.base}/audio/1/", headers={"Range": "bytes=4-7"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 206)
            self.assertEqual(r.read(), data[4:8])

    def test_set_file_avant_start(self):
        srv = FileServer("127.0.0.1", 0)
        srv.set_file(self.tmp.name)          # allowed before start()
        srv.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/x") as r:
                self.assertEqual(r.status, 200)
        finally:
            srv.stop()


if __name__ == "__main__":
    unittest.main()
