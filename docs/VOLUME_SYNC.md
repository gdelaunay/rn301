
### 10.5 Deux suites (post-redesign UI)

**a) Le libellé ne dépend plus de l'acceptation d'un échantillon.** `#volval`
affiche la valeur **ou** `--` selon `state.power` : il a donc deux entrées, pas
une. Avant, il n'était écrit que dans `paintVol()`, donc uniquement quand un
échantillon volume était **accepté**. À l'ouverture de page : le seed SSE arrive
avant l'état (`power` inconnu → `--`), puis le poll est rejeté comme périmé
(§10.1 filtre 1) → le `--` restait collé jusqu'au poll suivant (~4 s). D'où
`paintVolLabel()`, appelé par `paintVol()` **et** par `render()` juste après
`state=s`. Verrouillé par le test *« ouverture de page : écho SSE reçu AVANT
l'état → pas de « -- » collé »* (15 tests au harnais).

**b) Amorçage rapide (`GET /api/basic`).** Le premier affichage attendait
`full_state()` (6-8 GET YNC séquentiels, ~2-3 s). `/api/basic` renvoie
`basic_status()` + la liste des sources (cachée) avec `partial: true` ; la page
lance `bootstrap()` **en parallèle** du premier `refresh()` — sans risque : la
lib sérialise déjà les accès à l'ampli via `amp_lock(host)` (`rn301/_locks.py`)
et le serveur est `ThreadingHTTPServer`. `render()` s'arrête après les sources
sur un état partiel (les cartes now playing / tuner / liste / push sont
`display:none` par défaut et dépendent de `play_info`/`list_info`), et **un état
partiel n'écrase jamais un état complet déjà reçu**. Gains au passage :
`sysinfo` n'affiche plus `v?` quand `system_config` échoue, et le bloc tuner ne
peut plus planter sur `(pi.freq/100).toFixed(2)` quand `play_info` est `null`
(crash latent préexistant).
