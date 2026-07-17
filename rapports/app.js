/**
 * app.js — Verification page for /rapports/.
 *
 * Uses sql.js (SQLite WASM, ~1 MB) for SQL search on fiches.sqlite.
 * Uses PDF.js (desktop only) for cropped snippet rendering.
 * Mobile falls back to a link that opens the PDF at the right page.
 */

import {
  parseFicheIdFromHash,
  buildPdfUrl,
  buildSqlLikePattern,
  formatSearchResult,
  isMobileViewport,
  reflowText,
} from './lib.js';

// --- Configuration -------------------------------------------------------

const SQLITE_URL = new URL('../carte/data/fiches.sqlite', import.meta.url).href;
const SQLJS_CDN = 'https://cdn.jsdelivr.net/npm/sql.js@1.12.0/dist/sql-wasm.js';
const SQLJS_WASM = 'https://cdn.jsdelivr.net/npm/sql.js@1.12.0/dist/sql-wasm.wasm';
const PDFJS_CDN = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.min.mjs';
const PDFJS_WORKER_CDN = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.worker.min.mjs';
const SEARCH_DEBOUNCE_MS = 300;
const MAX_RESULTS = 100;
const CANVAS_WIDTH = 500;
const CANVAS_HEIGHT = 380;
const BBOX_PADDING = 0.15;

// --- State ---------------------------------------------------------------

let db = null; // sql.js Database instance
let pdfjsLib = null;
let pdfDocCache = {};  // url → PDFDocumentProxy
const PDF_CACHE_MAX = 5;
let debounceTimer = null;
let currentFicheId = null;

// --- DOM refs ------------------------------------------------------------

const searchInput = document.getElementById('search-input');
const searchHint = document.getElementById('search-hint');
const resultsEl = document.getElementById('results');
const resultsEmpty = document.getElementById('results-empty');
const detailEl = document.getElementById('detail');
const layoutEl = document.getElementById('layout');
const detailEmpty = document.getElementById('detail-empty');
const initLoading = document.getElementById('init-loading');
const initStatus = document.getElementById('init-status');

// Filters
const filterBar = document.getElementById('filter-bar');
const filterToggle = document.getElementById('filter-toggle');
const filterFields = document.getElementById('filter-fields');
const filterSuite = document.getElementById('filter-suite');
const filterCommune = document.getElementById('filter-commune');
const filterRegime = document.getElementById('filter-regime');
const filterSeveso = document.getElementById('filter-seveso');
const filterAnnee = document.getElementById('filter-annee');

const FILTER_SELECTS = [filterSuite, filterCommune, filterRegime, filterSeveso, filterAnnee];

// --- Filters (mobile toggle) ---------------------------------------------

filterToggle.addEventListener('click', () => {
  filterBar.classList.toggle('filter-bar--open');
  filterToggle.textContent = filterBar.classList.contains('filter-bar--open')
    ? 'Filtres ▴' : 'Filtres ▾';
});

// Show toggle only on mobile
if (window.matchMedia('(max-width: 719px)').matches) {
  filterToggle.hidden = false;
}

// --- Init ----------------------------------------------------------------

const initProgress = document.getElementById('init-progress');
const initStep = document.getElementById('init-step');

function setProgress(pct, label) {
  initStatus.textContent = label;
  initStep.textContent = `${pct}%`;
  initProgress.style.width = `${pct}%`;
}

/**
 * Fetch a URL with real download progress via ReadableStream.
 * Falls back to a plain fetch if Content-Length is missing.
 */
async function fetchWithProgress(url, onProgress) {
  const resp = await fetch(url);
  const total = +resp.headers.get('Content-Length');
  if (!total || !resp.body) return new Uint8Array(await resp.arrayBuffer());
  const reader = resp.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(received, total);
  }
  const result = new Uint8Array(received);
  let pos = 0;
  for (const chunk of chunks) { result.set(chunk, pos); pos += chunk.length; }
  return result;
}

/**
 * Run a SQL query on the sql.js database. Returns an array of objects.
 * sql.js returns {columns: [...], values: [[...], ...]}, we convert
 * to [{col: val, ...}, ...] for compatibility with the render code.
 */
