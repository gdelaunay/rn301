"""Tests hors-ligne du client GENA (rn301/gena.py).

Un faux renderer UPnP (SUBSCRIBE/UNSUBSCRIBE + NOTIFY forgés avec les payloads
réels mesurés en live le 2025-10-10, voir docs/DLNA_PUSH.md §Eventing GENA)
valide : parsing du LastChange double-échappé, état accumulé (delta), ordre des
attributs (channel avant val), trous de SEQ, wait_for, renouvellement/fermeture.

Lancer : python -m unittest discover tests -v
"""
from __future__ import annotations

import http.client
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rn301.gena import GenaClient, parse_propertyset

# ---------------------------------------------------------------- payloads mesurés

# NOTIFY initial RenderingControl (SEQ=0) : état complet, Volume channel AVANT val
RC_INITIAL = (
    '<?xml version="1.0"?>'
    '<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
    '<e:property><LastChange>'
    '&lt;Event xmlns=&quot;urn:schemas-upnp-org:metadata-1-0/RCS/&quot;&gt;'
    '&lt;InstanceID val=&quot;0&quot;&gt;'
    '&lt;PresetNameList val=&quot;&quot;/&gt;'
    '&lt;Mute channel=&quot;Master&quot; val=&quot;0&quot;/&gt;'
    '&lt;Volume channel=&quot;Master&quot; val=&quot;50&quot;/&gt;'
    '&lt;/InstanceID&gt;&lt;/Event&gt;'
    '</LastChange></e:property></e:propertyset>'
)

# event incrémental : SEULE la variable modifiée (piège n°1)
RC_VOLUME = (
    '<?xml version="1.0"?>'
    '<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
    '<e:property><LastChange>'
    '&lt;Event xmlns=&quot;urn:schemas-upnp-org:metadata-1-0/RCS/&quot;&gt;'
    '&lt;InstanceID val=&quot;0&quot;&gt;'
    '&lt;Volume channel=&quot;Master&quot; val=&quot;52&quot;/&gt;'
    '&lt;/InstanceID&gt;&lt;/Event&gt;'
    '</LastChange></e:property></e:propertyset>'
)

RC_MUTE = RC_VOLUME.replace('Volume channel=&quot;Master&quot; val=&quot;52&quot;',
                            'Mute channel=&quot;Master&quot; val=&quot;1&quot;')

AVT_STOPPED = (
    '<?xml version="1.0"?>'
    '<e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
    '<e:property><LastChange>'
    '&lt;Event xmlns=&quot;urn:schemas-upnp-org:metadata-1-0/AVT/&quot;&gt;'
    '&lt;InstanceID val=&quot;0&quot;&gt;'
    '&lt;TransportState val=&quot;STOPPED&quot;/&gt;'
    '&lt;TransportStatus val=&quot;OK&quot;/&gt;'
    '&lt;/InstanceID&gt;&lt;/Event&gt;'
    '</LastChange></e:property></e:propertyset>'
)


# ---------------------------------------------------------------- faux renderer

