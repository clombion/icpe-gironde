/**
 * lib.js — Pure functions for the /rapports/ verification page.
 *
 * Testable in rapports/test.html via console.assert.
 * No DOM access, no side effects, no imports.
 */

// --- Fiche ID parsing --------------------------------------------------

/**
 * Parse a fiche_id from a URL hash fragment.
 * E.g. "#ACME_123_2024-01-01_12345_f03" → "ACME_123_2024-01-01_12345_f03"
 * Returns null if hash is empty or doesn't look like a fiche_id.
 */
export function parseFicheIdFromHash(hash) {
  if (!hash || hash === '#') return null;
  const id = hash.startsWith('#') ? hash.slice(1) : hash;
  // Must end with _fNN or _prose
  if (/_f\d{2,}$/.test(id) || /_prose$/.test(id)) return id;
  return null;
}

// --- PDF URL building --------------------------------------------------

/**
 * Build a PDF URL with page anchor.
 * @param {string} baseUrl - Full URL to the PDF (url_pages from parquet)
 * @param {number} page - 1-based page number
 * @returns {string} URL with #page=N appended
 */
export function buildPdfUrl(baseUrl, page) {
  if (!baseUrl) return '';
  const p = Math.max(1, Math.floor(page || 1));
  return baseUrl + '#page=' + p;
}

// --- SQL building ------------------------------------------------------

/**
 * Build a SQL LIKE clause for search.
 * Escapes % and _ in the user's query, wraps in %...%.
 * @param {string} term - Raw search input
 * @returns {string} The LIKE pattern
 */
export function buildSqlLikePattern(term) {
  if (!term) return '%%';
  const escaped = term.replace(/%/g, '\\%').replace(/_/g, '\\_');
  return '%' + escaped + '%';
}

// --- Canvas coordinates ------------------------------------------------

/**
 * Convert a PDF bbox (points) to canvas pixel coordinates.
 * @param {number[]} bbox - [x0, y0, x1, y1] in PDF points
 * @param {{width: number, height: number}} pagePts - Page size in points
 * @param {{width: number, height: number}} canvasPx - Canvas size in pixels
 * @param {number} padding - Padding ratio (e.g. 0.15 for 15%)
 * @returns {{sx: number, sy: number, sw: number, sh: number, dx: number, dy: number, dw: number, dh: number}}
 */
export function canvasCoordinatesFromBbox(bbox, pagePts, canvasPx, padding) {
  if (!bbox || bbox.length < 4) return null;
  const [x0, y0, x1, y1] = bbox;
  const bw = x1 - x0;
  const bh = y1 - y0;
  const padX = bw * (padding ?? 0.15);
  const padY = bh * (padding ?? 0.15);

  // Source region in page points (clamped to page bounds)
  const sx = Math.max(0, x0 - padX);
  const sy = Math.max(0, y0 - padY);
  const sw = Math.min(pagePts.width - sx, bw + 2 * padX);
  const sh = Math.min(pagePts.height - sy, bh + 2 * padY);

  // Scale to fit canvas, preserving aspect ratio
  const scale = Math.min(canvasPx.width / sw, canvasPx.height / sh);
  const dw = sw * scale;
  const dh = sh * scale;
  const dx = (canvasPx.width - dw) / 2;
  const dy = (canvasPx.height - dh) / 2;

  return { sx, sy, sw, sh, dx, dy, dw, dh };
}

// --- Search result formatting ------------------------------------------

/**
 * Format a row from the parquet search results for display.
 * @param {object} row - A row from SQL query result
 * @returns {{title: string, subtitle: string, badge: string}}
 */
export function formatSearchResult(row) {
  const nom = row.nom_complet || '';
  const titre = row.titre || '(rapport complet)';
  const commune = row.nom_commune || '';
  const date = row.date_inspection || '';
  const suite = row.type_suite || '';
  // Title = installation name, subtitle = fiche titre + commune + date
  const title = nom || titre;
  const parts = [];
  if (nom && titre !== '(rapport complet)') parts.push(titre);
  if (commune) parts.push(commune);
  if (date) parts.push(date);
  const subtitle = parts.join(' · ');
  return { title, subtitle, badge: suite };
}

/**
 * Reflow text extracted from PDFs: join lines that were broken by
 * page layout (justified text, columns) into continuous paragraphs.
 *
 * Rules:
 * - A line ending with a lowercase letter/comma/semicolon followed by
 *   a line starting with a lowercase letter → join with space (layout break)
 * - A line ending with a hyphen followed by a lowercase letter → join
 *   without space (word hyphenation)
 * - Double newlines (blank lines) are preserved as paragraph separators
 * - Lines starting with • or - are preserved as list items
 *
 * @param {string} text - Raw extracted text with layout line breaks
 * @returns {string} Reflowed text with natural paragraphs
 */
