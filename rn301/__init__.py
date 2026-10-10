"""Librairie Python pour l'ampli Yamaha R-N301 (protocole YNC).

100 % stdlib. Voir docs/YNC_COMMANDS.md et docs/RESEARCH.md pour le détail
des commandes validées en live.

Usage:
    from rn301 import RN301
    amp = RN301("192.168.1.91")
    amp.power_on()
    amp.set_volume(55)
    print(amp.basic_status())

Push DLNA direct (envoyer n'importe quel audio au renderer :8080) :
    from rn301 import PushServer
    push = PushServer("192.168.1.91")
    push.push_file("morceau.wav", title="Mon morceau")

Eventing GENA (volume/mute + transport du push, notifiés par l'ampli) :
    from rn301 import GenaClient
    gena = GenaClient("192.168.1.91")
    gena.subscribe("RenderingControl", lambda svc, changed: print(changed))

Découverte SSDP (trouver l'ampli sans IP fixe) :
    from rn301 import discover_one, RN301
    found = discover_one()
    amp = RN301(found["host"]) if found else None

Installation depuis un autre projet : `uv add ../rn301` (path dependency
editable), `pip install -e ../rn301`, ou git/PyPI une fois publié.
"""

__version__ = "0.1.0"

from .rn301 import RN301, YNCError, SOURCE_TAGS, REMOTE_KEYS
from .push import PushServer, PushError, Renderer, FileServer, didl_metadata
from .gena import GenaClient, GenaError, parse_propertyset
from .discover import discover, discover_one, parse_device_desc, DiscoverError

__all__ = ["__version__",
           "RN301", "YNCError", "SOURCE_TAGS", "REMOTE_KEYS",
           "PushServer", "PushError", "Renderer", "FileServer", "didl_metadata",
           "GenaClient", "GenaError", "parse_propertyset",
           "discover", "discover_one", "parse_device_desc", "DiscoverError"]
