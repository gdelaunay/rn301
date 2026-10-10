"""Tests hors-ligne de la découverte SSDP (rn301/discover.py).

Le parsing est validé sur le VRAI desc.xml de l'ampli, archivé pendant le
reverse (amp/upnp_desc.xml) — pas de payload synthétique. Le scan multicast
lui-même ne se teste pas hors-ligne (self-test live : python -m rn301.discover).

Lancer : python -m unittest discover tests -v
"""
from __future__ import annotations

import os
import unittest

from rn301.discover import parse_device_desc, parse_ssdp_response

DESC_PATH = os.path.join(os.path.dirname(__file__), "..", "amp", "upnp_desc.xml")
LOCATION = "http://192.168.1.91:8080/MediaRenderer/desc.xml"


class ParseDeviceDescTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(DESC_PATH, "rb") as f:
            cls.desc = parse_device_desc(f.read(), LOCATION)

    def test_identite(self):
        self.assertEqual(self.desc["model"], "R-N301")
        self.assertEqual(self.desc["manufacturer"], "Yamaha Corporation")
        self.assertEqual(self.desc["friendly_name"], "YAMAHA R-N301")
        self.assertEqual(self.desc["serial"], "0ED9C263")
        self.assertEqual(self.desc["udn"], "uuid:5f9ec1b3-ed59-1900-4530-00a0dec48d64")
        self.assertEqual(self.desc["device_type"],
                         "urn:schemas-upnp-org:device:MediaRenderer:1")

    def test_endpoint_ync(self):
        # la trouvaille clé du reverse : X_URLBase + X_controlURL donnent le YNC
        self.assertEqual(self.desc["ync_host"], "192.168.1.91")
        self.assertEqual(self.desc["ync_url"],
                         "http://192.168.1.91:80/YamahaRemoteControl/ctrl")

    def test_presentation_url(self):
        self.assertEqual(self.desc["presentation_url"], "http://192.168.1.91/")

    def test_icones_resolvees(self):
        urls = [i["url"] for i in self.desc["icons"]]
        self.assertIn("http://192.168.1.91:8080/BCO_device_lrg_icon.png", urls)
        self.assertTrue(all(u.startswith("http://") for u in urls))

    def test_services_resolvees(self):
        by_type = {s["type"].split(":")[-2]: s for s in self.desc["services"]}
        self.assertEqual(set(by_type), {"AVTransport", "RenderingControl", "ConnectionManager"})
        self.assertEqual(by_type["AVTransport"]["control_url"],
                         "http://192.168.1.91:8080/AVTransport/ctrl")
        self.assertEqual(by_type["AVTransport"]["event_url"],
                         "http://192.168.1.91:8080/AVTransport/evt")

    def test_str_et_bytes(self):
        with open(DESC_PATH, encoding="utf-8") as f:
            d2 = parse_device_desc(f.read(), LOCATION)
        self.assertEqual(d2["model"], self.desc["model"])


class ParseSsdpResponseTest(unittest.TestCase):
    def test_reponse_type(self):
        raw = (
            b"HTTP/1.1 200 OK\r\n"
            b"CACHE-CONTROL: max-age=1800\r\n"
            b"LOCATION: http://192.168.1.91:8080/MediaRenderer/desc.xml\r\n"
            b"SERVER: KnOS/3.2 UPnP/1.0 DMP/3.5\r\n"
            b"ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            b"USN: uuid:5f9ec1b3-ed59-1900-4530-00a0dec48d64::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            b"\r\n"
        )
        r = parse_ssdp_response(raw)
        self.assertEqual(r["location"], LOCATION)
        self.assertEqual(r["server"], "KnOS/3.2 UPnP/1.0 DMP/3.5")
        self.assertTrue(r["usn"].startswith("uuid:"))


if __name__ == "__main__":
    unittest.main()