class _FakeRenderer(BaseHTTPRequestHandler):
    """Renderer UPnP minimal : honore SUBSCRIBE/UNSUBSCRIBE, mémorise les SID."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_SUBSCRIBE(self):
        sid = self.headers.get("SID")
        if sid and sid in self.server.sids:          # renouvellement
            self.server.renews.append(sid)
        else:                                        # nouvel abonnement
            sid = f"uuid:fake-{len(self.server.sids) + 1}"
            self.server.sids.add(sid)
        self.send_response(200)
        self.send_header("SID", sid)
        self.send_header("TIMEOUT", "Second-300")    # plafond mesuré du firmware
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_UNSUBSCRIBE(self):
        sid = self.headers.get("SID")
        self.server.unsubscribed.append(sid)
        self.server.sids.discard(sid)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def notify(callback_url: str, sid: str, seq: int, body: str,
           nts: str = "upnp:propchange") -> None:
    """Envoie un NOTIFY au callback (comme l'ampli). callback_url = '<http://ip:port/cb>'."""
    url = callback_url.strip("<>")
    host, rest = url.split("://", 1)[1].split(":", 1)
    conn = http.client.HTTPConnection(host, int(rest.split("/", 1)[0]), timeout=5)
    conn.request("NOTIFY", "/" + rest.split("/", 1)[1], body=body.encode(),
                 headers={"HOST": f"{host}:{rest.split('/')[0]}",
                          "CONTENT-TYPE": 'text/xml; charset="utf-8"',
                          "NT": "upnp:event", "NTS": nts,
                          "SID": sid, "SEQ": str(seq)})
    rsp = conn.getresponse()
    rsp.read()
    conn.close()
    assert rsp.status == 200, f"le callback doit répondre 200, pas {rsp.status}"


class GenaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRenderer)
        cls.renderer.sids = set()
        cls.renderer.renews = []
        cls.renderer.unsubscribed = []
        threading.Thread(target=cls.renderer.serve_forever, daemon=True).start()
        cls.amp = "127.0.0.1"
        cls.port = cls.renderer.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.renderer.shutdown()
        cls.renderer.server_close()

    # --- parsing pur -------------------------------------------------------

    def test_parse_initial_complet(self):
        st = parse_propertyset(RC_INITIAL)
        self.assertEqual(st["Volume"], {"channel": "Master", "val": "50"})
        self.assertEqual(st["Mute"], {"channel": "Master", "val": "0"})
        self.assertIn("PresetNameList", st)

    def test_parse_incremental(self):
        st = parse_propertyset(RC_VOLUME)
        self.assertEqual(st, {"Volume": {"channel": "Master", "val": "52"}})

    def test_parse_str_et_bytes(self):
        self.assertEqual(parse_propertyset(AVT_STOPPED.encode()),
                         parse_propertyset(AVT_STOPPED))

    # --- bout en bout avec le faux renderer --------------------------------

    def test_cycle_complet(self):
        events: list[tuple[str, dict]] = []
        gena = GenaClient(self.amp, port=self.port)
        sid_rc = gena.subscribe("RenderingControl",
                                lambda svc, ch: events.append((svc, ch)))
        sid_avt = gena.subscribe("AVTransport")
        cb = gena.callback_url or ""

        # 1. NOTIFY initial (SEQ=0) -> état complet accumulé
        notify(cb, sid_rc, 0, RC_INITIAL)
        notify(cb, sid_avt, 0, AVT_STOPPED.replace("STOPPED", "NO_MEDIA_PRESENT"))

        # 2. delta Volume (SEQ=1) : l'état accumulé garde Mute/PresetNameList
        notify(cb, sid_rc, 1, RC_VOLUME)
        st = gena.state("RenderingControl")
        self.assertEqual(st["Volume"]["val"], "52")
        self.assertEqual(st["Mute"]["val"], "0")            # pas écrasé (delta)
        self.assertEqual(gena.var("RenderingControl", "Volume")["val"], "52")

        # 3. trou de SEQ (1 -> 4) : 2 events perdus comptés
        notify(cb, sid_rc, 4, RC_MUTE)
        self.assertEqual(gena.missed("RenderingControl"), 2)
        self.assertEqual(gena.var("RenderingControl", "Mute")["val"], "1")

        # 4. callback utilisateur appelé sur chaque NOTIFY utile
        self.assertTrue(any(svc == "RenderingControl" and "Volume" in ch
                            for svc, ch in events))

        # 5. wait_for se débloque sur l'event transport
        done = threading.Thread(target=lambda: results.append(
            gena.wait_for("AVTransport",
                          lambda s: s.get("TransportState", {}).get("val") == "STOPPED",
                          timeout=5)), daemon=True)
        results: list[bool] = []
        done.start()
        time.sleep(0.2)
        notify(cb, sid_avt, 1, AVT_STOPPED)
        done.join(timeout=5)
        self.assertEqual(results, [True])

        # 6. wait_for expire proprement
        self.assertFalse(gena.wait_for("AVTransport", lambda s: False, timeout=0.3))

        # 7. close() -> UNSUBSCRIBE des deux SID, NOTIFY d'un vieux SID ignoré
        gena.close()
        self.assertIn(sid_rc, self.renderer.unsubscribed)
        self.assertIn(sid_avt, self.renderer.unsubscribed)
        self.assertEqual(gena.state("RenderingControl"), {})

    def test_service_inconnu(self):
        gena = GenaClient(self.amp, port=self.port)
        with self.assertRaises(ValueError):
            gena.subscribe("YamahaRemoteControl")


if __name__ == "__main__":
    unittest.main()
