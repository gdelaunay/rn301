/* Harnais déterministe du contrôleur volume de la webapp (cf. docs/VOLUME_SYNC.md §7).

   On extrait le bloc du contrôleur volume de webapp/static/index.html (du
   marqueur '===== Volume' jusqu'à 'function togglePlay') et on l'exécute dans
   un contexte vm avec :
     - une horloge virtuelle (Date.now + setTimeout/setInterval),
     - un DOM minimal (le slider enregistre chaque peinture),
     - un stub `api()` = serveur + ampli simulé : clamp à `max`, ack POST relu
       après le set, latences variables → acks et échos pouvant arriver DANS LE
       DÉSORDRE,
     - un injecteur d'échos GENA/SSE (`gena()`), canal ordonné mais en retard.

   Aucun ampli requis. Lancer :  node webapp/tests/volume_ui.test.mjs        */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const HTML = new URL('../static/index.html', import.meta.url);
const T0 = 100000;                       // horloge de départ (évite lastSentMs=0)

function controllerSource() {
  const html = readFileSync(HTML, 'utf8');
  const i = html.indexOf('/* ===== Volume');
  const j = html.indexOf('function togglePlay');
  assert.ok(i > 0 && j > i, 'marqueurs du contrôleur volume introuvables dans index.html');
  return html.slice(i, j);
}

const PRELUDE = `
var PAINTS = [];      // [{v, at}] — chaque écriture du slider
var SENT = [];        // [{val, at}] — chaque POST /api/volume
var MUTES = [];       // états peints du bouton mute
var NOW = ${T0};
var api = null;       // stub fourni par le test
var state = { power: 'On', volume: 40, mute: 'Off' };

var Date = { now: function () { return NOW; } };

var __els = {};
function __mkEl(id) {
  if (id === 'vol') {
    var v = 0;
    return {
      min: 0, max: 100,
      get value() { return v; },
      set value(x) { v = x; PAINTS.push({ v: x, at: NOW }); },
      style: { setProperty: function () {} },
      textContent: ''
    };
  }
  return {
    value: 0, textContent: '', style: { setProperty: function () {} },
    classList: { toggle: function (cls, on) { if (id === 'btnMute') MUTES.push({ on: !!on, at: NOW }); } }
  };
}
var document = { getElementById: function (id) { if (!__els[id]) __els[id] = __mkEl(id); return __els[id]; } };

/* --- horloge virtuelle + file de microtâches (les acks sont asynchrones) --- */
var __t = new Map(), __tid = 1, __micro = [];
function setTimeout(fn, ms) { var id = __tid++; __t.set(id, { at: NOW + (ms || 0), fn: fn, iv: null }); return id; }
function clearTimeout(id) { __t.delete(id); }
function setInterval(fn, ms) { var id = __tid++; __t.set(id, { at: NOW + (ms || 0), fn: fn, iv: ms || 1 }); return id; }
function clearInterval(id) { __t.delete(id); }
function __drain() { var g = 0; while (__micro.length && g++ < 10000) { __micro.shift()(); } }
function deliver(cb, lat) { setTimeout(function () { __micro.push(cb); }, lat); }
function tick(ms) {
  var end = NOW + ms;
  for (;;) {
    var best = null, bid = null;
    for (var e of __t.entries()) {
      var id = e[0], t = e[1];
      if (t.at <= end && (best === null || t.at < best.at || (t.at === best.at && id < bid))) { best = t; bid = id; }
    }
    if (!best) break;
    NOW = best.at;
    if (best.iv) best.at = NOW + best.iv; else __t.delete(bid);
    best.fn();
    __drain();
  }
  NOW = end; __drain();
}
/* render() minimal : remplace l'état puis injecte volume/mute comme le fait la page */
function render(s, ts) { state = s; noteDevice(s.volume, s.mute, ts); }
`;

