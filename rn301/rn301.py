"""Client YNC (Yamaha Network Control) pour le R-N301.

Quirks firmware R-N301 (Network_Player/3.1) gérés ici :
  * une seule requête (un seul chemin) par POST, sinon HTTP 400 corps vide ;
  * GET de sous-éléments souvent refusés -> GET du bloc complet + parsing ;
  * réponses vides/400 aléatoires quand l'ampli est occupé -> retry (3x, 600 ms).
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from ._locks import amp_lock

DEFAULT_HOST = "192.168.1.91"
CTRL_PATH = "/YamahaRemoteControl/ctrl"

# Échelle de volume du PROTOCOLE : 0..100. Le firmware clampe lui-même à son
# réglage « Max Volume » (configuré en façade, 80 par défaut sur cet appareil) :
# un PUT de 100 sur un ampli réglé à 80 est accepté et appliqué à 80 (validé en
# live). Aucun chemin YNC connu ne permet de LIRE ce réglage (absent des ~90 ops
# de l'app) → ne pas coder de plafond matériel ici, laisser le firmware clampler.
# RN301(max_volume=N) ajoute un plafond LOGICIEL optionnel (sécurité des
# automatisations), indépendant du matériel.
VOL_MIN, VOL_MAX, VOL_STEP = 0, 100, 1

# Source (telle qu'affichée par Input_Sel) -> tag XML YNC.
# NB: validé en live — c'est NET_RADIO (comme dans l'app, q.java), pas Net_Radio.
# Utilisé par RN301.source_tag().
SOURCE_TAGS = {
    "NET RADIO": "NET_RADIO",
    "SERVER": "SERVER",
    "Spotify": "Spotify",
    "AirPlay": "AirPlay",
    "Tuner": "Tuner",
    "iPod_USB": "iPod_USB",
    "USB": "USB",
}
# alias tolérés en entrée -> valeur Input_Sel exacte attendue par l'ampli
# NB validé en live : 'Tuner' (casse app) est REFUSÉ (RC=3), il faut 'TUNER'.
SOURCE_ALIASES = {
    "NET_RADIO": "NET RADIO",
    "NET RADIO": "NET RADIO",
    "NET RADIO ": "NET RADIO",
    "SERVER": "SERVER",
    "SPOTIFY": "Spotify",
    "AIRPLAY": "AirPlay",
    "TUNER": "TUNER",
    "IPOD_USB": "iPod_USB",
    "IPOD": "iPod_USB",
    "USB": "USB",
    "CD": "CD",
    "OPTICAL": "OPTICAL",
    "COAXIAL": "COAXIAL",
    "LINE1": "LINE1",
    "LINE2": "LINE2",
    "LINE3": "LINE3",
}

# Codes IR injectables via System/Misc/Remote_Signal (device 7F01 uniquement,
# format NEC étendu : 7F01 + OBC + ~OBC). Voir docs/YNC_COMMANDS.md.
REMOTE_KEYS = {
    "shuffle": "7F0146B9",       # OBC 0x46 — touche Shuffle (utilisée par l'app)
    "repeat": "7F0145BA",        # OBC 0x45 — touche Repeat (utilisée par l'app)
    "net_source": "7F013AC5",    # OBC 0x3A — source réseau (cycle)
    "net_cycle": "7F013FC0",     # OBC 0x3F — cycle SERVER->NET RADIO->AirPlay->Spotify
}


class YNCError(Exception):
    """Erreur de communication ou refus de l'ampli (HTTP 400, RC != 0)."""

    def __init__(self, message: str, http: int = 0, rc: str | None = None, body: str = ""):
        super().__init__(message)
        self.http = http
        self.rc = rc
        self.body = body


def _find_text(node, *path):
    """Texte du premier descendant trouvé en suivant `path`, sinon None."""
    cur = node
    for tag in path:
        if cur is None:
            return None
        cur = cur.find(tag)
    return cur.text.strip() if cur is not None and cur.text else None


def _tag(path: str) -> str:
    """'Main_Zone/Volume/Mute' -> '<Main_Zone><Volume><Mute>' + fermantes."""
    parts = path.split("/")
    open_ = "".join(f"<{p}>" for p in parts)
    close = "".join(f"</{p}>" for p in reversed(parts))
    return open_, close


