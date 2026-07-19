/**
 * explorer.js — Faceted tag explorer over fiches.sqlite (sql.js).
 *
 * Third /rapports/ mode. Liste + Répartition views share one URL-persisted
 * filter state. Axis kind (single vs multi-label) is probed from the DB
 * (SELECT DISTINCT axis FROM fiche_tags), never hardcoded. The tagged corpus
 * (10 514 fiches) is the baseline: `f.gravity IS NOT NULL` — a valid proxy
 * because all single-label axes are NULL together (build-time invariant).
 */
import {
  buildFilterWhereClause, serializeFilterState, parseFilterState,
  formatSearchResult, rowsToCsv, pivotMatrix,
} from './lib.js?v=13';
import { fetchWithProgress } from './loader.js?v=13';

const SQLITE_URL = new URL('../carte/data/fiches.sqlite', import.meta.url).href;
const TAXO_URL = new URL('../carte/data/taxonomy-labels.json', import.meta.url).href;
const SQLJS_CDN = 'https://cdn.jsdelivr.net/npm/sql.js@1.12.0/dist/sql-wasm.js';
const SQLJS_WASM = 'https://cdn.jsdelivr.net/npm/sql.js@1.12.0/dist/sql-wasm.wasm';

const PRIMARY = ['gravity', 'domains', 'mechanisms', 'trajectory'];
const GRAVE_CODES = ['G4', 'G5', 'G6'];
const BASELINE = 'f.gravity IS NOT NULL'; // = the 10 514 tagged fiches (co-NULL invariant)

let db = null;
let TAXO = null;
let AXES = [];   // [{field, multi}] — kind from the DB probe
let FIELDS = []; // ordered axis fields
const state = { filters: {}, view: 'liste' };

// --- Loader UI ---------------------------------------------------------

const initLoading = document.getElementById('init-loading');
const initStatus = document.getElementById('init-status');
const initStep = document.getElementById('init-step');
const initProgress = document.getElementById('init-progress');

function setProgress(pct, label) {
  const clamped = Math.min(100, Math.round(pct));
  initStatus.textContent = label;
  initStep.textContent = `${clamped}%`;
  initProgress.style.width = `${clamped}%`;
}

// --- SQL ---------------------------------------------------------------

function runQuery(sql, params = []) {
  const res = db.exec(sql, params);
  if (!res.length) return [];
  const { columns, values } = res[0];
  return values.map((row) => Object.fromEntries(columns.map((c, i) => [c, row[i]])));
}

/** Clauses for the current filter state (bare, no WHERE). */
function matchClauses() {
  return buildFilterWhereClause(state.filters, AXES);
}

/** Compose a WHERE from the tagged baseline plus extra bare clauses. */
function composeWhere(extraClauses) {
  return 'WHERE ' + [BASELINE, ...extraClauses].join(' AND ');
}

function axisMeta(field) {
  return TAXO.axes.find((a) => a.field === field);
}

// --- Init --------------------------------------------------------------

async function init() {
  try {
    setProgress(5, 'Chargement du moteur SQL…');
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = SQLJS_CDN; s.onload = resolve; s.onerror = reject;
      document.head.appendChild(s);
    });
    const [SQL, taxo] = await Promise.all([
      initSqlJs({ locateFile: () => SQLJS_WASM }),
      fetch(TAXO_URL).then((r) => r.json()),
    ]);
    TAXO = taxo;

    setProgress(20, 'Téléchargement des données…');
    const dbBytes = await fetchWithProgress(SQLITE_URL, (received, total) => {
      setProgress(20 + (received / total) * 75, `Téléchargement des données… ${(received / 1048576).toFixed(1)} Mo`);
    });
    setProgress(98, 'Ouverture de la base…');
    db = new SQL.Database(dbBytes);

    // Axis kind is the DB's to decide (D12): axes present in fiche_tags are multi-label.
    const multiSet = new Set(runQuery('SELECT DISTINCT axis FROM fiche_tags').map((r) => r.axis));
    AXES = TAXO.axes.map((a) => ({ field: a.field, multi: multiSet.has(a.field) }));
    FIELDS = TAXO.axes.map((a) => a.field);
    state.filters = parseFilterState(location.hash, FIELDS);

    initLoading.hidden = true;
    document.getElementById('layout').hidden = false;
    renderFilterPanel();
    wireEvents();
    applyFilters();
  } catch (err) {
    initLoading.innerHTML = '<span style="color:var(--rust)">Erreur de chargement. Rechargez la page.</span>';
    console.error('explorer init:', err);
  }
}

