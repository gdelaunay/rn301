/* Harnais du bloc « Navigateur » de la webapp (SERVER / NET RADIO).

   Même principe que volume_ui.test.mjs : on extrait le bloc de
   webapp/static/index.html (du marqueur '--- Navigateur' jusqu'à
   'function render(s,ts){') et on l'exécute dans un contexte vm avec une
   horloge virtuelle, un DOM minimal et des stubs api()/fetch().

   Ce qu'il verrouille : la carte ne DISPARAÎT JAMAIS pendant un chargement
   (Menu_Status='Busy' ou list_info null) — contenu précédent conservé, grisé +
   spinner — donc plus de saut de scroll ; elle se repeint dès que le menu est
   Ready, via la réponse légère du POST ou la relance /api/list ; et le marqueur
   « en lecture » (retrouvé côté client depuis Play_Info, le curseur YNC ne
   suivant pas les changements de piste) suit la piste, distinct du curseur.

   Lancer :  node webapp/tests/list_ui.test.mjs                              */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const HTML = new URL('../static/index.html', import.meta.url);
const T0 = 100000;

function blockSource() {
  const html = readFileSync(HTML, 'utf8');
  const i = html.indexOf('/* --- Navigateur');
  const j = html.indexOf('function render(s,ts){');
  assert.ok(i > 0 && j > i, 'marqueurs du bloc Navigateur introuvables dans index.html');
  return html.slice(i, j);
}

const PRELUDE = `
var NOW = ${T0};
var state = { power: 'On', input: 'SERVER' };
var pushMode = false;
/* ces trois vivent sur la ligne de globaux de la page, hors du bloc extrait */
var listEver = false, listBusySince = 0, listRetry = null, listGaveUp = false;
var CALLS = [];          // {kind, at, ...} — requêtes émises
var REFRESH = [];        // refresh() complets demandés
var apiReply = null;     // réponse simulée du POST de navigation
var listReply = null;    // réponse simulée de /api/list (relance)

var Date = { now: function () { return NOW; } };

/* --- DOM minimal --- */
function mkEl(id) {
  var el = {
    id: id || '', children: [], style: {}, attrs: {}, className: '', textContent: '', _cls: {},
    setAttribute: function (k, v) { el.attrs[k] = String(v); },
    appendChild: function (c) { el.children.push(c); return c; },
    classList: {
      toggle: function (c, on) { if (on === undefined) on = !el._cls[c]; if (on) el._cls[c] = 1; else delete el._cls[c]; return !!on; },
      contains: function (c) { return !!el._cls[c]; }
    }
  };
  var html = '';
  Object.defineProperty(el, 'innerHTML', {
    get: function () { return html; },
    set: function (v) { html = v; if (v === '') el.children.length = 0; }
  });
  return el;
}
var __els = {};
var document = {
  getElementById: function (id) { if (!__els[id]) __els[id] = mkEl(id); return __els[id]; },
  createElement: function (tag) { var e = mkEl(); e.tagName = tag; return e; }
};
function el(id) { return document.getElementById(id); }

/* --- horloge virtuelle --- */
var __t = new Map(), __tid = 1, __micro = [];
function setTimeout(fn, ms) { var id = __tid++; __t.set(id, { at: NOW + (ms || 0), fn: fn, iv: null }); return id; }
function clearTimeout(id) { __t.delete(id); }
function __drain() { var g = 0; while (__micro.length && g++ < 10000) { __micro.shift()(); } }
function later(cb, lat) { setTimeout(function () { __micro.push(cb); }, lat); }
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
    __t.delete(bid);
    best.fn();
    __drain();
  }
  NOW = end; __drain();
}

/* --- stubs réseau ---
   vpromise() : une mini-promesse dont les continuations passent par __micro,
   donc pilotées par tick(). Une vraie promesse échapperait à l'horloge virtuelle
   (c'est pour ça que pollList() est en .then et pas en async/await). */
function vpromise() {
  var cbs = [], val = null, settled = false;
  var p = { then: function (f, g) {
      var next = vpromise();
      var run = function (v) { try { next._resolve(f ? f(v) : v); } catch (e) { if (g) g(e); } };
      if (settled) __micro.push(function () { run(val); }); else cbs.push(run);
      return next;
    } };
  p._resolve = function (v) {
    if (v && typeof v.then === 'function') { v.then(p._resolve); return; }
    if (settled) return; settled = true; val = v;
    cbs.splice(0).forEach(function (cb) { __micro.push(function () { cb(val); }); });
  };
  return p;
}
function api(action, body, norefresh) {
  CALLS.push({ kind: 'api:' + action, body: body, norefresh: !!norefresh, at: NOW });
  var p = vpromise();
  later(function () { p._resolve(apiReply); }, 120);
  return p;
}
function refresh() { REFRESH.push(NOW); }
var fetch = function (url, opts) {
  CALLS.push({ kind: 'fetch:' + url, body: JSON.parse((opts && opts.body) || '{}'), at: NOW });
  var p = vpromise();
  later(function () {
    p._resolve({ ok: true, json: function () {
      var q = vpromise();
      later(function () { q._resolve(listReply); }, 80);
      return q;
    } });
  }, 20);
  return p;
};
`;

