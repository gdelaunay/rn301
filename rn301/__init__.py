"""Librairie Python pour l'ampli Yamaha R-N301 (protocole YNC).

100 % stdlib. Voir docs/YNC_COMMANDS.md et docs/RESEARCH.md pour le détail
des commandes validées en live.

Usage:
    from rn301 import RN301
    amp = RN301("192.168.1.91")
    amp.power_on()
    amp.set_volume(55)
    print(amp.basic_status())
"""
from .rn301 import RN301, YNCError, SOURCE_TAGS, REMOTE_KEYS

__all__ = ["RN301", "YNCError", "SOURCE_TAGS", "REMOTE_KEYS"]