// --- Live faceted counts ----------------------------------------------

/** Count fiches per code for `field`, given the other filters (this axis cleared). */
function facetCounts(field) {
  const probe = { ...state.filters, [field]: [] };
  const { clauses, params } = buildFilterWhereClause(probe, AXES);
  const meta = axisMeta(field);
  if (meta.multi_label) {
    const sql = `SELECT t.code AS code, COUNT(DISTINCT f.fiche_id) AS n
      FROM fiches f JOIN fiche_tags t ON t.fiche_id = f.fiche_id AND t.axis = '${field}'
      ${composeWhere(clauses)} GROUP BY t.code`;
    return Object.fromEntries(runQuery(sql, params).map((r) => [r.code, r.n]));
  }
  const sql = `SELECT f.${field} AS code, COUNT(*) AS n FROM fiches f
    ${composeWhere(clauses)} GROUP BY f.${field}`;
  return Object.fromEntries(runQuery(sql, params).map((r) => [r.code, r.n]));
}

function totalCount() {
  const { clauses, params } = matchClauses();
  return runQuery(`SELECT COUNT(*) AS n FROM fiches f ${composeWhere(clauses)}`, params)[0].n;
}

// --- Filter panel ------------------------------------------------------

function renderAxis(container, meta) {
  const counts = facetCounts(meta.field);
  const box = document.createElement('fieldset');
  box.className = 'facet';
  const legend = document.createElement('legend');
  legend.textContent = meta.name;
  box.appendChild(legend);

  if (meta.field === 'gravity') {
    const preset = document.createElement('button');
    preset.type = 'button';
    preset.className = 'facet__preset';
    preset.textContent = 'Graves (G4–G6)';
    preset.addEventListener('click', () => {
      state.filters.gravity = [...GRAVE_CODES];
      onFiltersChanged();
    });
    box.appendChild(preset);
  }

  for (const { code, label } of meta.codes) {
    const n = counts[code] || 0;
    const checked = state.filters[meta.field].includes(code);
    const row = document.createElement('label');
    row.className = 'facet__opt' + (n === 0 && !checked ? ' facet__opt--empty' : '');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = checked;
    cb.disabled = n === 0 && !checked;
    cb.addEventListener('change', () => {
      const arr = state.filters[meta.field];
      if (cb.checked) arr.push(code);
      else state.filters[meta.field] = arr.filter((c) => c !== code);
      onFiltersChanged();
    });
    row.appendChild(cb);
    const text = document.createElement('span');
    text.className = 'facet__label';
    text.textContent = `${code} · ${label}`;
    row.appendChild(text);
    const cnt = document.createElement('span');
    cnt.className = 'facet__count';
    cnt.textContent = n;
    row.appendChild(cnt);
    box.appendChild(row);
  }
  container.appendChild(box);
}

function renderFilterPanel() {
  const prim = document.getElementById('filters-primary');
  const sec = document.getElementById('filters-secondary');
  prim.innerHTML = '';
  sec.innerHTML = '';
  for (const meta of TAXO.axes) {
    renderAxis(PRIMARY.includes(meta.field) ? prim : sec, meta);
  }
}

function onFiltersChanged() {
  const hash = serializeFilterState(state.filters, FIELDS);
  history.replaceState(null, '', hash ? '#' + hash : location.pathname + location.search);
  renderFilterPanel(); // recompute live counts
  applyFilters();
}

function applyFilters() {
  document.getElementById('headline').textContent = `${totalCount().toLocaleString('fr-FR')} fiches`;
  if (state.view === 'liste') renderListe();
  else renderRepartition();
}

// --- Liste view --------------------------------------------------------

const LISTE_CAP = 500; // display budget only — CSV export is uncapped

