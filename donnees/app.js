/* ============================================================================
   donnees/app.js — Catalogue des données ICPE en Gironde

   Charge le dictionnaire (metadonnees_colonnes.csv) et le sidecar
   d'échantillons (metadonnees_samples.json), groupe par fichier, et
   rend une section par fichier avec un tableau colonnes + chips
   d'échantillons cliquables-pour-copier.
============================================================================ */

(function () {
  'use strict';

  const METADATA_CSV_URL = '../carte/data/metadonnees_colonnes.csv';
  const SAMPLES_JSON_URL = '../carte/data/metadonnees_samples.json';

  // ---- Helpers ----------------------------------------------------------

  // Shared HTML-escape from window.IcpeUtil (shared/util.js).
  const escapeHTML = window.IcpeUtil.escapeHTML;

  // Minimal CSV parser that handles quoted fields, escaped quotes, and
  // newlines inside quotes. The metadata CSV uses standard `,` delimiter
  // and `"` quoting per RFC 4180.
  function parseCSV(text) {
    const rows = [];
    let row = [];
    let cell = '';
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') {
            cell += '"';
            i++;
          } else {
            inQuotes = false;
          }
        } else {
          cell += c;
        }
      } else {
        if (c === '"') {
          inQuotes = true;
        } else if (c === ',') {
          row.push(cell);
          cell = '';
        } else if (c === '\n') {
          row.push(cell);
          rows.push(row);
          row = [];
          cell = '';
        } else if (c === '\r') {
          // ignore
        } else {
          cell += c;
        }
      }
    }
    if (cell || row.length) {
      row.push(cell);
      rows.push(row);
    }
    return rows;
  }

  function csvToObjects(text) {
    const rows = parseCSV(text);
    if (rows.length === 0) return [];
    const header = rows[0];
    return rows
      .slice(1)
      .filter((r) => r.length > 1 || (r.length === 1 && r[0]))
      .map((r) => {
        const obj = {};
        header.forEach((h, i) => {
          obj[h] = r[i] || '';
        });
        return obj;
      });
  }

  // ---- Data loading -----------------------------------------------------

  async function loadAll() {
    const [csvText, samplesJson] = await Promise.all([
      fetch(METADATA_CSV_URL, { cache: 'no-store' }).then((r) => {
        if (!r.ok) throw new Error('metadonnees_colonnes.csv: HTTP ' + r.status);
        return r.text();
      }),
      fetch(SAMPLES_JSON_URL, { cache: 'no-store' })
        .then((r) => {
          if (!r.ok) {
            console.warn('donnees: metadonnees_samples.json — HTTP', r.status);
            return null;
          }
          return r.json();
        })
        .catch((err) => {
          console.warn('donnees: metadonnees_samples.json unavailable', err);
          return null;
        }),
    ]);

    const rows = csvToObjects(csvText);
    return { rows, samples: samplesJson || {} };
  }

  // ---- Grouping ---------------------------------------------------------

  function groupByFichier(rows) {
    const byFile = new Map();
    for (const row of rows) {
      const f = row.fichier;
      if (!f) continue;
      if (!byFile.has(f)) byFile.set(f, []);
      byFile.get(f).push(row);
    }
    return byFile;
  }

  // ---- Rendering --------------------------------------------------------

  function renderSidebar(byFile, samples) {
    const nav = document.getElementById('sidebar-nav');
    if (!nav) return;
    const links = [];
    for (const [filename, cols] of byFile) {
      const sampleInfo = samples[filename];
      const rowCount = sampleInfo ? sampleInfo.row_count : null;
      const slug = filename.replace(/[^a-z0-9]/gi, '-');
      links.push(
        `<a href="#${escapeHTML(slug)}">` +
          escapeHTML(filename) +
          (rowCount != null
            ? `<span class="file-rowcount">${rowCount.toLocaleString('fr-FR')} lignes · ${cols.length} colonnes</span>`
            : `<span class="file-rowcount">${cols.length} colonnes</span>`) +
          `</a>`
      );
    }
    nav.innerHTML = links.join('');
  }

  function renderFile(filename, cols, samples) {
    const sampleInfo = samples[filename] || { row_count: null, columns: {} };
    const slug = filename.replace(/[^a-z0-9]/gi, '-');
    const meta = [];
    if (sampleInfo.row_count != null) {
      meta.push(`<span>${sampleInfo.row_count.toLocaleString('fr-FR')} lignes</span>`);
    }
    meta.push(`<span class="meta-sep">·</span>`);
    meta.push(`<span>${cols.length} colonnes documentées</span>`);

    let html = `<section class="file-section" id="${escapeHTML(slug)}">`;
    html += `<header class="file-section__header">`;
    html += `<h2 class="file-section__title"><code>${escapeHTML(filename)}</code></h2>`;
    html += `<p class="file-section__meta">${meta.join(' ')}</p>`;
    html += `</header>`;

    html += `<table class="col-table">`;
    html += `<thead><tr>`;
    html += `<th>Alias</th><th>Source</th><th>Type</th><th>Définition</th>`;
    html += `<th>Distinct</th><th>Échantillons</th>`;
    html += `</tr></thead><tbody>`;

    for (const col of cols) {
      const colSamples = sampleInfo.columns[col.alias] || {};
      const type = colSamples.type || '';
      const distinctCount = colSamples.distinct_count;
      const sampleValues = colSamples.samples || [];

      html += `<tr>`;
      html += `<td class="col-alias">${escapeHTML(col.alias)}</td>`;
      html += `<td class="col-original">${escapeHTML(col.nom_original)}</td>`;
      html += `<td>`;
      if (type) {
        html += `<span class="col-type col-type--${escapeHTML(type)}">${escapeHTML(type)}</span>`;
      }
      html += `</td>`;
      html += `<td class="col-definition">${escapeHTML(col.definition)}</td>`;
      html += `<td class="col-distinct">`;
      if (distinctCount != null) {
        html += `${distinctCount.toLocaleString('fr-FR')}`;
      }
      html += `</td>`;
      html += `<td><div class="col-samples">`;
      for (const v of sampleValues) {
        // role=button + tabindex=0 + keydown handler in attachChipHandlers
        // makes the chip operable from a keyboard. WCAG 2.1.1.
        html += `<span class="chip" data-value="${escapeHTML(String(v))}" role="button" tabindex="0" aria-label="Copier ${escapeHTML(String(v))}" title="Cliquer pour copier (Entrée ou Espace au clavier)">${escapeHTML(String(v))}</span>`;
      }
      html += `</div></td>`;
      html += `</tr>`;
    }

    html += `</tbody></table></section>`;
    return html;
  }

  function _copyChip(chip) {
    const value = chip.getAttribute('data-value') || '';
    navigator.clipboard.writeText(value).then(
      () => {
        chip.classList.add('chip--copied');
        setTimeout(() => chip.classList.remove('chip--copied'), 800);
      },
      () => {
        // Clipboard API failed (insecure context, etc.) — soft fail
        chip.classList.add('chip--copied');
        setTimeout(() => chip.classList.remove('chip--copied'), 800);
      }
    );
  }

  function attachChipHandlers() {
    document.querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', () => _copyChip(chip));
      chip.addEventListener('keydown', (ev) => {
        // Enter or Space activate the chip — same contract as <button>.
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          _copyChip(chip);
        }
      });
    });
  }

  // ---- Init -------------------------------------------------------------

  async function init() {
    const content = document.getElementById('content');
    const headerStats = document.getElementById('header-stats');

    try {
      const { rows, samples } = await loadAll();
      const byFile = groupByFichier(rows);

      const totalCols = rows.length;
      const totalFiles = byFile.size;
      headerStats.textContent = `${totalFiles} fichier${totalFiles > 1 ? 's' : ''} · ${totalCols} colonnes documentées`;

      renderSidebar(byFile, samples);

      const sectionsHtml = [];
      for (const [filename, cols] of byFile) {
        sectionsHtml.push(renderFile(filename, cols, samples));
      }
      content.innerHTML = sectionsHtml.join('\n');
      attachChipHandlers();
    } catch (err) {
      console.error('donnees/app.js: load failed', err);
      content.innerHTML =
        '<div class="error-banner">Impossible de charger le catalogue : ' +
        escapeHTML(err.message || 'erreur inconnue') +
        '. Vérifie que <code>carte/data/metadonnees_colonnes.csv</code> existe.</div>';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
