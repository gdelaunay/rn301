# AGENTS.md

## Webapp — traduction obligatoire à chaque ajout

Toute chaîne visible (libellé, tooltip, placeholder, erreur, aide) doit exister dans
`webapp/static/i18n.json` sous `fr` **et** `en` (mêmes clés, structure identique).

Attributs : `data-i18n` (text), `data-i18n-html` (balises), `data-i18n-ph` (placeholder),
`data-i18n-title` (tooltip), `data-i18n-aria` (aria-label). Dynamique JS : `t('key')||'FR'`.

Ne pas traduire : états protocole (Play, SERVER, TUNER…), identifiants, R-N301, termes
techniques déjà anglais en FR (Push, Shuffle, GENA…).

Une chaîne = une clé. Si title, aria et label disent la même chose → même clé partout.