class RN301:
    """Client YNC du Yamaha R-N301."""

    def __init__(self, host: str = DEFAULT_HOST, timeout: float = 5.0, retries: int = 3,
                 max_volume: int | None = None):
        self.url = f"http://{host}{CTRL_PATH}" if not host.startswith("http") else host + CTRL_PATH
        self.timeout = timeout
        self.retries = retries
        # plafond logiciel optionnel (None = échelle du protocole 0..VOL_MAX,
        # le firmware appliquant de toute façon son propre « Max Volume »)
        self.max_volume = max_volume
        # verrou partagé avec les autres clients du même ampli (voir _locks.py) :
        # le R-N301 ne traite qu'une requête à la fois, sinon RC=3
        self._lock = amp_lock(host)

    # ------------------------------------------------------------------ bas niveau

    def _raw(self, cmd: str, xml: str) -> str:
        """POST <YAMAHA_AV cmd=...>xml</YAMAHA_AV> avec retry. Renvoie le corps."""
        body = f'<YAMAHA_AV cmd="{cmd}">{xml}</YAMAHA_AV>'.encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    self.url, data=body, method="POST",
                    headers={"Content-Type": "text/xml; charset=UTF-8"},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    rsp = r.read().decode("utf-8", "replace")
                if rsp.strip():
                    return rsp
                last_err = "réponse vide"
            except urllib.error.HTTPError as e:
                # 400 = commande invalide ou ampli occupé : on retente (quirk firmware)
                last_err = f"HTTP {e.code}"
            except OSError as e:
                last_err = str(e)
            if attempt < self.retries - 1:
                time.sleep(0.6)
        raise YNCError(f"YNC {cmd} échoué après {self.retries} essais ({last_err}): {xml[:120]}")

    def _request(self, cmd: str, xml: str) -> ET.Element:
        """Requête + vérification RC="0" + parsing XML -> racine de la réponse.

        RC != 0 (ex RC=3 = ampli occupé/pas prêt) est retenté comme un échec
        transitoire : le R-N301 refuse parfois une commande valide s'il est
        sollicité ailleurs (quirk firmware documenté).

        Toute la boucle (retries compris) tient le verrou de l'ampli : aucune
        requête concurrente d'un autre client (webapp, script, Renderer…)
        ne passe pendant ce temps.
        """
        last = None
        with self._lock:
            for attempt in range(self.retries):
                rsp = self._raw(cmd, xml)
                try:
                    root = ET.fromstring(rsp)
                except ET.ParseError as e:
                    raise YNCError(f"réponse XML invalide: {e}", body=rsp)
                rc = root.get("RC", "?")
                if rc == "0":
                    return root
                last = (rc, rsp)
                if attempt < self.retries - 1:
                    # RC=3 = occupé/pas prêt. Après un changement de source réseau,
                    # l'ampli met ~4-5 s ; des retries trop rapprochés semblent
                    # entretenir l'état occupé -> délai long (validé en live).
                    time.sleep(5.0)
        raise YNCError(f"ampli a refusé la commande (RC={last[0]})", rc=last[0], body=last[1])

    def get(self, path: str) -> str | None:
        """GET d'un chemin simple : 'Main_Zone/Power_Control/Power' -> texte."""
        open_, close = _tag(path)
        root = self._request("GET", f"{open_}GetParam{close}")
        node = root
        for tag in path.split("/"):
            node = node.find(tag)
            if node is None:
                return None
        return node.text.strip() if node.text else None

    def put(self, path: str, value: str) -> None:
        """PUT d'une valeur texte simple sur un chemin."""
        open_, close = _tag(path)
        self._request("PUT", f"{open_}{value}{close}")

    def get_block(self, path: str) -> ET.Element | None:
        """GET d'un bloc complet (ex 'Main_Zone/Basic_Status') -> élément XML.

        À privilégier : le R-N301 refuse souvent les GET de sous-éléments.
        """
        open_, close = _tag(path)
        root = self._request("GET", f"{open_}GetParam{close}")
        node = root
        for tag in path.split("/"):
            node = node.find(tag)
            if node is None:
                return None
        return node

    # ------------------------------------------------------------------ état zone

    def basic_status(self) -> dict:
        """État complet Main_Zone (power + volume + mute + input + icône)."""
        node = self.get_block("Main_Zone/Basic_Status")
        if node is None:
            raise YNCError("Basic_Status absent de la réponse")
        return {
            "power": _find_text(node, "Power_Control", "Power"),
            "volume": int(_find_text(node, "Volume", "Lvl", "Val") or 0),
            "mute": _find_text(node, "Volume", "Mute"),
            "input": _find_text(node, "Input", "Input_Sel"),
            "input_title": _find_text(node, "Input", "Input_Sel_Item_Info", "Title") or None,
            "input_icon": _find_text(node, "Input", "Input_Sel_Item_Info", "Icon", "On") or None,
        }

    def get_power(self) -> str:
        # via Basic_Status : les GET de sous-éléments sont instables (quirk firmware)
        return self.basic_status()["power"] or "?"

    def set_power(self, on: bool) -> None:
        self.put("Main_Zone/Power_Control/Power", "On" if on else "Standby")

    def power_on(self) -> None:
        self.set_power(True)

    def power_off(self) -> None:
        self.set_power(False)

    def toggle_power(self) -> str:
        """Bascule On/Standby, renvoie le nouvel état."""
        new = (self.get_power() or "").lower() != "on"
        self.set_power(new)
        return "On" if new else "Standby"

    # ------------------------------------------------------------------ volume / mute

    def get_volume(self) -> int:
        # Le GET du sous-élément Volume/Lvl est instable sur ce firmware
        # (400 aléatoires) -> lecture via le bloc Basic_Status, toujours fiable.
        return self.basic_status()["volume"]

    def _clamp_volume(self, val: int) -> int:
        """Plafond logiciel (max_volume) s'il est défini, sinon échelle protocole
        0..VOL_MAX. Le firmware applique en plus son propre « Max Volume »."""
        cap = self.max_volume if self.max_volume is not None else VOL_MAX
        return max(VOL_MIN, min(cap, int(val)))

    def set_volume(self, val: int) -> int:
        """Volume absolu (échelle protocole 0..100, clampé à max_volume s'il est
        défini). Renvoie la valeur effectivement envoyée — le firmware peut
        encore la clamper à son réglage « Max Volume » (non lisible en YNC)."""
        val = self._clamp_volume(val)
        self._request(
            "PUT",
            "<Main_Zone><Volume><Lvl>"
            f"<Val>{val}</Val><Exp>0</Exp><Unit></Unit>"
            "</Lvl></Volume></Main_Zone>",
        )
        return val

    def volume_up(self, step: int = 2) -> int:
        """Volume relatif. La syntaxe 'Up N.0 dB' est REFUSÉE par ce firmware
        (RC=3, validé en live) -> lecture + calcul + écriture absolue.
        Renvoie le nouveau volume."""
        new = self._clamp_volume(self.get_volume() + step)
        self.set_volume(new)
        return new

    def volume_down(self, step: int = 2) -> int:
        new = self._clamp_volume(self.get_volume() - step)
        self.set_volume(new)
        return new

    def get_mute(self) -> str:
        # idem get_volume : passer par Basic_Status (quirk firmware)
        return self.basic_status()["mute"] or "?"

    def set_mute(self, on: bool) -> None:
        self.put("Main_Zone/Volume/Mute", "On" if on else "Off")

    def toggle_mute(self) -> str:
        new = (self.get_mute() or "").lower() != "on"
        self.set_mute(new)
        return "On" if new else "Off"

    # ------------------------------------------------------------------ input

    def get_input(self) -> str:
        # via Basic_Status : les GET de sous-éléments sont instables (quirk firmware)
        return self.basic_status()["input"] or "?"

    def set_input(self, source: str) -> None:
        """source: 'SERVER', 'NET RADIO', 'Spotify', 'AirPlay', 'TUNER', 'CD',
        'OPTICAL', 'COAXIAL', 'LINE1'..'LINE3' (voir list_inputs)."""
        source = SOURCE_ALIASES.get(source.upper().strip(), source)
        self.put("Main_Zone/Input/Input_Sel", source)

    def list_inputs(self) -> list[dict]:
        """Les 11 entrées avec titre + icône (GET du bloc Input_Sel_Item)."""
        node = self.get_block("Main_Zone/Input/Input_Sel_Item")
        if node is None:
            return []
        out = []
        for item in node.iter():
            if not item.tag.startswith("Item_"):
                continue
            out.append({
                "value": _find_text(item, "Param"),
                "rw": _find_text(item, "RW"),
                "title": _find_text(item, "Title") or "",
                "icon": _find_text(item, "Icon", "On"),
                "src_name": _find_text(item, "Src_Name") or None,
            })
        return out

    # ------------------------------------------------------------------ système

    def system_config(self) -> dict:
        node = self.get_block("System/Config")
        if node is None:
            raise YNCError("System/Config absent")
        feats = node.find("Feature_Existence")
        return {
            "model": _find_text(node, "Model_Name"),
            "system_id": _find_text(node, "System_ID"),
            "version": _find_text(node, "Version"),
            "features": {c.tag: c.text for c in feats} if feats is not None else {},
        }

    def system_power(self, on: bool) -> None:
        """Power global système (toutes zones)."""
        self.put("System/Power_Control/Power", "On" if on else "Standby")

    def get_sleep(self) -> str | None:
        """Non supporté par le firmware R-N301 (HTTP 400, validé en live)."""
        return self.get("System/Config/Sleep")

    def set_sleep(self, minutes) -> None:
        """Non supporté par le firmware R-N301 (validé en live). Lève YNCError."""
        val = "Off" if str(minutes).lower() in ("off", "0", "none") else str(int(minutes))
        self.put("System/Config/Sleep", val)

    def get_network_standby(self) -> str | None:
        # chemin réel R-N301 : System/Misc/Network (pas System/Config)
        return self.get("System/Misc/Network/Network_Standby")

    def set_network_standby(self, on: bool) -> None:
        self.put("System/Misc/Network/Network_Standby", "On" if on else "Off")

    def get_network_name(self) -> str | None:
        return self.get("System/Misc/Network/Network_Name")

    def set_network_name(self, name: str) -> None:
        self.put("System/Misc/Network/Network_Name", name)

    def get_gapless(self) -> str | None:
        """Non supporté par le firmware R-N301 (validé en live)."""
        return self.get("System/Config/Gapless_Playback")

    def set_gapless(self, on: bool) -> None:
        """Non supporté par le firmware R-N301 (validé en live). Lève YNCError."""
        self.put("System/Config/Gapless_Playback", "On" if on else "Off")

    def get_device_name(self) -> str | None:
        """Non supporté par le firmware R-N301 (validé en live)."""
        return self.get("System/Config/Device_Name")

    def set_device_name(self, name: str) -> None:
        """Non supporté par le firmware R-N301 (validé en live). Lève YNCError."""
        self.put("System/Config/Device_Name", name)

    def get_mac_filter(self) -> dict | None:
        node = self.get_block("System/Misc/Network/MAC_Address_Filter")
        if node is None:
            return None
        addrs = node.find("Address")
        return {
            "mode": _find_text(node, "Mode"),
            "addresses": [
                _find_text(addrs, f"Number_{i}") for i in range(1, 7)
            ] if addrs is not None else [],
        }

    def set_mac_filter(self, mode: str, *addresses: str | None) -> None:
        """mode: 'Off'/'Allow'/'Deny' ; adresses MAC sans séparateurs (12 hex)."""
        self.put("System/Misc/Network/MAC_Address_Filter/Mode", mode)
        for i, addr in enumerate(addresses[:6], 1):
            if addr is not None:
                self.put(f"System/Misc/Network/MAC_Address_Filter/Address/Number_{i}", addr)

    def firmware_update_check(self) -> str | None:
        return self.get("System/Misc/Update/Yamaha_Network_Site/Status")

    def firmware_update_start(self) -> None:
        """Déclenche la vérification/maj firmware depuis le site Yamaha."""
        self.put("System/Misc/Update/Yamaha_Network_Site", "Execute")

    # ------------------------------------------------------------------ lecture (par source)

    @staticmethod
    def source_tag(source: str) -> str:
        """Tag XML YNC d'une source réseau (ex 'NET RADIO' -> 'NET_RADIO')."""
        norm = source.strip().upper().replace("_", " ")
        for name, tag in SOURCE_TAGS.items():
            if name.upper().replace("_", " ") == norm:
                return tag
        raise ValueError(
            f"source réseau inconnue: {source!r} "
            f"(possibles: {', '.join(SOURCE_TAGS)})"
        )

    def play_info(self, source: str = "current") -> dict:
        """Infos de lecture d'une source. 'current' = source active de la zone.

        Renvoie Feature_Availability='Not Ready' si la source est inactive.
        """
        if source == "current":
            source = self.get_input()
        tag = self.source_tag(source)
        node = self.get_block(f"{tag}/Play_Info")
        if node is None:
            raise YNCError(f"{tag}/Play_Info absent de la réponse")
        info = {
            "source": source,
            "available": _find_text(node, "Feature_Availability"),
            "playback": _find_text(node, "Playback_Info"),
            "meta": {
                "artist": _find_text(node, "Meta_Info", "Artist"),
                "album": _find_text(node, "Meta_Info", "Album"),
                "song": _find_text(node, "Meta_Info", "Song"),
                "station": _find_text(node, "Meta_Info", "Station_Name")
                           or _find_text(node, "Meta_Info", "Program_Service"),
            },
            "album_art": _find_text(node, "Album_ART", "URL"),
            "play_time": _find_text(node, "Play_Time"),
        }
        if tag == "SERVER":
            info["repeat"] = _find_text(node, "Play_Mode", "Repeat")
            info["shuffle"] = _find_text(node, "Play_Mode", "Shuffle")
        if tag == "NET_RADIO":
            info["station"] = _find_text(node, "Meta_Info", "Station_Name")
            info["album"] = _find_text(node, "Meta_Info", "Album")
            info["song"] = _find_text(node, "Meta_Info", "Song")
        if tag == "Tuner":
            info["band"] = _find_text(node, "Tuning", "Band")
            info["freq"] = _find_text(node, "Tuning", "Freq", "Current", "Val")
            info["freq_unit"] = _find_text(node, "Tuning", "Freq", "Current", "Unit")
            info["preset"] = _find_text(node, "Preset", "Preset_Sel")
            info["fm_mode"] = _find_text(node, "FM_Mode")
            info["tuned"] = _find_text(node, "Signal_Info", "Tuned")
            info["stereo"] = _find_text(node, "Signal_Info", "Stereo")
            info["rds"] = {
                "program_type": _find_text(node, "Meta_Info", "Program_Type"),
                "program_service": _find_text(node, "Meta_Info", "Program_Service"),
                "radio_text": _find_text(node, "Meta_Info", "Radio_Text"),
            }
        return info

    def playback(self, source: str, action: str) -> None:
        """action: 'Play', 'Stop', 'Pause', 'Skip Fwd', 'Skip Rev'."""
        tag = self.source_tag(source)
        self.put(f"{tag}/Play_Control/Playback", action)

    def play(self, source: str = "current") -> None:
        self.playback(source if source != "current" else self.get_input(), "Play")

    def stop(self, source: str = "current") -> None:
        self.playback(source if source != "current" else self.get_input(), "Stop")

    def pause(self, source: str = "current") -> None:
        self.playback(source if source != "current" else self.get_input(), "Pause")

    def next_track(self, source: str = "current") -> None:
        self.playback(source if source != "current" else self.get_input(), "Skip Fwd")

    def prev_track(self, source: str = "current") -> None:
        self.playback(source if source != "current" else self.get_input(), "Skip Rev")

    def set_shuffle(self, source: str, on: bool) -> None:
        tag = self.source_tag(source)
        self.put(f"{tag}/Play_Control/Play_Mode/Shuffle", "On" if on else "Off")

    def set_repeat(self, source: str, mode: str) -> None:
        """mode: 'Off', 'One', 'All'."""
        tag = self.source_tag(source)
        self.put(f"{tag}/Play_Control/Play_Mode/Repeat", mode)

    # ------------------------------------------------------------------ navigation listes (SERVER / NET_RADIO)

    def list_info(self, source: str) -> dict:
        """Menu de navigation DLNA/net radio : 8 lignes + curseur."""
        tag = self.source_tag(source)
        node = self.get_block(f"{tag}/List_Info")
        if node is None:
            raise YNCError(f"{tag}/List_Info absent")
        lines = []
        cur = node.find("Current_List")
        if cur is not None:
            for i in range(1, 9):
                ln = cur.find(f"Line_{i}")
                if ln is not None:
                    lines.append({
                        "line": i,
                        "txt": (ln.findtext("Txt") or "").strip(),
                        "attribute": (ln.findtext("Attribute") or "").strip(),
                    })
        return {
            "source": source,
            "menu_status": _find_text(node, "Menu_Status"),
            "menu_layer": _find_text(node, "Menu_Layer"),
            "menu_name": _find_text(node, "Menu_Name"),
            "cursor_line": _find_text(node, "Cursor_Position", "Current_Line"),
            "max_line": _find_text(node, "Cursor_Position", "Max_Line"),
            "lines": lines,
        }

    def cursor(self, source: str, direction: str) -> None:
        """direction: 'Up', 'Down', 'Left', 'Right', 'Sel', 'Return'."""
        tag = self.source_tag(source)
        self.put(f"{tag}/List_Control/Cursor", direction)

    def page(self, source: str, direction: str = "Down") -> None:
        tag = self.source_tag(source)
        self.put(f"{tag}/List_Control/Page", direction)

    def select_line(self, source: str, n: int) -> None:
        """Sélection directe d'une ligne du menu (Line_1..8)."""
        tag = self.source_tag(source)
        self.put(f"{tag}/List_Control/Direct_Sel", f"Line_{int(n)}")

    def jump_line(self, source: str, line: int) -> None:
        """NON supporté par le firmware R-N301 (validé en live, HTTP 400).
        Naviguer avec cursor()/page()/select_line() à la place."""
        tag = self.source_tag(source)
        self.put(f"{tag}/List_Control/Jump_Line/Line", str(int(line)))

    # ------------------------------------------------------------------ tuner

    def get_band(self) -> str | None:
        """Bande courante (lecture via Play_Info, le GET Play_Control/Band est refusé)."""
        node = self.get_block("Tuner/Play_Info")
        return _find_text(node, "Tuning", "Band") if node is not None else None

    def set_band(self, band: str) -> None:
        """band: 'FM' ou 'AM'."""
        band = band.upper()
        if band not in ("FM", "AM"):
            raise ValueError("band doit être 'FM' ou 'AM'")
        self.put("Tuner/Play_Control/Tuning/Band", band)

    def tune(self, band: str, freq) -> None:
        """band 'FM': freq en MHz (ex 99.1 -> Val 9910, Exp=2).
        band 'AM': freq en kHz (Val brut, Exp=0).
        Chemin validé en live : Tuner/Play_Control/Tuning/Freq/{FM,AM}."""
        band = band.upper()
        if band == "FM":
            val = int(round(float(freq) * 100))
            xml = f"<Val>{val}</Val><Exp>2</Exp><Unit>MHz</Unit>"
            path = "Tuner/Play_Control/Tuning/Freq/FM"
        elif band == "AM":
            val = int(freq)
            xml = f"<Val>{val}</Val><Exp>0</Exp><Unit>kHz</Unit>"
            path = "Tuner/Play_Control/Tuning/Freq/AM"
        else:
            raise ValueError("band doit être 'FM' ou 'AM'")
        open_, close = _tag(path)
        self._request("PUT", f"{open_}{xml}{close}")

    def tune_fm(self, mhz: float) -> None:
        self.tune("FM", mhz)

    def tune_am(self, khz: int) -> None:
        self.tune("AM", khz)

    def get_freq(self) -> tuple[str | None, float | None]:
        """(bande, fréquence en MHz/kHz) courante, via Tuner/Play_Info."""
        node = self.get_block("Tuner/Play_Info")
        if node is None:
            return None, None
        band = _find_text(node, "Tuning", "Band")
        val = _find_text(node, "Tuning", "Freq", "Current", "Val")
        exp = _find_text(node, "Tuning", "Freq", "Current", "Exp") or "0"
        freq = int(val) / (10 ** int(exp)) if val else None
        return band, freq

    def tuning_step(self, direction: str, fm_step: float = 0.05, am_step: int = 9) -> None:
        """Pas-à-pas : le firmware R-N301 n'accepte que des fréquences absolues
        (Up/Down et Search_Mode refusés, validé en live) -> on calcule côté client.
        FM: pas de 50 kHz (87.5–108 MHz), AM: pas de 9 kHz (531–1602 kHz)."""
        band, freq = self.get_freq()
        if band is None or freq is None:
            raise YNCError("fréquence courante illisible")
        d = 1 if direction.lower().startswith(("up", "+")) else -1
        if band == "FM":
            new = min(108.0, max(87.5, round(freq + d * fm_step, 2)))
            self.tune_fm(new)
        else:
            new = min(1602, max(531, int(freq) + d * am_step))
            self.tune_am(new)

    def auto_search(self, band: str, direction: str = "Up") -> None:
        """NON supporté par le firmware R-N301 en YNC (validé en live).
        Utiliser tuning_step() en boucle côté client, ou la façade/télécommande."""
        raise YNCError(
            "auto-search non supporté en YNC sur R-N301 "
            "(Search_Mode/Tuning Up refusés) — utiliser tuning_step()"
        )

    def select_preset(self, n) -> None:
        """Preset tuner : entier 1..40, ou 'Up'/'Down' (validé en live).
        NB: valeur directe, PAS de <Val> — le wrapper est refusé par ce firmware."""
        val = str(n) if isinstance(n, int) else str(n).capitalize()
        self.put("Tuner/Play_Control/Preset/Preset_Sel", val)

    def get_preset(self) -> str | None:
        return self.get("Tuner/Play_Control/Preset/Preset_Sel")

    def remember_preset(self, n: int) -> None:
        """Mémorisation de preset : non confirmée sur R-N301 (l'app ne l'expose pas)."""
        self.put("Tuner/Play_Control/Preset/Memory", str(int(n)))

    def set_fm_mode(self, mode: str) -> None:
        """NON supporté en YNC sur R-N301 (validé en live, HTTP 400)."""
        self.put("Tuner/Play_Control/FM_Mode", mode)

    # ------------------------------------------------------------------ Remote_Signal (IR virtuel)

    def remote_signal(self, code: str) -> None:
        """Injecte un code télécommande (device 7F01 uniquement, 4 octets hex)."""
        code = code.upper().replace(" ", "")
        if len(code) != 8 or not all(c in "0123456789ABCDEF" for c in code):
            raise ValueError(f"code IR invalide: {code!r} (attendu 8 hex, ex 7F0146B9)")
        self._request(
            "PUT",
            f"<System><Misc><Remote_Signal><Receive><Code>{code}</Code>"
            "</Receive></Remote_Signal></Misc></System>",
        )

    def remote_key(self, name: str) -> None:
        """Touche nommée : 'shuffle', 'repeat', 'net_source', 'net_cycle'."""
        key = name.lower().replace("-", "_")
        if key not in REMOTE_KEYS:
            raise ValueError(f"touche inconnue: {name!r} (dispo: {', '.join(REMOTE_KEYS)})")
        self.remote_signal(REMOTE_KEYS[key])

    def remote_shuffle(self) -> None:
        self.remote_key("shuffle")

    def remote_repeat(self) -> None:
        self.remote_key("repeat")

    def remote_net_cycle(self) -> None:
        self.remote_key("net_cycle")


# ---------------------------------------------------------------------- CLI de test

if __name__ == "__main__":
    import json
    import sys

    amp = RN301(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST)
    print(json.dumps({
        "basic_status": amp.basic_status(),
        "system_config": amp.system_config(),
        "inputs": amp.list_inputs(),
    }, indent=2, ensure_ascii=False))
