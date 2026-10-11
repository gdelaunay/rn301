"""Test live: push DLNA direct vers le MediaRenderer du R-N301 (:8080).

Sert un fichier en HTTP sur le LAN, puis SOAP SetAVTransportURI + Play.

Usage:
    python tools/test_push.py <fichier> [--ip 192.168.1.32] [--amp 192.168.1.91] [--port 9000]
"""
import argparse
import http.server
import os
import socket
import threading
import time
import urllib.request
import xml.sax.saxutils as sx

AMP_DEFAULT = "192.168.1.91"
RENDERER_PORT = 8080

SOAP_ENV = ('<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
            'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>%s</s:Body></s:Envelope>')


def soap(amp: str, action: str, body: str) -> str:
    url = f"http://{amp}:{RENDERER_PORT}/AVTransport/ctrl"
    req = urllib.request.Request(
        url,
        data=(body if body.startswith("<?xml") else SOAP_ENV % body).encode("utf-8"),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"urn:schemas-upnp-org:service:AVTransport:1#{action}"',
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", "replace")


def transport_state(amp: str) -> str:
    resp = soap(amp, "GetTransportInfo",
                '<u:GetTransportInfo xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
                '<InstanceID>0</InstanceID></u:GetTransportInfo>')
    import re
    m = re.search(r"<CurrentTransportState>([^<]*)</CurrentTransportState>", resp)
    s = re.search(r"<CurrentTransportStatus>([^<]*)</CurrentTransportStatus>", resp)
    return f"{m.group(1) if m else '?'} / {s.group(1) if s else '?'}"


def didl(uri: str, mime: str, title: str, size: int) -> str:
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
        f'<item id="1" parentID="0" restricted="1">'
        f'<dc:title>{sx.escape(title)}</dc:title><upnp:class>object.item.audioItem.musicTrack</upnp:class>'
        f'<res protocolInfo="http-get:*:{mime}:DLNA.ORG_OP=01;'
        f'DLNA.ORG_FLAGS=01700000000000000000000000000000" size="{size}">{sx.escape(uri)}</res>'
        '</item></DIDL-Lite>'
    )


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"  [http] {self.address_string()} {fmt % args}", flush=True)


def guess_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
            ".wma": "audio/x-ms-wma", ".m4a": "audio/mp4", ".aac": "audio/mp4",
            ".ogg": "audio/ogg"}.get(ext, "application/octet-stream")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--ip", default=None)
    ap.add_argument("--amp", default=AMP_DEFAULT)
    ap.add_argument("--port", type=int, default=9000)
    args = ap.parse_args()

    f = os.path.abspath(args.file)
    servedir = os.path.dirname(f)
    name = os.path.basename(f)

    if args.ip:
        ip = args.ip
    else:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((args.amp, 80))
        ip = s.getsockname()[0]
        s.close()

    os.chdir(servedir)
    httpd = http.server.ThreadingHTTPServer((ip, args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    uri = f"http://{ip}:{args.port}/{name}"
    print(f"serveur HTTP: {servedir} sur {ip}:{args.port}")
    print(f"URI poussée : {uri}")

    print("état avant  :", transport_state(args.amp))

    meta = sx.escape(didl(uri, guess_mime(f), os.path.splitext(name)[0], os.path.getsize(f)))
    body = ('<u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
            f'<InstanceID>0</InstanceID><CurrentURI>{sx.escape(uri)}</CurrentURI>'
            f'<CurrentURIMetaData>{meta}</CurrentURIMetaData></u:SetAVTransportURI>')
    print("SetAVTransportURI ->", soap(args.amp, "SetAVTransportURI", body)[:300])
    print("état après set:", transport_state(args.amp))

    print("Play ->", soap(args.amp, "Play",
          '<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
          '<InstanceID>0</InstanceID><Speed>1</Speed></u:Play>')[:300])
    time.sleep(2)
    print("état après play:", transport_state(args.amp))

    print("\n>>> lecture en cours, serveur actif 45 s (Ctrl+C pour arrêter avant)")
    try:
        time.sleep(45)
    except KeyboardInterrupt:
        pass
    print("état final:", transport_state(args.amp))
    httpd.shutdown()


if __name__ == "__main__":
    main()