function makeWorld(opts = {}) {
  const sandbox = {};
  vm.createContext(sandbox);
  vm.runInContext(PRELUDE + blockSource(), sandbox, { filename: 'index.html(navigateur)' });
  if (opts.input) sandbox.state = { power: 'On', input: opts.input };
  sandbox.pushMode = !!opts.pushMode;
  sandbox.setApiReply = r => { sandbox.apiReply = r; };
  sandbox.setListReply = r => { sandbox.listReply = r; };
  sandbox.el = id => sandbox.document.getElementById(id);
  sandbox.calls = () => [...sandbox.CALLS].map(c => c.kind);
  sandbox.menuTxt = () => [...sandbox.el('menu').children].map(d => d.textContent);
  sandbox.menuCls = () => [...sandbox.el('menu').children].map(d => d.className);
  sandbox.marked = () => [...sandbox.el('menu').children].filter(d => /\bcur\b/.test(d.className)).map(d => d.textContent);
  sandbox.nowPlaying = (meta, playing = true) => sandbox.noteNowPlaying({ available: 'Ready', meta }, playing);
  sandbox.busy = () => !!sandbox.el('listcard')._cls.busy;
  sandbox.visible = () => sandbox.el('listcard').style.display !== 'none';
  return sandbox;
}

const MENU = (name, lines, status = 'Ready') => ({
  menu_status: status, menu_layer: '1', menu_name: name, cursor_line: String(lines.findIndex(l => l.sel) + 1 || 1),
  lines: lines.map((l, i) => ({ line: i + 1, txt: l.txt, attribute: l.un ? 'Unselectable' : 'Selectable' })),
});
const A = MENU('Musique', [{ txt: 'Artiste' }, { txt: 'Album' }, { txt: 'Titre', sel: true }]);
const B = MENU('Album', [{ txt: 'Piste 1' }, { txt: 'Piste 2', sel: true }]);

/* ------------------------------------------------------------------ tests */
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('menu Ready → carte visible, lignes peintes, curseur marqué', () => {
  const w = makeWorld();
  w.syncListCard({ list_info: A }, true, 'SERVER');
  assert.ok(w.visible());
  assert.deepEqual(w.menuTxt(), ['Artiste', 'Album', '▸ Titre']);
  assert.equal(w.el('menuname').textContent, 'Musique');
  assert.equal(w.busy(), false);
});

test('chargement (Busy) → la carte NE DISPARAÎT PAS et garde son contenu', () => {
  const w = makeWorld();
  w.syncListCard({ list_info: A }, true, 'SERVER');
  const avant = w.menuTxt();
  w.tick(500);
  w.syncListCard({ list_info: MENU('Album', [], 'Busy') }, true, 'SERVER');
  assert.ok(w.visible(), 'carte toujours affichée → pas de saut de scroll');
  assert.deepEqual(w.menuTxt(), avant, 'contenu précédent conservé tel quel');
  assert.equal(w.busy(), true, 'grisé + spinner');
  assert.equal(w.el('listcard').attrs['aria-busy'], 'true');
  assert.equal(w.el('menuname').textContent, 'Musique', 'titre précédent conservé');
});

test('list_info null (GET List_Info en échec) → pareil, pas de disparition', () => {
  const w = makeWorld();
  w.syncListCard({ list_info: A }, true, 'SERVER');
  w.tick(500);
  w.syncListCard({ list_info: null }, true, 'NET RADIO');
  assert.ok(w.visible());
  assert.deepEqual(w.menuTxt(), ['Artiste', 'Album', '▸ Titre']);
  assert.equal(w.busy(), true);
});

