# Contribuer — instructions de développement

## Installation (dev)

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows  (Linux/mac : source .venv/bin/activate)
pip install -e .                # la lib rn301, 100 % stdlib, aucune dépendance
```

Python **≥ 3.10**.

## Lancer les tests

Deux suites, toutes deux hors-ligne (mockées, pas besoin d'ampli) :

```bash
python -m unittest discover tests        # librairie (discover, gena, push, rn301)
node webapp/tests/list_ui.test.mjs       # front webapp — navigateur DLNA
node webapp/tests/volume_ui.test.mjs     # front webapp — contrôleur volume
```

Les tests `*.mjs` sont autonomes (harness maison, aucune dépendance npm).

## Lancer la webapp

```bash
python -m webapp.server            # découvre l'ampli en SSDP au démarrage
python -m webapp.server --amp 192.168.1.91   # ou IP forcée (saut de la découverte)
```

Puis `http://localhost:8080`. Voir `README.md` pour l'API et les options.

## Outils de reverse engineering

Les scripts de `tools/` (sonde du protocole, scans IR, analyse de l'APK) et leurs
dépendances éventuelles sont documentés dans [`tools/README.md`](tools/README.md).
Ils ne font **pas** partie du package `rn301`.

## Conventions de code

- **Zéro dépendance runtime** dans `rn301/` et `webapp/server.py` : Python stdlib
  uniquement. Le `RE` (reverse engineering) est la valeur du projet, pas les libs.
- Toute requête vers l'ampli passe par le verrou `amp_lock` (une seule requête
  YNC/SOAP à la fois, sinon le firmware renvoie `RC=3` busy).
- Les captures matérielles (SCPD, scans, config) vivent dans `amp/` ; les notes
  protocolaires dans `docs/`.
