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

__version__ = "1.0.0"

from .discover import DiscoverError, discover, discover_one, parse_device_desc
from .gena import GenaClient, GenaError, parse_propertyset
from .push import FileServer, PushError, PushServer, Renderer, didl_metadata
from .rn301 import REMOTE_KEYS, RN301, SOURCE_TAGS, YNCError

__all__ = [
           "REMOTE_KEYS",
           "RN301",
           "SOURCE_TAGS",
           "DiscoverError",
           "FileServer",
           "GenaClient",
           "GenaError",
           "PushError",
           "PushServer",
           "Renderer",
           "YNCError",
           "__version__",
           "didl_metadata",
           "discover",
           "discover_one",
           "parse_device_desc",
           "parse_propertyset",
]
