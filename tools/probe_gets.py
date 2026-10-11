"""Valide en live les commandes GET YNC du R-N301.

Chaque requête est envoyée une par une (le R-N301 refuse les requêtes
combinées), avec retry. Résultats -> amp/probe_gets.json + rapport console.

Usage: .venv/Scripts/python tools/probe_gets.py
"""
import json
import time
import urllib.request

AMP = "http://192.168.1.91/YamahaRemoteControl/ctrl"

# Chemins GET candidats (issus de k.java/q.java, adaptés R-N301).
# {player} = source courante parmi: Net_Radio, SERVER, Spotify, AirPlay, Tuner, iPod_USB, USB
QUERIES = {
    "power_zone": '<Main_Zone><Power_Control><Power>GetParam</Power></Power_Control></Main_Zone>',
    "power_system": '<System><Power_Control><Power>GetParam</Power></System>',
    "volume": '<Main_Zone><Volume><Lvl>GetParam</Lvl></Volume></Main_Zone>',
    "mute": '<Main_Zone><Volume><Mute>GetParam</Mute></Volume></Main_Zone>',
    "input": '<Main_Zone><Input><Input_Sel>GetParam</Input_Sel></Main_Zone>',
    "input_items": '<Main_Zone><Input><Input_Sel_Item>GetParam</Input_Sel_Item></Main_Zone>',
    "basic_status": '<Main_Zone><Basic_Status>GetParam</Basic_Status></Main_Zone>',
    "system_config": '<System><Config>GetParam</Config></System>',
    "network_standby": '<System><Config><Network_Standby>GetParam</Network_Standby></Config></System>',
    "sleep": '<System><Config><Sleep>GetParam</Sleep></Config></System>',
    "gapless": '<System><Config><Gapless_Playback>GetParam</Gapless_Playback></System>',
    "device_name": '<System><Config><Device_Name>GetParam</Device_Name></Config></System>',
    "net_info": '<System><Misc><Network><Network_Standby>GetParam</Network_Standby></Misc></System>',
    "mac_filter": '<System><Network><MAC_Address_Filter><Mode>GetParam</Mode></MAC_Address_Filter></System>',
    "firmware_update": '<System><Misc><Update><Yamaha_Network_Site><Status>GetParam</Status></Update></Misc></System>',
    "netradio_play_info": '<Net_Radio><Play_Info>GetParam</Play_Info></Net_Radio>',
    "server_play_info": '<SERVER><Play_Info>GetParam</Play_Info></SERVER>',
    "server_list": '<SERVER><List_Info>GetParam</List_Info></SERVER>',
    "spotify_play_info": '<Spotify><Play_Info>GetParam</Play_Info></Spotify>',
    "airplay_play_info": '<AirPlay><Play_Info>GetParam</Play_Info></AirPlay>',
    "tuner_play_info": '<Tuner><Play_Info>GetParam</Play_Info></Tuner>',
    "tuner_preset": '<Tuner><Play_Info><Preset><FM><Preset_Sel_Item>GetParam</Preset_Sel_Item></Preset></Play_Info></Tuner>',
    "tuner_band": '<Tuner><Play_Info><Band>GetParam</Band></Play_Info></Tuner>',
    "ipod_play_info": '<iPod_USB><Play_Info>GetParam</Play_Info></iPod_USB>',
    "usb_play_info": '<USB><Play_Info>GetParam</Play_Info></USB>',
    "netradio_shuffle": '<Net_Radio><Play_Control><Shuffle>GetParam</Shuffle></Play_Control></Net_Radio>',
    "netradio_repeat": '<Net_Radio><Play_Control><Repeat>GetParam</Repeat></Play_Control></Net_Radio>',
    "sleep_zone": '<Main_Zone><Power_Control><Sleep>GetParam</Sleep></Power_Control></Main_Zone>',
}


def ctrl(xml: str, tries: int = 3):
    body = f'<YAMAHA_AV cmd="GET">{xml}</YAMAHA_AV>'
    for i in range(tries):
        try:
            req = urllib.request.Request(AMP, data=body.encode(), method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                rsp = r.read().decode()
            if rsp.strip():
                return r.status, rsp
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception:
            pass
        time.sleep(0.6)
    return 0, ""


def main():
    results = {}
    for name, xml in QUERIES.items():
        status, rsp = ctrl(xml)
        ok = status == 200 and 'RC="0"' in rsp
        results[name] = {"http": status, "ok": ok, "rsp": rsp[:2000]}
        mark = "OK " if ok else f"KO({status})"
        print(f"[{mark}] {name}: {rsp[:160] if ok else ''}")
        time.sleep(0.4)
    with open("amp/probe_gets.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, ensure_ascii=False)
    print(f"\n-> amp/probe_gets.json  ({sum(r['ok'] for r in results.values())}/{len(results)} OK)")


if __name__ == "__main__":
    main()