export function reflowText(text) {
  if (!text) return '';
  // Preserve double newlines as paragraph markers
  const paragraphs = text.split(/\n\s*\n/);
  return paragraphs.map((para) => {
    const lines = para.split('\n');
    if (lines.length <= 1) return para;
    let result = lines[0];
    for (let i = 1; i < lines.length; i++) {
      const prev = result;
      const next = lines[i];
      if (!next.trim()) continue;
      // Preserve list items
      if (/^\s*[•\-–—]\s/.test(next)) {
        result += '\n' + next;
        continue;
      }
      // Hyphenated word break: join without space
      if (/[a-zàâéèêëïîôùûüç]-$/.test(prev.trimEnd())) {
        result = prev.trimEnd().slice(0, -1) + next.trimStart();
        continue;
      }
      // Layout line break: lowercase/punctuation end → lowercase start
      if (/[a-zàâéèêëïîôùûüç,;:)]$/.test(prev.trimEnd()) &&
          /^[a-zàâéèêëïîôùûüçl'(«]/.test(next.trimStart())) {
        result += ' ' + next.trimStart();
        continue;
      }
      // Default: keep the line break
      result += '\n' + next;
    }
    return result;
  }).join('\n\n');
}

/**
 * Check if current viewport is mobile (<720px).
 * @returns {boolean}
 */
export function isMobileViewport() {
  return window.matchMedia('(max-width: 719px)').matches;
}

// --- Explorer: filter WHERE building -----------------------------------

/**
 * Build parameterized filter clauses from a filter state.
 * Returns BARE clauses (no leading WHERE) so callers own the WHERE and can
 * prepend a baseline predicate (matches app.js buildFilterWhere convention).
 * AND across axes, OR (IN / EXISTS…IN) within an axis. Base query aliases fiches as `f`.
 * @param {Object<string,string[]>} filters - axis field → selected codes
 * @param {{field:string,multi:boolean}[]} axes - ordered axis kinds (from taxonomy)
 * @returns {{clauses: string[], params: string[]}}
 */
export function buildFilterWhereClause(filters, axes) {
  const clauses = [];
  const params = [];
  for (const { field, multi } of axes) {
    const codes = (filters && filters[field]) || [];
    if (!codes.length) continue;
    const placeholders = codes.map(() => '?').join(',');
    if (multi) {
      clauses.push(`EXISTS (SELECT 1 FROM fiche_tags t WHERE t.fiche_id = f.fiche_id AND t.axis = '${field}' AND t.code IN (${placeholders}))`);
    } else {
      clauses.push(`f.${field} IN (${placeholders})`);
    }
    params.push(...codes);
  }
  return { clauses, params };
}

// --- Explorer: URL filter state ----------------------------------------

/**
 * Serialize a filter state to a URL hash fragment (no leading '#').
 * @param {Object<string,string[]>} filters
 * @param {string[]} fields - ordered axis-field list (from taxonomy)
 * @returns {string}
 */
export function serializeFilterState(filters, fields) {
  const parts = [];
  for (const field of fields) {
    const codes = (filters && filters[field]) || [];
    if (!codes.length) continue;
    parts.push(`${field}=${codes.map(encodeURIComponent).join(',')}`);
  }
  return parts.join('&');
}

/**
 * Parse a URL hash fragment into a filter state with every field key present.
 * @param {string} hash
 * @param {string[]} fields - ordered axis-field list (from taxonomy)
 * @returns {Object<string,string[]>}
 */
export function parseFilterState(hash, fields) {
  const out = {};
  for (const field of fields) out[field] = [];
  if (!hash) return out;
  const raw = hash.startsWith('#') ? hash.slice(1) : hash;
  for (const pair of raw.split('&')) {
    if (!pair) continue;
    const eq = pair.indexOf('=');
    if (eq < 0) continue;
    const field = pair.slice(0, eq);
    if (!Object.prototype.hasOwnProperty.call(out, field)) continue;
    out[field] = pair.slice(eq + 1).split(',').filter(Boolean).map(decodeURIComponent);
  }
  return out;
}

// --- Explorer: matrix pivot --------------------------------------------

/**
 * Pivot {r,c,n} rows into a dense grid over the given ordered axis values.
 * Rows whose r/c fall outside the value lists are ignored.
 * @param {{r:string,c:string,n:number}[]} rows
 * @param {string[]} rowValues
 * @param {string[]} colValues
 * @returns {{grid:number[][], rowTotals:number[], colTotals:number[], total:number}}
 */
export function pivotMatrix(rows, rowValues, colValues) {
  const rIdx = new Map(rowValues.map((v, i) => [v, i]));
  const cIdx = new Map(colValues.map((v, i) => [v, i]));
  const grid = rowValues.map(() => colValues.map(() => 0));
  for (const { r, c, n } of rows) {
    if (!rIdx.has(r) || !cIdx.has(c)) continue;
    grid[rIdx.get(r)][cIdx.get(c)] += n;
  }
  const rowTotals = grid.map((row) => row.reduce((a, b) => a + b, 0));
  const colTotals = colValues.map((_, j) => grid.reduce((a, row) => a + row[j], 0));
  const total = rowTotals.reduce((a, b) => a + b, 0);
  return { grid, rowTotals, colTotals, total };
}

// --- Explorer: CSV -----------------------------------------------------

function csvCell(value) {
  const s = value == null ? '' : String(value);
  return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

/**
 * Serialize rows to CSV. columns = [{key, header}]. CRLF line endings.
 * A value containing " , or a newline is wrapped in double quotes with internal " doubled.
 * @param {Object[]} rows
 * @param {{key:string,header:string}[]} columns
 * @returns {string}
 */
export function rowsToCsv(rows, columns) {
  const head = columns.map((c) => csvCell(c.header)).join(',');
  const body = rows.map((row) => columns.map((c) => csvCell(row[c.key])).join(','));
  return [head, ...body].join('\r\n');
}