function listeRows() {
  const { clauses, params } = matchClauses();
  const sql = `SELECT fiche_id, nom_complet, titre, nom_commune, date_inspection, gravity
    FROM fiches f ${composeWhere(clauses)}
    ORDER BY (f.gravity IN ('G6','G5','G4')) DESC, date_inspection DESC
    LIMIT ${LISTE_CAP + 1}`;
  return runQuery(sql, params);
}

function renderListe() {
  const host = document.getElementById('view-liste');
  host.innerHTML = '';
  const rows = listeRows();
  const capped = rows.length > LISTE_CAP;
  const shown = capped ? rows.slice(0, LISTE_CAP) : rows;

  const bar = document.createElement('div');
  bar.className = 'liste__bar';
  const info = document.createElement('span');
  info.textContent = capped
    ? `${LISTE_CAP}+ fiches — affinez les filtres pour tout voir`
    : `${shown.length} fiche${shown.length > 1 ? 's' : ''}`;
  bar.appendChild(info);
  const csvBtn = document.createElement('button');
  csvBtn.type = 'button';
  csvBtn.className = 'liste__csv';
  csvBtn.textContent = 'Télécharger CSV';
  csvBtn.addEventListener('click', exportCsv);
  bar.appendChild(csvBtn);
  host.appendChild(bar);

  if (!shown.length) {
    const p = document.createElement('p');
    p.className = 'results__empty';
    p.textContent = 'Aucune fiche pour ces filtres.';
    host.appendChild(p);
    return;
  }

  const frag = document.createDocumentFragment();
  for (const row of shown) {
    const { title, subtitle } = formatSearchResult(row);
    const a = document.createElement('a');
    a.className = 'result-item';
    a.href = './#' + row.fiche_id;
    const h = document.createElement('p');
    h.className = 'result-item__title';
    h.textContent = title;
    a.appendChild(h);
    if (subtitle) {
      const sub = document.createElement('p');
      sub.className = 'result-item__subtitle';
      sub.textContent = subtitle;
      a.appendChild(sub);
    }
    if (row.gravity) {
      const b = document.createElement('span');
      b.className = 'result-item__badge';
      b.textContent = `${row.gravity} · ${TAXO.labels[row.gravity] || ''}`.trim();
      a.appendChild(b);
    }
    frag.appendChild(a);
  }
  host.appendChild(frag);
}

function exportCsv() {
  const { clauses, params } = matchClauses();
  const sql = `SELECT fiche_id, nom_complet, nom_commune, date_inspection, gravity, trajectory
    FROM fiches f ${composeWhere(clauses)} ORDER BY date_inspection DESC`;
  const rows = runQuery(sql, params);
  const csv = rowsToCsv(rows, [
    { key: 'fiche_id', header: 'fiche_id' },
    { key: 'nom_complet', header: 'installation' },
    { key: 'nom_commune', header: 'commune' },
    { key: 'date_inspection', header: 'date' },
    { key: 'gravity', header: 'gravite' },
    { key: 'trajectory', header: 'trajectoire' },
  ]);
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'explorer-icpe.csv';
  a.click();
  URL.revokeObjectURL(url);
}

// --- Répartition view (Phase 7 replaces this stub) --------------------
function renderRepartition() {
  document.getElementById('view-repartition').textContent = '(répartition — phase 7)';
}

// --- Events ------------------------------------------------------------

function wireEvents() {
  document.getElementById('reset-filters').addEventListener('click', () => {
    state.filters = parseFilterState('', FIELDS);
    onFiltersChanged();
  });
  document.getElementById('tab-liste').addEventListener('click', () => switchView('liste'));
  document.getElementById('tab-repartition').addEventListener('click', () => switchView('repartition'));
}

function switchView(view) {
  state.view = view;
  document.getElementById('tab-liste').classList.toggle('active', view === 'liste');
  document.getElementById('tab-repartition').classList.toggle('active', view === 'repartition');
  document.getElementById('view-liste').hidden = view !== 'liste';
  document.getElementById('view-repartition').hidden = view !== 'repartition';
  applyFilters();
}

init();