function makeWorld(opts = {}) {
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(PRELUDE + controllerSource(), sandbox, { filename: 'index.html(volume)' });

  const max = opts.max ?? 80;            // plafond firmware « Max Volume », non lisible en YNC
  const ackLat = opts.ackLat ?? 250;     // latence de la réponse POST (relue après le set)
  const genaLat = opts.genaLat ?? 180;   // latence des notifications GENA
  const noGena = !!opts.noGena;          // --no-gena : aucun SSE, l'ack/poll seuls
  let devVol = opts.devVol ?? 40;
  const applied = [];                    // historique des valeurs appliquées par l'ampli

  sandbox.api = function (action, body /*, norefresh */) {
    assert.equal(action, 'volume', 'le contrôleur ne doit appeler que /api/volume');
    const target = Math.max(0, Math.min(max, Math.round(body.val)));   // le firmware clampe
    const changed = target !== devVol;
    devVol = target;
    applied.push({ asked: body.val, applied: target, at: sandbox.NOW });
    sandbox.SENT.push({ val: body.val, at: sandbox.NOW });

    const resp = { ok: true, volume: devVol, mute: 'Off' };   // server.py : ack léger
    const al = typeof ackLat === 'function' ? ackLat(body.val, applied.length) : ackLat;
    let cb = null;
    sandbox.deliver(() => { if (cb) { const f = cb; cb = null; f(resp); } }, al);

    // GENA n'émet que si la valeur a réellement changé (doc §2.3), canal ordonné
    if (changed && !noGena) {
      const gl = typeof genaLat === 'function' ? genaLat(body.val) : genaLat;
      sandbox.deliver(() => sandbox.noteDevice(target, undefined), gl);
    }
    return { then(f) { cb = f; return this; } };             // thenable synchrone-friendly
  };

  /* changement EXTERNE (télécommande physique, app Yamaha) → notification GENA */
  sandbox.gena = function (v, lat) {
    devVol = Math.max(0, Math.min(max, v));
    sandbox.deliver(() => sandbox.noteDevice(devVol, undefined), lat ?? (typeof genaLat === 'number' ? genaLat : 180));
  };
  /* snapshot pollé : ts = heure d'ENVOI de la requête, réponse lat ms après */
  sandbox.poll = function (v, sentAt, lat = 60) {
    sandbox.deliver(() => sandbox.render({ power: 'On', volume: v, mute: 'Off' }, sentAt), lat);
  };
  sandbox.dev = () => devVol;
  // [...] : recopie dans le realm hôte (sinon assert.deepEqual compare les prototypes)
  sandbox.applied = () => [...applied];
  sandbox.paints = () => [...sandbox.PAINTS].map(p => p.v);
  sandbox.sentVals = () => [...sandbox.SENT].map(s => s.val);
  return sandbox;
}