function query(sql) {
  const results = db.exec(sql);
  if (results.length === 0) return [];
  const { columns, values } = results[0];
  return values.map((row) => {
    const obj = {};
    for (let i = 0; i < columns.length; i++) obj[columns[i]] = row[i];
    return obj;
  });
}

async function init() {
  try {
    // Phase 1: load sql.js module (~1 MB WASM vs ~7 MB for DuckDB)
    setProgress(10, 'Chargement du moteur SQL…');
    // sql.js is a UMD module — load via script tag, then call initSqlJs
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = SQLJS_CDN;
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
    const SQL = await initSqlJs({ locateFile: () => SQLJS_WASM });

    // Phase 2: download the SQLite database with progress
    setProgress(30, 'Téléchargement des données…');
    const dbBytes = await fetchWithProgress(SQLITE_URL, (received, total) => {
      const pct = 30 + Math.round((received / total) * 60);
      const mb = (received / 1024 / 1024).toFixed(1);
      const totalMb = (total / 1024 / 1024).toFixed(0);
      setProgress(pct, `Téléchargement des données… ${mb} / ${totalMb} Mo`);
    });

    // Phase 3: open database
    setProgress(95, 'Ouverture de la base…');
    db = new SQL.Database(dbBytes);

    const count = query("SELECT COUNT(*) AS n FROM fiches")[0].n;
    setProgress(100, 'Prêt');

    // Show interface
    initLoading.hidden = true;
    layoutEl.hidden = false;
    searchHint.textContent = Number(count).toLocaleString('fr-FR') + ' fiches';
    searchInput.disabled = false;
    searchInput.focus();

    // Populate filters and initial results
    populateFilters();
    const hashId = parseFicheIdFromHash(location.hash);
    if (hashId) {
      loadFiche(hashId);
    } else {
      loadRecentFiches();
    }
  } catch (err) {
    initStatus.textContent = 'Erreur de chargement — rechargez la page';
    initStep.textContent = '';
    initProgress.style.width = '0%';
    console.error('SQL init failed:', err);
  }
}

// --- Filters -------------------------------------------------------------

function populateFilters() {
  if (!db) return;
  const queries = [
    { el: filterSuite, sql: "SELECT DISTINCT type_suite AS v FROM fiches WHERE type_suite IS NOT NULL ORDER BY v" },
    { el: filterCommune, sql: "SELECT DISTINCT nom_commune AS v FROM fiches WHERE nom_commune IS NOT NULL ORDER BY v" },
    { el: filterRegime, sql: "SELECT DISTINCT regime_icpe AS v FROM fiches WHERE regime_icpe IS NOT NULL ORDER BY v" },
    { el: filterSeveso, sql: "SELECT DISTINCT categorie_seveso AS v FROM fiches WHERE categorie_seveso IS NOT NULL ORDER BY v" },
    // SQLite: extract year with substr (no YEAR() function)
    { el: filterAnnee, sql: "SELECT DISTINCT SUBSTR(date_inspection, 1, 4) AS v FROM fiches WHERE date_inspection IS NOT NULL AND date_inspection != '' ORDER BY v DESC" },
  ];
  for (const { el, sql } of queries) {
    try {
      for (const row of query(sql)) {
        if (!row.v) continue;
        const opt = document.createElement('option');
        opt.value = row.v;
        opt.textContent = row.v;
        el.appendChild(opt);
      }
      el.disabled = false;
    } catch (err) {
      console.warn('Filter populate error:', err);
    }
  }
  // Wire change events
  for (const sel of FILTER_SELECTS) {
    sel.addEventListener('change', () => {
      sel.classList.toggle('filter-bar__select--active', sel.value !== '');
      clearTimeout(debounceTimer);
      runSearch();
    });
  }
}

function buildFilterWhere() {
  const clauses = [];
  if (filterSuite.value) {
    clauses.push(`type_suite = '${filterSuite.value.replace(/'/g, "''")}'`);
  }
  if (filterCommune.value) {
    clauses.push(`nom_commune = '${filterCommune.value.replace(/'/g, "''")}'`);
  }
  if (filterRegime.value) {
    clauses.push(`regime_icpe = '${filterRegime.value.replace(/'/g, "''")}'`);
  }
  if (filterSeveso.value) {
    clauses.push(`categorie_seveso = '${filterSeveso.value.replace(/'/g, "''")}'`);
  }
  if (filterAnnee.value) {
    clauses.push(`date_inspection LIKE '${filterAnnee.value}%'`);
  }
  return clauses.length > 0 ? clauses.join(' AND ') : '';
}

