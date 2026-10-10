"""Brute-force les 256 codes Remote_Signal du R-N301.

Format du code : 7F01 + XX + ~XX (complément à 1), ex: 7F0146B9 = touche 0x46 (Shuffle).

Pour chaque code XX :
  1. snapshot de l'état observable (power, volume, mute, input)
  2. injection du code
  3. re-snapshot -> on note les diffs

Les effets NON observables via YNC (balance, speaker A/B, menus, display...)
doivent être notés à l'oreille/à l'oeil -> utiliser --interactive pour
répondre manuellement pour chaque code.

Usage:
    .venv/Scripts/python tools/scan_remote.py                # auto, plage complète
    .venv/Scripts/python tools/scan_remote.py --range 00-3F  # une plage
    .venv/Scripts/python tools/scan_remote.py --interactive  # demande un retour humain
"""
import sys, time, json, argparse
import urllib.request

AMP = "http://192.168.1.91/YamahaRemoteControl/ctrl"
RESULTS = "amp/remote_scan.json"

# Le R-N301 refuse les requetes combinees (RC=2) -> une requete par param
STATE_QUERIES = {
    "power": '<YAMAHA_AV cmd="GET"><Main_Zone><Power_Control><Power>GetParam</Power></Power_Control></Main_Zone></YAMAHA_AV>',
    "volume": '<YAMAHA_AV cmd="GET"><Main_Zone><Volume><Lvl>GetParam</Lvl></Volume></Main_Zone></YAMAHA_AV>',
    "mute": '<YAMAHA_AV cmd="GET"><Main_Zone><Volume><Mute>GetParam</Mute></Volume></Main_Zone></YAMAHA_AV>',
    "input": '<YAMAHA_AV cmd="GET"><Main_Zone><Input><Input_Sel>GetParam</Input_Sel></Input></Main_Zone></YAMAHA_AV>',
}
STATE_TAGS = {"power": "Power", "volume": "Val", "mute": "Mute", "input": "Input_Sel"}


def ctrl(xml: str) -> str:
    req = urllib.request.Request(AMP, data=xml.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.read().decode()
    except Exception as e:
        return f"<error {e}>"


def ctrl_retry(xml: str, tag: str, tries: int = 4) -> str:
    """Le R-N301 repond parfois vide quand il est occupe -> retry."""
    import re
    for i in range(tries):
        rsp = ctrl(xml)
        if re.search(rf"<{tag}>[^<]+</{tag}>", rsp):
            return rsp
        time.sleep(0.5)
    return rsp


def snapshot() -> dict:
    import re
    state = {}
    for key, q in STATE_QUERIES.items():
        rsp = ctrl_retry(q, STATE_TAGS[key])
        m = re.search(rf"<{STATE_TAGS[key]}>([^<]*)</{STATE_TAGS[key]}>", rsp)
        state[key] = m.group(1) if m else None
    return state


def code_for(xx: int) -> str:
    return f"7F01{xx:02X}{(~xx & 0xFF):02X}"


def inject(xx: int) -> str:
    return ctrl(
        '<YAMAHA_AV cmd="PUT"><System><Misc><Remote_Signal><Receive>'
        f"<Code>{code_for(xx)}</Code>"
        "</Receive></Remote_Signal></Misc></System></YAMAHA_AV>"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--range", default="00-FF", help="plage hex, ex 00-3F ou 46")
    ap.add_argument("--interactive", action="store_true")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    if "-" in args.range:
        lo, hi = args.range.split("-")
    else:
        lo = hi = args.range
    codes = range(int(lo, 16), int(hi, 16) + 1)

    results = []
    try:
        with open(RESULTS) as f:
            results = json.load(f)
    except Exception:
        pass
    done = {r["code"] for r in results}

    base = snapshot()
    print(f"Etat initial: {base}\n")

    for xx in codes:
        code = code_for(xx)
        if code in done:
            continue
        before = snapshot()
        rsp = inject(xx)
        rc_ok = 'RC="0"' in rsp
        time.sleep(args.delay)
        after = snapshot()
        diff = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        note = ""
        if args.interactive:
            note = input(f"[{code}] RC_ok={rc_ok} diff={diff or '-'} — effet vu/entendu ? (entrer=rien) : ").strip()
        results.append({"code": code, "byte": f"{xx:02X}", "accepted": rc_ok,
                        "diff": diff, "note": note})
        flag = "!!" if (diff or note) else ("ok" if rc_ok else "no")
        print(f"[{code}] {flag} accepted={rc_ok} diff={diff or ''} {note}")

    with open(RESULTS, "w") as f:
        json.dump(results, f, indent=1)
    print(f"\nRésultats -> {RESULTS}")
    hits = [r for r in results if r["diff"] or r["note"]]
    print(f"Codes avec effet observable: {len(hits)}")
    for r in hits:
        print(" ", r["code"], r["diff"], r["note"])


if __name__ == "__main__":
    main()