/* ------------------------------------------------------------------ tests */
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('plage du slider figée 0..100 (jamais de max dynamique)', () => {
  const html = readFileSync(HTML, 'utf8');
  assert.match(html, /id="vol" min="0" max="100"/);
  assert.doesNotMatch(html, /(\.max\s*=|setAttribute\(\s*['"]max)/, 'quelque chose réécrit slider.max');
  assert.equal(makeWorld().document.getElementById('vol').max, 100);
});

test('critère 1 — feedback instantané : peint avant tout envoi réseau', () => {
  const w = makeWorld({ ackLat: 5000, genaLat: 5000 });
  w.volInput(25);
  assert.deepEqual(w.paints(), [25], 'peint immédiatement, sans attendre le réseau');
  w.tick(200);
  assert.equal(w.SENT.length, 1, 'un seul envoi (throttle leading)');
  assert.ok(w.PAINTS[0].at <= w.SENT[0].at);
  w.tick(6000);
  assert.equal(w.paints().at(-1), 25);
});

test('critère 1 bis — premier +/− part de la valeur affichée', () => {
  const w = makeWorld({ devVol: 40, ackLat: 120 });
  w.noteDevice(40, undefined, w.NOW);
  w.tick(1);
  w.tapVol(1);
  assert.equal(w.SENT[0].val, 41);
  assert.equal(w.paints().at(-1), 41);
  w.tick(2000);
  assert.equal(w.paints().at(-1), 41, 'stable, pas de retour à 40');
});

test('critère 2 — repro §4 : alternance rapide 15↔30, acks DÉSORDONNÉS → zéro replay', () => {
  // l'ack de la 1re commande (15) arrive APRÈS ceux des suivantes ; échos GENA très en retard
  let n = 0;
  const w = makeWorld({ devVol: 20, ackLat: () => (++n === 1 ? 1400 : 120), genaLat: 900 });
  w.volInput(15); w.tick(60);
  w.volInput(30); w.tick(60);
  w.volInput(15); w.tick(60);
  w.volInput(30); w.volEnd();
  const gestureEnd = w.NOW;
  w.tick(4000);
  const after = w.PAINTS.filter(p => p.at >= gestureEnd).map(p => p.v);
  assert.equal(w.paints().at(-1), 30, `doit finir sur 30 — peint : ${w.paints()}`);
  assert.ok(!after.includes(15), `aucun retour à 15 après relâchement : ${after}`);
  assert.equal(w.dev(), 30, 'ampli bien à 30');
  assert.ok(w.applied().at(-1).applied === 30);
});

test('critère 2 bis — même repro avec acks en ordre mais GENA très lent', () => {
  const w = makeWorld({ devVol: 20, ackLat: 100, genaLat: 1500 });
  w.volInput(15); w.tick(200);
  w.volInput(30); w.tick(200);
  w.volInput(15); w.tick(200);
  w.volInput(30); w.volEnd();
  const gestureEnd = w.NOW;
  w.tick(5000);
  const after = w.PAINTS.filter(p => p.at >= gestureEnd).map(p => p.v);
  assert.equal(w.paints().at(-1), 30);
  assert.ok(!after.includes(15), `échos GENA tardifs ignorés : ${after}`);
});

test('critère 3 — clamp : viser 100 → settle NET sur 80, sans aller-retour', () => {
  const w = makeWorld({ devVol: 70, max: 80, ackLat: 200, genaLat: 260 });
  w.volInput(85); w.tick(150);          // traverse 80 en chemin (faux positif ownEcho possible)
  w.volInput(100); w.volEnd();
  w.tick(3000);
  const p = w.paints();
  assert.equal(p.at(-1), 80, `doit finir sur 80 : ${p}`);
  const first80 = p.indexOf(80);
  assert.equal(p.indexOf(100, first80), -1, `pas de 100 après le 80 : ${p}`);
  assert.equal(p.filter(v => v === 80).length, 1, `80 peint une seule fois (net) : ${p}`);
  assert.equal(w.document.getElementById('vol').max, 100, 'plage toujours 0..100');
});

test('critère 3 bis — clamp sans GENA (--no-gena) : l\'ack POST suffit', () => {
  const w = makeWorld({ devVol: 70, max: 80, noGena: true, ackLat: 300 });
  w.volInput(100); w.volEnd();
  w.tick(3000);
  const p = w.paints();
  assert.equal(p.at(-1), 80);
  assert.equal(p.filter(v => v === 80).length, 1);
});

test('critère 4 — changement externe suivi rapidement quand on ne touche à rien', () => {
  const w = makeWorld({ devVol: 30 });
  w.noteDevice(30, undefined, w.NOW); w.tick(1);
  const t = w.NOW;
  w.gena(55, 40);                        // télécommande physique
  w.tick(1000);
  assert.equal(w.paints().at(-1), 55);
  assert.ok(w.PAINTS.at(-1).at - t <= 1000, 'aligné en ≤ 1 s');
});

test('critère 4 bis — changement externe pendant un glisser : repris juste après', () => {
  const w = makeWorld({ devVol: 30, ackLat: 120, genaLat: 100 });
  w.noteDevice(30, undefined, w.NOW); w.tick(1);
  w.volInput(45); w.tick(150);           // notre ordre est appliqué et confirmé
  w.gena(70, 30);                        // qqun change le volume ailleurs PENDANT la gesture
  w.tick(60);
  assert.equal(w.paints().at(-1), 45, 'pendant la gesture, c\'est nous qui avons la main');
  w.volEnd(); w.tick(3000);
  assert.equal(w.paints().at(-1), 70, 'la vérité device reprend la main');
  assert.equal(w.dev(), 70);
});

test('anti-flicker — un poll périmé n\'écrase pas un SSE plus neuf', () => {
  const w = makeWorld({ noGena: true });
  const t = w.NOW;
  w.noteDevice(60, undefined, t);        // SSE à t
  w.tick(1);
  assert.equal(w.paints().at(-1), 60);
  w.render({ power: 'On', volume: 50, mute: 'Off' }, t - 100);   // poll envoyé avant
  w.tick(1);
  assert.equal(w.paints().at(-1), 60, 'snapshot périmé ignoré');
  w.render({ power: 'On', volume: 50, mute: 'Off' }, t + 100);   // poll plus récent
  w.tick(1);
  assert.equal(w.paints().at(-1), 50);
});

test('critère 5 — envois bornés pendant un long glisser (throttle ~120 ms)', () => {
  const w = makeWorld({ ackLat: 50, genaLat: 50 });
  for (let i = 0; i < 40; i++) { w.volInput(10 + i); w.tick(25); }   // 1 s de glisser
  w.volEnd(); w.tick(1000);
  assert.ok(w.SENT.length <= 11, `≤ ~1 envoi/120 ms + trailing, obtenu ${w.SENT.length}`);
  assert.equal(w.SENT.at(-1).val, 49, 'la dernière cible part toujours (trailing)');
  assert.equal(w.paints().at(-1), 49);
});

test('critère 5 bis — hold +/− : répétition bornée, settle sur la valeur device', () => {
  const w = makeWorld({ devVol: 40, ackLat: 120, genaLat: 150 });
  w.noteDevice(40, undefined, w.NOW); w.tick(1);
  w.holdStart(1); w.tick(700); w.holdEnd();
  assert.ok(w.SENT.at(-1).val > 41, 'la répétition a monté le volume');
  assert.ok(w.SENT.length <= 12, `envois bornés pendant le hold (${w.SENT.length})`);
  w.tick(3000);
  assert.equal(w.paints().at(-1), w.dev(), 'affiche la vérité device à la fin');
});

test('mute : peint depuis le device, ignoré si l\'échantillon est périmé', () => {
  const w = makeWorld({ noGena: true });
  const t = w.NOW;
  w.noteDevice(40, 'On', t); w.tick(1);
  assert.equal(w.MUTES.at(-1).on, true);
  w.noteDevice(40, 'Off', t - 100); w.tick(1);
  assert.equal(w.MUTES.at(-1).on, true, 'échantillon vieux ignoré');
  w.noteDevice(40, 'Off', t + 100); w.tick(1);
  assert.equal(w.MUTES.at(-1).on, false);
});

test('pas de peinture parasite : rien ne bouge sans évènement', () => {
  const w = makeWorld({ devVol: 40, ackLat: 120, genaLat: 150 });
  w.noteDevice(40, undefined, w.NOW); w.tick(1);
  const before = w.PAINTS.length;
  w.tick(20000);                          // 20 s de polls/SSE stables
  assert.equal(w.PAINTS.length, before, 'aucune peinture inutile');
});

/* ------------------------------------------------------------------ runner */
let fail = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`  ok    ${name}`); }
  catch (e) { fail++; console.log(`  ÉCHEC ${name}\n          ${e.message.split('\n')[0]}`); }
}
console.log(fail ? `\n${fail} test(s) en échec sur ${tests.length}` : `\n${tests.length} tests passés`);
process.exit(fail ? 1 : 0);