test('menu Ready suivant → repeint et dégrise', () => {
  const w = makeWorld();
  w.syncListCard({ list_info: A }, true, 'SERVER');
  w.tick(500);
  w.syncListCard({ list_info: MENU('Album', [], 'Busy') }, true, 'SERVER');
  assert.equal(w.busy(), true);
  w.syncListCard({ list_info: B }, true, 'SERVER');
  assert.deepEqual(w.menuTxt(), ['Piste 1', '▸ Piste 2']);
  assert.equal(w.el('menuname').textContent, 'Album');
  assert.equal(w.busy(), false, 'plus de spinner une fois prêt');
  assert.equal(w.el('listcard').attrs['aria-busy'], 'false');
});

test('clic sur une ligne → grisé IMMÉDIAT, avant la réponse réseau', () => {
  const w = makeWorld();
  w.setApiReply({ ok: true, source: 'SERVER', list: null });   // ampli encore en train de charger
  w.syncListCard({ list_info: A }, true, 'SERVER');
  const avant = w.menuTxt();
  w.el('menu').children[0].onclick();                           // clic « Artiste »
  assert.equal(w.busy(), true, 'grisé dès le clic, sans attendre');
  assert.deepEqual(w.menuTxt(), avant, 'rien ne disparaît pendant l\'aller-retour');
  assert.deepEqual(w.calls().slice(-1), ['api:line']);
  const call = w.CALLS.at(-1);
  assert.equal(call.norefresh, true, 'pas de refresh complet derrière le clic');
  assert.equal(call.body.source, 'SERVER', 'source passée → le serveur évite un GET Input_Sel');
});

test('réponse légère {list:Ready} → peint tout de suite + UN full_state différé', () => {
  const w = makeWorld();
  w.setApiReply({ ok: true, source: 'SERVER', list: B });
  w.syncListCard({ list_info: A }, true, 'SERVER');
  w.el('menu').children[0].onclick();
  w.tick(200);                                   // la réponse arrive
  assert.deepEqual(w.menuTxt(), ['Piste 1', '▸ Piste 2']);
  assert.equal(w.busy(), false);
  w.tick(1000);
  assert.equal(w.REFRESH.length, 1, 'now playing resynchronisé une seule fois');
});

test('relance légère pendant le chargement : /api/list jusqu\'à Ready, puis stop', () => {
  const w = makeWorld();
  w.setApiReply({ ok: true, source: 'SERVER', list: null });
  w.syncListCard({ list_info: A }, true, 'SERVER');
  w.el('menu').children[0].onclick();
  w.tick(400);                                   // 1re relance partie
  w.setListReply({ ok: true, list: MENU('Album', [], 'Busy') });
  w.tick(3000);
  const n = w.calls().filter(c => c === 'fetch:/api/list').length;
  assert.ok(n >= 2, `relances tant que pas prêt (${n})`);
  assert.equal(w.busy(), true);
  w.setListReply({ ok: true, list: B });         // l'ampli a fini
  w.tick(3000);
  assert.deepEqual(w.menuTxt(), ['Piste 1', '▸ Piste 2']);
  assert.equal(w.busy(), false, 'spinner éteint');
  const n2 = w.calls().filter(c => c === 'fetch:/api/list').length;
  w.tick(5000);
  assert.equal(w.calls().filter(c => c === 'fetch:/api/list').length, n2, 'plus aucune relance après Ready');
});

test('relances bornées : si l\'ampli ne répond jamais, on lâche et on masque', () => {
  const w = makeWorld();
  w.setApiReply({ ok: true, source: 'SERVER', list: null });
  w.setListReply({ ok: true, list: null });
  w.syncListCard({ list_info: A }, true, 'SERVER');
  w.el('menu').children[0].onclick();
  w.tick(20000);
  assert.equal(w.busy(), false, 'pas de grisé éternel');
  w.syncListCard({ list_info: null }, true, 'SERVER');
  assert.equal(w.visible(), false, 'carte masquée après LIST_BUSY_MAX');
  w.tick(20000);
  w.syncListCard({ list_info: null }, true, 'SERVER');
  assert.equal(w.visible(), false, 'et elle ne repart pas dans un nouveau cycle de 8 s');
  assert.equal(w.busy(), false);
  const n = w.calls().filter(c => c === 'fetch:/api/list').length;
  assert.ok(n <= 25, `relances bornées (${n})`);
  // un menu Ready qui finit par arriver reprend la main
  w.syncListCard({ list_info: B }, true, 'SERVER');
  assert.ok(w.visible(), 'un Ready après abandon réaffiche la carte');
  assert.deepEqual(w.menuTxt(), ['Piste 1', '▸ Piste 2']);
});