// --- Initial load --------------------------------------------------------

function loadRecentFiches() {
  if (!db) return;
  try {
    const filterWhere = buildFilterWhere();
    const where = filterWhere
      ? `WHERE fiche_num IS NOT NULL AND ${filterWhere}`
      : 'WHERE fiche_num IS NOT NULL';
    const rows = query(`
      SELECT fiche_id, titre, nom_complet, nom_commune, date_inspection,
             type_suite, extraction_method, fiche_num
      FROM fiches
      ${where}
      ORDER BY date_inspection DESC
      LIMIT 50
    `);
    renderResults(rows);
    const filterActive = FILTER_SELECTS.some(s => s.value !== '');
    searchHint.textContent = filterActive
      ? rows.length + ' résultat' + (rows.length > 1 ? 's' : '') + ' (filtrés)'
      : rows.length + ' plus récentes';
  } catch (err) {
    console.warn('Recent fiches load failed:', err);
  }
}

// --- Search --------------------------------------------------------------

searchInput.addEventListener('input', () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runSearch, SEARCH_DEBOUNCE_MS);
});

searchInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    clearTimeout(debounceTimer);
    runSearch();
  }
});

function runSearch() {
  const term = searchInput.value.trim();
  const filterWhere = buildFilterWhere();

  if (!term && !filterWhere) {
    loadRecentFiches();
    return;
  }
  if (!db) return;

  try {
    let whereClauses = [];

    if (term) {
      const safeTerm = term.replace(/'/g, "''").replace(/%/g, '\\%').replace(/_/g, '\\_');
      const pattern = `%${safeTerm}%`;
      const fullText = document.getElementById('fulltext-toggle')?.checked ?? false;
      const bodyParts = fullText
        ? `OR LOWER(body) LIKE '${pattern.toLowerCase()}' ESCAPE '\\'
           OR LOWER(COALESCE(constats_body, '')) LIKE '${pattern.toLowerCase()}' ESCAPE '\\'`
        : '';
      whereClauses.push(`(LOWER(COALESCE(titre, '')) LIKE '${pattern.toLowerCase()}' ESCAPE '\\'
         OR LOWER(nom_complet) LIKE '${pattern.toLowerCase()}' ESCAPE '\\'
         OR LOWER(COALESCE(nom_commune, '')) LIKE '${pattern.toLowerCase()}' ESCAPE '\\'
         OR LOWER(COALESCE(theme, '')) LIKE '${pattern.toLowerCase()}' ESCAPE '\\'
         ${bodyParts})`);
    }

    if (filterWhere) {
      whereClauses.push(filterWhere);
    }

    const where = whereClauses.length > 0 ? 'WHERE ' + whereClauses.join(' AND ') : '';
    const rows = query(`
      SELECT fiche_id, titre, nom_complet, nom_commune, date_inspection,
             type_suite, extraction_method, fiche_num
      FROM fiches
      ${where}
      LIMIT ${MAX_RESULTS}
    `);

    renderResults(rows);
    searchHint.textContent = rows.length >= MAX_RESULTS
      ? MAX_RESULTS + '+ résultats'
      : rows.length + ' résultat' + (rows.length > 1 ? 's' : '');
  } catch (err) {
    console.error('Search error:', err);
    searchHint.textContent = 'Erreur de recherche';
  }
}

function renderResults(rows) {
  resultsEl.innerHTML = '';
  if (rows.length === 0) {
    const p = document.createElement('p');
    p.className = 'results__empty';
    p.textContent = 'Aucun résultat pour cette recherche.';
    resultsEl.appendChild(p);
    return;
  }
  const frag = document.createDocumentFragment();
  for (const row of rows) {
    const { title, subtitle, badge } = formatSearchResult(row);
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'result-item' + (row.fiche_id === currentFicheId ? ' active' : '');
    item.dataset.ficheId = row.fiche_id;

    const h = document.createElement('p');
    h.className = 'result-item__title';
    h.textContent = title;
    item.appendChild(h);

    if (subtitle) {
      const sub = document.createElement('p');
      sub.className = 'result-item__subtitle';
      sub.textContent = subtitle;
      item.appendChild(sub);
    }
    if (badge) {
      const b = document.createElement('span');
      b.className = 'result-item__badge';
      if (/mise en demeure/i.test(badge)) b.className += ' result-item__badge--demeure';
      else if (badge !== 'Sans suite') b.className += ' result-item__badge--suite';
      b.textContent = badge;
      item.appendChild(b);
    }

    item.addEventListener('click', (e) => {
      e.preventDefault();
      history.replaceState(null, '', '#' + row.fiche_id);
      loadFiche(row.fiche_id);
    });
    frag.appendChild(item);
  }
  resultsEl.appendChild(frag);
}

// --- Detail panel --------------------------------------------------------

window.addEventListener('hashchange', () => {
  const id = parseFicheIdFromHash(location.hash);
  if (id) loadFiche(id);
});

function loadFiche(ficheId) {
  if (!db) return;
  currentFicheId = ficheId;

  document.querySelectorAll('.result-item').forEach((el) => {
    el.classList.toggle('active', el.dataset.ficheId === ficheId);
  });

  layoutEl.classList.add('layout--detail');

  try {
    const safeId = ficheId.replace(/'/g, "''");
    const rows = query(`SELECT * FROM fiches WHERE fiche_id = '${safeId}'`);
    if (rows.length === 0) {
      detailEl.innerHTML = '<p class="detail__empty">Fiche introuvable.</p>';
      return;
    }
    renderDetail(rows[0]);
  } catch (err) {
    console.error('Load fiche error:', err);
    detailEl.innerHTML = '<p class="detail__empty">Erreur de chargement.</p>';
  }
}

function renderDetail(row) {
  detailEl.innerHTML = '';

  // Mobile: switch to detail view
  layoutEl.classList.add('layout--detail');

  // Back button (visible only on mobile via CSS)
  const backBtn = document.createElement('button');
  backBtn.className = 'detail__back';
  backBtn.textContent = '← résultats';
  backBtn.addEventListener('click', () => {
    layoutEl.classList.remove('layout--detail');
    detailEl.innerHTML = '';
    currentFicheId = null;
    history.replaceState(null, '', location.pathname + location.search);
  });
  detailEl.appendChild(backBtn);

  // Header
  const header = document.createElement('div');
  header.className = 'detail__header';

  const title = document.createElement('h2');
  title.className = 'detail__title';
  title.textContent = row.fiche_num
    ? `Fiche N° ${row.fiche_num} — ${row.titre || '(sans titre)'}`
    : `${row.nom_complet} — rapport complet`;
  header.appendChild(title);

  const meta = document.createElement('div');
  meta.className = 'detail__meta';
  const metaParts = [
    row.nom_commune, row.date_inspection, row.id_icpe ? 'ICPE ' + row.id_icpe : '',
    row.siret ? 'SIRET ' + row.siret : '',
  ].filter(Boolean);
  metaParts.forEach((text) => {
    const s = document.createElement('span');
    s.textContent = text;
    meta.appendChild(s);
  });
  header.appendChild(meta);
  detailEl.appendChild(header);

  // Structured fields (only for fiches, not prose)
  if (row.fiche_num) {
    const fields = document.createElement('div');
    fields.className = 'detail__fields';
    const fieldDefs = [
      ['Thème', row.theme],
      ['Type de suites', row.type_suite],
      ['Déjà contrôlé', row.deja_controle],
      ['Référence', row.reference_reglementaire],
    ];
    for (const [label, value] of fieldDefs) {
      if (!value) continue;
      const f = document.createElement('div');
      f.className = 'field';
      const fl = document.createElement('div');
      fl.className = 'field__label';
      fl.textContent = label;
      f.appendChild(fl);
      const fv = document.createElement('div');
      fv.className = 'field__value';
      fv.textContent = reflowText(value);
      f.appendChild(fv);
      fields.appendChild(f);
    }
    // Constats (full width, reflowed to remove layout line breaks)
    if (row.constats_body) {
      const f = document.createElement('div');
      f.className = 'field field--full';
      const fl = document.createElement('div');
      fl.className = 'field__label';
      fl.textContent = 'Constats';
      f.appendChild(fl);
      const fv = document.createElement('div');
      fv.className = 'field__value';
      const reflowed = reflowText(row.constats_body);
      fv.textContent = reflowed.length > 2000
        ? reflowed.slice(0, 2000) + '…'
        : reflowed;
      fv.style.whiteSpace = 'pre-wrap';
      f.appendChild(fv);
      fields.appendChild(f);
    }
    detailEl.appendChild(fields);
  }

  // PDF snippet or link
  let regions = row.regions;
  if (typeof regions === 'string') {
    try { regions = JSON.parse(regions); } catch { regions = null; }
  }
  const firstRegion = Array.isArray(regions) && regions.length > 0 ? regions[0] : null;
  const page = firstRegion ? firstRegion.page : 1;
  const pdfUrl = row.url_pages || '';

  if (isMobileViewport() || !pdfUrl) {
    // Mobile: link only
    if (pdfUrl) {
      const link = document.createElement('a');
      link.className = 'pdf-link';
      link.href = buildPdfUrl(pdfUrl, page);
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = `📄 Page ${page} — ouvrir le rapport`;
      detailEl.appendChild(link);
    }
  } else {
    // Desktop: canvas snippet
    const snippet = document.createElement('div');
    snippet.className = 'snippet';

    const canvas = document.createElement('canvas');
    canvas.className = 'snippet__canvas';
    // Dimensions set later by renderSnippet after we know the content aspect ratio.
    // Start with placeholder size; renderSnippet will resize.
    canvas.style.width = CANVAS_WIDTH + 'px';
    canvas.style.maxHeight = '600px';
    canvas.title = 'Cliquer pour ouvrir le PDF complet';
    canvas.addEventListener('click', () => {
      window.open(buildPdfUrl(pdfUrl, page), '_blank', 'noopener');
    });
    snippet.appendChild(canvas);

    const caption = document.createElement('div');
    caption.className = 'snippet__caption';
    caption.append(`Page ${page} du rapport · `);
    const captionLink = document.createElement('a');
    captionLink.href = buildPdfUrl(pdfUrl, page);
    captionLink.target = '_blank';
    captionLink.rel = 'noopener';
    captionLink.textContent = 'ouvrir le PDF complet →';
    caption.appendChild(captionLink);
    snippet.appendChild(caption);
    detailEl.appendChild(snippet);

    // Render async
    renderSnippet(canvas, pdfUrl, firstRegion);
  }

  // Context block
  const context = document.createElement('div');
  context.className = 'context';
  const contextTitle = document.createElement('div');
  contextTitle.className = 'context__title';
  contextTitle.textContent = 'Contexte installation';
  context.appendChild(contextTitle);
  const grid = document.createElement('div');
  grid.className = 'context__grid';
  const contextItems = [
    ['Régime', row.regime_icpe],
    ['Seveso', row.categorie_seveso],
    ['Commune', row.nom_commune],
    ['EPCI', row.epci_nom],
    ['Extraction', row.extraction_method],
    ['Source', row.source_pdf],
  ];
  for (const [label, value] of contextItems) {
    if (!value) continue;
    const s = document.createElement('span');
    const strong = document.createElement('strong');
    strong.textContent = label;
    s.appendChild(strong);
    s.append(` : ${value}`);
    grid.appendChild(s);
  }
  context.appendChild(grid);

  // Link to markdown
  if (row.url_markdown) {
    const mdLink = document.createElement('div');
    mdLink.style.marginTop = '12px';
    const mdA = document.createElement('a');
    mdA.href = row.url_markdown;
    mdA.target = '_blank';
    mdA.rel = 'noopener';
    mdA.style.cssText = 'color:var(--moss);font-size:13px;font-family:var(--font-body)';
    mdA.textContent = 'voir le markdown complet →';
    mdLink.appendChild(mdA);
    context.appendChild(mdLink);
  }
  detailEl.appendChild(context);
}

// --- PDF.js snippet rendering (desktop only) -----------------------------

async function loadPdfJs() {
  if (pdfjsLib) return pdfjsLib;
  const mod = await import(PDFJS_CDN);
  mod.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_CDN;
  pdfjsLib = mod;
  return mod;
}

function sizeCanvas(canvas, contentWidth, contentHeight) {
  /** Resize the canvas to fit CANVAS_WIDTH CSS px wide, preserving aspect ratio. */
  const dpr = window.devicePixelRatio || 1;
  const aspect = contentHeight / contentWidth;
  const cssW = CANVAS_WIDTH;
  const cssH = Math.round(Math.min(cssW * aspect, 600)); // cap at 600 CSS px
  canvas.width = cssW * dpr;
  canvas.height = cssH * dpr;
  canvas.style.width = cssW + 'px';
  canvas.style.height = cssH + 'px';
}

async function renderSnippet(canvas, pdfUrl, region) {
  // Placeholder while loading
  sizeCanvas(canvas, 3, 4); // default A4-ish ratio
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#f5f3ed';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#999';
  ctx.font = `${14 * (window.devicePixelRatio || 1)}px sans-serif`;
  ctx.textAlign = 'center';
  ctx.fillText('Chargement du PDF…', canvas.width / 2, canvas.height / 2);

  try {
    const lib = await loadPdfJs();
    if (!pdfDocCache[pdfUrl]) {
      // Evict oldest entry if cache is full
      const keys = Object.keys(pdfDocCache);
      if (keys.length >= PDF_CACHE_MAX) {
        const evict = keys[0];
        pdfDocCache[evict].destroy();
        delete pdfDocCache[evict];
      }
      pdfDocCache[pdfUrl] = await lib.getDocument(pdfUrl).promise;
    }
    const doc = pdfDocCache[pdfUrl];
    const pageNum = region ? region.page : 1;
    const page = await doc.getPage(pageNum);
    const renderScale = Math.max(2, window.devicePixelRatio || 1);
    const viewport = page.getViewport({ scale: renderScale });

    // Render full page to offscreen canvas
    const offscreen = document.createElement('canvas');
    offscreen.width = viewport.width;
    offscreen.height = viewport.height;
    const offCtx = offscreen.getContext('2d');
    await page.render({ canvasContext: offCtx, viewport }).promise;

    const pagePts = { width: page.view[2], height: page.view[3] };

    // Crop to bbox if available
    if (region && region.bbox && region.bbox.length === 4) {
      const [x0, y0, x1, y1] = region.bbox;
      const bw = x1 - x0;
      const bh = y1 - y0;
      const padX = bw * BBOX_PADDING;
      const padY = bh * BBOX_PADDING;
      const sx = Math.max(0, x0 - padX);
      const sy = Math.max(0, y0 - padY);
      const sw = Math.min(pagePts.width - sx, bw + 2 * padX);
      const sh = Math.min(pagePts.height - sy, bh + 2 * padY);

      // Size canvas to match the crop's aspect ratio
      sizeCanvas(canvas, sw, sh);
      const ctx2 = canvas.getContext('2d');
      ctx2.fillStyle = '#fdfbf4';
      ctx2.fillRect(0, 0, canvas.width, canvas.height);
      ctx2.drawImage(
        offscreen,
        sx * renderScale, sy * renderScale, sw * renderScale, sh * renderScale,
        0, 0, canvas.width, canvas.height,
      );
      return;
    }

    // Fallback: render full page, sized to page aspect ratio
    sizeCanvas(canvas, pagePts.width, pagePts.height);
    const ctx2 = canvas.getContext('2d');
    ctx2.fillStyle = '#fdfbf4';
    ctx2.fillRect(0, 0, canvas.width, canvas.height);
    ctx2.drawImage(offscreen, 0, 0, canvas.width, canvas.height);
  } catch (err) {
    console.warn('PDF render failed:', err);
    const ctx2 = canvas.getContext('2d');
    ctx2.fillStyle = '#f5f3ed';
    ctx2.fillRect(0, 0, canvas.width, canvas.height);
    ctx2.fillStyle = '#999';
    ctx2.font = `${14 * (window.devicePixelRatio || 1)}px sans-serif`;
    ctx2.textAlign = 'center';
    ctx2.fillText('PDF indisponible', canvas.width / 2, canvas.height / 2);
  }
}

// --- Boot ----------------------------------------------------------------

init();
