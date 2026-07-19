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

// --- Répartition view --------------------------------------------------

const MATRIX_AXES = ['gravity', 'domains', 'mechanisms', 'trajectory', 'stage', 'actor', 'dynamic'];
const matrixSel = { row: 'gravity', col: 'domains' };

/** Distribution of one axis over the current filters: [{code,n}] descending. */
function axisDistribution(field) {
  const { clauses, params } = matchClauses();
  if (axisMeta(field).multi_label) {
    const sql = `SELECT t.code AS code, COUNT(DISTINCT f.fiche_id) AS n
      FROM fiches f JOIN fiche_tags t ON t.fiche_id = f.fiche_id AND t.axis = '${field}'
      ${composeWhere(clauses)} GROUP BY t.code ORDER BY n DESC`;
    return runQuery(sql, params);
  }
  return runQuery(`SELECT f.${field} AS code, COUNT(*) AS n FROM fiches f
    ${composeWhere(clauses)} GROUP BY f.${field} ORDER BY n DESC`, params);
}

/** Cross-tab {r,c,n} for two axes over the current filters. */
function matrixData(rowAxis, colAxis) {
  const { clauses, params } = matchClauses();
  const rowMulti = axisMeta(rowAxis).multi_label;
  const colMulti = axisMeta(colAxis).multi_label;
  const rowExpr = rowMulti ? 'tr.code' : `f.${rowAxis}`;
  const colExpr = colMulti ? 'tc.code' : `f.${colAxis}`;
  let joins = '';
  if (rowMulti) joins += ` JOIN fiche_tags tr ON tr.fiche_id = f.fiche_id AND tr.axis = '${rowAxis}'`;
  if (colMulti) joins += ` JOIN fiche_tags tc ON tc.fiche_id = f.fiche_id AND tc.axis = '${colAxis}'`;
  const sql = `SELECT ${rowExpr} AS r, ${colExpr} AS c, COUNT(DISTINCT f.fiche_id) AS n
    FROM fiches f ${joins} ${composeWhere(clauses)} GROUP BY r, c`;
  return runQuery(sql, params).filter((x) => x.r && x.c);
}

function renderRepartition() {
  const host = document.getElementById('view-repartition');
  host.innerHTML = '';

  for (const field of PRIMARY) {
    const meta = axisMeta(field);
    const dist = axisDistribution(field);
    const max = dist.reduce((m, d) => Math.max(m, d.n), 0) || 1;
    const block = document.createElement('div');
    block.className = 'repart__axis';
    const h = document.createElement('h3');
    h.textContent = meta.name;
    block.appendChild(h);
    for (const d of dist) {
      const bar = document.createElement('div');
      bar.className = 'repart__bar';
      const lbl = document.createElement('span');
      lbl.className = 'repart__lbl';
      lbl.textContent = `${d.code} · ${TAXO.labels[d.code] || ''}`;
      const track = document.createElement('span');
      track.className = 'repart__track';
      const fill = document.createElement('span');
      fill.className = 'repart__fill';
      fill.style.width = `${(d.n / max * 100).toFixed(1)}%`;
      track.appendChild(fill);
      const num = document.createElement('span');
      num.className = 'repart__n';
      num.textContent = d.n;
      bar.append(lbl, track, num);
      block.appendChild(bar);
    }
    host.appendChild(block);
  }

  renderMatrix(host);
}

function renderMatrix(host) {
  const wrap = document.createElement('div');
  wrap.className = 'repart__matrix';
  const title = document.createElement('h3');
  title.textContent = 'Matrice croisée';
  wrap.appendChild(title);

  const controls = document.createElement('div');
  controls.className = 'repart__mctrl';
  const opts = (sel) => MATRIX_AXES
    .map((f) => `<option value="${f}"${f === sel ? ' selected' : ''}>${axisMeta(f).name}</option>`).join('');
  controls.innerHTML =
    `<label>Lignes <select id="mrow">${opts(matrixSel.row)}</select></label>` +
    `<label>Colonnes <select id="mcol">${opts(matrixSel.col)}</select></label>`;
  wrap.appendChild(controls);
  const note = document.createElement('p');
  note.className = 'repart__mnote';
  note.textContent = 'Cliquez une cellule pour filtrer sur ce croisement et voir les fiches.';
  wrap.appendChild(note);
  const tableHost = document.createElement('div');
  tableHost.className = 'repart__mtable';
  wrap.appendChild(tableHost);
  host.appendChild(wrap);

  const rowSel = controls.querySelector('#mrow');
  const colSel = controls.querySelector('#mcol');
  rowSel.addEventListener('change', () => { matrixSel.row = rowSel.value; drawTable(); });
  colSel.addEventListener('change', () => { matrixSel.col = colSel.value; drawTable(); });

  function drawTable() {
    const rowVals = axisMeta(matrixSel.row).codes.map((c) => c.code);
    const colVals = axisMeta(matrixSel.col).codes.map((c) => c.code);
    const { grid, rowTotals, colTotals } = pivotMatrix(matrixData(matrixSel.row, matrixSel.col), rowVals, colVals);
    // No grand/row/col totals shown: they inflate for multi-label axes.
    const rKeep = rowVals.map((_, i) => rowTotals[i] > 0);
    const cKeep = colVals.map((_, j) => colTotals[j] > 0);
    let html = '<table><thead><tr><th></th>';
    colVals.forEach((c, j) => { if (cKeep[j]) html += `<th title="${TAXO.labels[c] || ''}">${c}</th>`; });
    html += '</tr></thead><tbody>';
    rowVals.forEach((r, i) => {
      if (!rKeep[i]) return;
      html += `<tr><th title="${TAXO.labels[r] || ''}">${r}</th>`;
      colVals.forEach((c, j) => {
        if (!cKeep[j]) return;
        const n = grid[i][j];
        html += `<td class="repart__cell${n ? '' : ' repart__cell--0'}" data-r="${r}" data-c="${c}">${n || ''}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    tableHost.innerHTML = html;
    tableHost.querySelectorAll('.repart__cell:not(.repart__cell--0)').forEach((td) => {
      td.addEventListener('click', () => {
        state.filters[matrixSel.row] = [td.dataset.r]; // replace (D4-A)
        state.filters[matrixSel.col] = [td.dataset.c];
        onFiltersChanged();
        switchView('liste');
      });
    });
  }
  drawTable();
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
