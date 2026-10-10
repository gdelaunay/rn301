"""Tests hors-ligne des événements GENA de PushServer (on_transport / wait_stopped).

Réutilise le faux renderer UPnP de test_gena.py : PushServer croit parler à un
ampli (:8080 SOAP/SUBSCRIBE) alors que tout reste en localhost.

Lancer : python -m unittest discover tests -v
"""
from __future__ import annotations

import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from rn301 import PushServer
from test_gena import _FakeRenderer, notify, AVT_STOPPED

AVT_PLAYING = AVT_STOPPED.replace("STOPPED", "PLAYING")


class PushEventsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRenderer)
        cls.renderer.sids = set()
        cls.renderer.renews = []
        cls.renderer.unsubscribed = []
        threading.Thread(target=cls.renderer.serve_forever, daemon=True).start()
        cls.port = cls.renderer.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.renderer.shutdown()
        cls.renderer.server_close()

    def make_push(self) -> PushServer:
        return PushServer("127.0.0.1", renderer_port=self.port)

    def wait_subscribed(self, push: PushServer, timeout: float = 3.0):
        """Attend la fin de l'abonnement paresseux (thread de wait_stopped)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if push._gena is not None and push._gena.is_subscribed("AVTransport"):
                return push._gena._subs["AVTransport"].sid
            time.sleep(0.05)
        raise AssertionError("abonnement GENA jamais arrivé")

    # --- paresse : rien d'abonné tant qu'on ne demande rien -----------------

    def test_aucun_gena_sans_usage(self):
        push = self.make_push()
        self.assertIsNone(push._gena)             # pas de GenaClient créé
        self.assertIsNone(push.transport_state())
        push.close()                              # close() sans gena = no-op propre

    # --- on_transport -------------------------------------------------------

    def test_on_transport(self):
        push = self.make_push()
        events: list[tuple] = []
        push.on_transport(lambda state, changed: events.append((state, changed)))
        sid = push._gena._subs["AVTransport"].sid
        cb = push._gena.callback_url

        notify(cb, sid, 0, AVT_PLAYING)
        notify(cb, sid, 1, AVT_STOPPED)
        time.sleep(0.2)

        self.assertEqual(events[0][0], "PLAYING")
        self.assertEqual(events[1], ("STOPPED", {"TransportState": {"val": "STOPPED"},
                                                 "TransportStatus": {"val": "OK"}}))
        self.assertEqual(push.transport_state(), "STOPPED")
        push.close()
        self.assertIn(sid, self.renderer.unsubscribed)   # UNSUBSCRIBE propre

    def test_on_transport_reabonne_remplace(self):
        push = self.make_push()
        first, second = [], []
        push.on_transport(lambda s, c: first.append(s))
        push.on_transport(lambda s, c: second.append(s))   # remplace
        sid = push._gena._subs["AVTransport"].sid
        notify(push._gena.callback_url, sid, 0, AVT_PLAYING)
        time.sleep(0.2)
        self.assertEqual(first, [])
        self.assertEqual(second, ["PLAYING"])
        push.close()

    # --- wait_stopped -------------------------------------------------------

    def test_wait_stopped_deux_phases(self):
        push = self.make_push()
        results: list[bool] = []
        t = threading.Thread(target=lambda: results.append(push.wait_stopped(timeout=5)),
                             daemon=True)
        t.start()
        sid = self.wait_subscribed(push)
        cb = push._gena.callback_url
        notify(cb, sid, 0, AVT_STOPPED)          # vieux STOPPED : ne doit PAS finir
        time.sleep(0.3)
        self.assertEqual(results, [])            # toujours armé (pas encore PLAYING)
        notify(cb, sid, 1, AVT_PLAYING)          # armement
        time.sleep(0.3)
        self.assertEqual(results, [])            # lecture en cours : attend la fin
        notify(cb, sid, 2, AVT_STOPPED)          # fin réelle
        t.join(timeout=5)
        self.assertEqual(results, [True])
        push.close()

    def test_wait_stopped_deja_arrete(self):
        # piste déjà finie avant l'appel : l'armement expire vite puis True direct
        push = self.make_push()
        push.on_transport(lambda s, c: None)     # abonne (état vide)
        sid = push._gena._subs["AVTransport"].sid
        notify(push._gena.callback_url, sid, 0, AVT_STOPPED)
        time.sleep(0.2)
        t0 = time.monotonic()
        self.assertTrue(push.wait_stopped(timeout=2, arm_timeout=0.5))
        self.assertLess(time.monotonic() - t0, 1.5)
        push.close()

    def test_wait_stopped_timeout(self):
        push = self.make_push()
        push.on_transport(lambda s, c: None)
        sid = push._gena._subs["AVTransport"].sid
        notify(push._gena.callback_url, sid, 0, AVT_PLAYING)   # armé, joue…
        t0 = time.monotonic()
        self.assertFalse(push.wait_stopped(timeout=0.5))       # …jamais fini
        self.assertGreaterEqual(time.monotonic() - t0, 0.4)
        push.close()


if __name__ == "__main__":
    unittest.main()
