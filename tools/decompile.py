"""Décompile des classes ciblées de l'APK.

Usage:
    .venv/Scripts/python tools/decompile.py refs "Main_Zone"   -> classes qui référencent un string
    .venv/Scripts/python tools/decompile.py src <classe>       -> source décompilée d'une classe
                                                                  (ex: Lcom/yamaha/av/c/a;)
    .venv/Scripts/python tools/decompile.py src-all "com/yamaha/npcontroller/activity2"
                                                               -> décompile tout un package
"""
import os
import re
import sys

from loguru import logger

logger.remove()

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK = os.path.join(BASE, "NETWORK PLAYER CONTROLLER_4.60_APKPure.apk")
OUT = os.path.join(BASE, "apk", "decompiled", "src")
os.makedirs(OUT, exist_ok=True)

from androguard.decompiler.decompiler import DecompilerDAD
from androguard.misc import AnalyzeAPK

print("Chargement APK... (peut prendre ~1min)", file=sys.stderr)
a, d, dx = AnalyzeAPK(APK)
dex = d[0]
decompiler = DecompilerDAD(dex, dx)


def find_refs(needle):
    pat = re.compile(re.escape(needle), re.I)
    hits = {}
    for cls in dex.get_classes():
        cname = cls.get_name()
        found = []
        for meth in cls.get_methods():
            code = meth.get_code()
            if code is None:
                continue
            bc = code.get_bc()
            for inst in bc.get_instructions():
                out = inst.get_output()
                if pat.search(out):
                    # extraire la constante string
                    m = re.search(r'"([^"]*)"', out)
                    found.append((meth.get_name(), m.group(1) if m else out[:120]))
        if found:
            hits[cname] = found
    for cname, found in hits.items():
        print(f"\n### {cname}")
        for mname, val in found[:10]:
            print(f"  {mname}: {val!r}")


def decompile_class(cname, quiet=True):
    for cls in dex.get_classes():
        if cls.get_name() == cname:
            try:
                return decompiler.get_source_class(cls)
            except Exception as e:
                return f"// decompile error: {e}"
    return None


def save(cname, src):
    path = os.path.join(OUT, cname.strip("L;").replace("/", os.sep) + ".java")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    return path


def src(cname):
    s = decompile_class(cname)
    if s is None:
        print(f"classe introuvable: {cname}")
        return
    p = save(cname, s)
    print(p)
    print(s[:3000])


def src_all(pkg):
    n = 0
    for cls in dex.get_classes():
        cname = cls.get_name()
        if pkg in cname:
            s = decompile_class(cname)
            if s:
                save(cname, s)
                n += 1
    print(f"{n} classes décompilées depuis {pkg} -> apk/decompiled/src/")


if __name__ == "__main__":
    cmd, arg = sys.argv[1], sys.argv[2]
    {"refs": find_refs, "src": src, "src-all": src_all}[cmd](arg)