test('sortie de la liste : source non-liste, push ou veille → carte masquée', () => {
  for (const [label, args] of [
    ['TUNER', [{ list_info: A }, true, 'TUNER']],
    ['push', [{ list_info: A }, true, 'SERVER', true]],
    ['veille', [{ list_info: A }, false, 'SERVER']],
  ]) {
    const w = makeWorld();
    w.syncListCard({ list_info: A }, true, 'SERVER');
    assert.ok(w.visible(), 'précondition');
    if (args[3]) w.pushMode = true;
    w.syncListCard(args[0], args[1], args[2]);
    assert.equal(w.visible(), false, `masquée : ${label}`);
    assert.equal(w.busy(), false);
  }
});

test('après masquage, un nouveau menu Ready repart de zéro', () => {
  const w = makeWorld();
  w.syncListCard({ list_info: A }, true, 'SERVER');
  w.syncListCard({ list_info: A }, true, 'TUNER');      // on quitte la liste
  assert.equal(w.visible(), false);
  w.syncListCard({ list_info: MENU('Album', [], 'Busy') }, true, 'SERVER');
  assert.equal(w.visible(), false, 'pas de carte vide grisée sans contenu précédent');
  w.syncListCard({ list_info: B }, true, 'SERVER');
  assert.ok(w.visible());
  assert.deepEqual(w.menuTxt(), ['Piste 1', '▸ Piste 2']);
});

test('ligne Unselectable sans texte → ignorée, ligne non selectable → non cliquable', () => {
  const w = makeWorld();
  const m = { menu_status: 'Ready', menu_name: 'X', cursor_line: '2', lines: [
    { line: 1, txt: '', attribute: 'Unselectable' },
    { line: 2, txt: 'Lecture', attribute: 'Selectable' },
    { line: 3, txt: 'Dossier vide', attribute: 'Unselectable' },
  ] };
  w.syncListCard({ list_info: m }, true, 'SERVER');
  const kids = [...w.el('menu').children];
  assert.deepEqual(kids.map(d => d.textContent), ['▸ Lecture', 'Dossier vide']);
  assert.equal(typeof kids[0].onclick, 'function');
  assert.equal(kids[1].onclick, undefined, 'une ligne Unselectable ne déclenche rien');
});

/* --- marqueur « en lecture » (le curseur YNC ne suit pas la piste) --------- */
const TRACKS = MENU('Album', [
  { txt: 'Intro' }, { txt: 'One More Time' }, { txt: 'Digital Love', sel: true }, { txt: 'Harder Better' },
]);

test('sans métadonnées → aucune ligne marquée, le curseur reste seul repère', () => {
  const w = makeWorld();
  w.syncListCard({ list_info: TRACKS }, true, 'SERVER');
  assert.deepEqual(w.marked(), []);
  assert.deepEqual(w.menuCls().filter(c => /\bsel\b/.test(c)).length, 1, 'curseur inchangé');
});

test('piste en lecture → marquée, et le curseur de navigation ne bouge pas', () => {
  const w = makeWorld();
  w.nowPlaying({ song: 'One More Time', artist: 'Daft Punk', album: 'Discovery' });
  w.syncListCard({ list_info: TRACKS }, true, 'SERVER');
  assert.deepEqual(w.marked(), ['One More Time'], 'la ligne qui joue, pas celle cliquée');
  assert.deepEqual(w.menuCls(), ['li', 'li cur', 'li sel', 'li'], 'cur (lecture) ≠ sel (curseur)');
  assert.equal(w.el('menu').children[1].title, 'En lecture');
});

test('changement de piste → le marqueur suit au prochain rendu', () => {
  const w = makeWorld();
  w.nowPlaying({ song: 'One More Time' });
  w.syncListCard({ list_info: TRACKS }, true, 'SERVER');
  assert.deepEqual(w.marked(), ['One More Time']);
  w.nowPlaying({ song: 'Harder Better' });                  // fin de morceau / Skip Fwd
  w.syncListCard({ list_info: TRACKS }, true, 'SERVER');
  assert.deepEqual(w.marked(), ['Harder Better']);
  assert.equal(w.el('menu').children[2].className, 'li sel', 'le curseur, lui, est resté où on a cliqué');
});

test('lecture stoppée/pausée → marqueur atténué (npidle), métadonnées conservées', () => {
  const w = makeWorld();
  w.nowPlaying({ song: 'Digital Love' }, false);
  w.syncListCard({ list_info: TRACKS }, true, 'SERVER');
  assert.deepEqual(w.menuCls()[2], 'li sel cur npidle');
  assert.equal(w.el('menu').children[2].title, 'Dernier morceau chargé');
});

test('Txt tronqué par le firmware → reconnu par préfixe (≥ 6 car.)', () => {
  const w = makeWorld();
  w.nowPlaying({ song: 'Something About Us' });
  w.syncListCard({ list_info: MENU('Album', [{ txt: 'Intro' }, { txt: 'Something Abou' }]) }, true, 'SERVER');
  assert.deepEqual(w.marked(), ['Something Abou']);
});

test('titre court → pas de faux positif par préfixe', () => {
  const w = makeWorld();
  w.nowPlaying({ song: 'One' });
  w.syncListCard({ list_info: MENU('Album', [{ txt: 'One More Time' }, { txt: 'Only' }]) }, true, 'SERVER');
  assert.deepEqual(w.marked(), [], '« One » ne doit pas marquer « One More Time »');
});

test('piste absente du menu → repli sur l\'album, puis sur l\'artiste', () => {
  const w = makeWorld();
  w.nowPlaying({ song: 'Face to Face', album: 'Discovery', artist: 'Daft Punk' });
  w.syncListCard({ list_info: MENU('Musique', [{ txt: 'Daft Punk' }, { txt: 'Discovery' }, { txt: 'Homework' }]) }, true, 'SERVER');
  assert.deepEqual(w.marked(), ['Discovery'], 'niveau album : l\'album en lecture est marqué');
  w.nowPlaying({ song: 'Face to Face', album: 'Random Access' });
  w.syncListCard({ list_info: MENU('Musique', [{ txt: 'Daft Punk' }, { txt: 'Discovery' }]) }, true, 'SERVER');
  assert.deepEqual(w.marked(), [], 'album inconnu du menu → rien (pas de marqueur fantaisiste)');
});

test('webradio : le nom de la station marque la ligne', () => {
  const w = makeWorld();
  w.nowPlaying({ station: 'FIP', song: null });
  w.syncListCard({ list_info: MENU('Presets', [{ txt: 'TSF Jazz' }, { txt: 'FIP' }]) }, true, 'NET RADIO');
  assert.deepEqual(w.marked(), ['FIP']);
});

test('accents/casse/ponctuation ignorés dans la comparaison', () => {
  const w = makeWorld();
  w.nowPlaying({ song: "L'Été — Indien" });
  w.syncListCard({ list_info: MENU('Album', [{ txt: 'Joe Dassin' }, { txt: 'l ete indien' }]) }, true, 'SERVER');
  assert.deepEqual(w.marked(), ['l ete indien']);
});

test('lignes vides/Unselectable jamais marquées', () => {
  const w = makeWorld();
  w.nowPlaying({ song: '   ' });
  const m = { menu_status: 'Ready', menu_name: 'X', cursor_line: '1', lines: [
    { line: 1, txt: '', attribute: 'Unselectable' },
    { line: 2, txt: 'Dossier', attribute: 'Container' },
  ] };
  w.syncListCard({ list_info: m }, true, 'SERVER');
  assert.deepEqual(w.marked(), []);
});

test('quitter la lecture réseau → plus aucun marqueur', () => {
  const w = makeWorld();
  w.nowPlaying({ song: 'One More Time' });
  w.syncListCard({ list_info: TRACKS }, true, 'SERVER');
  assert.deepEqual(w.marked(), ['One More Time']);
  w.noteNowPlaying(null, false);                             // carte Lecture masquée
  w.syncListCard({ list_info: TRACKS }, true, 'SERVER');
  assert.deepEqual(w.marked(), []);
});

/* ------------------------------------------------------------------ runner */
let fail = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`  ok    ${name}`); }
  catch (e) { fail++; console.log(`  ÉCHEC ${name}\n          ${e.message.split('\n')[0]}`); }
}
console.log(fail ? `\n${fail} test(s) en échec sur ${tests.length}` : `\n${tests.length} tests passés`);
process.exit(fail ? 1 : 0);
