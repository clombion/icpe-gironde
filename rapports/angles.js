/**
 * angles.js — Recipe book: pre-computed SQL angles from static JSON.
 *
 * Loads angles from angles/index.json, fetches each .md for SQL + explanation,
 * and the pre-computed .json for results. No DuckDB WASM needed — results are
 * generated at build time by the pipeline.
 */

const INDEX_URL = 'angles/index.json';

// --- Init ----------------------------------------------------------------

async function init() {
  const loadingEl = document.getElementById('loading');
  const container = document.getElementById('angles-container');

  try {
    const indexResp = await fetch(INDEX_URL);
    if (!indexResp.ok) throw new Error('Failed to fetch angles index');
    const angles = await indexResp.json();

    // Load and render each angle
    for (const angle of angles) {
      const [mdResp, jsonResp] = await Promise.all([
        fetch('angles/' + angle.file),
        fetch('angles/' + angle.file.replace('.md', '.json')),
      ]);
      if (!mdResp.ok) continue;
      const mdText = await mdResp.text();
      const sql = extractSqlFromMarkdown(mdText);
      const explanation = extractExplanation(mdText);
      const rows = jsonResp.ok ? await jsonResp.json() : [];
      if (!sql) continue;
      renderAngle(container, angle, sql, explanation, rows);
    }
    loadingEl.hidden = true;
  } catch (err) {
    loadingEl.innerHTML = '<span style="color:var(--rust)">Erreur de chargement. Rechargez la page.</span>';
    console.error('Angles init error:', err);
  }
}

// --- Markdown parsing ----------------------------------------------------

function extractSqlFromMarkdown(md) {
  const match = md.match(/```sql\n([\s\S]+?)```/);
  return match ? match[1].trim() : null;
}

function extractExplanation(md) {
  const idx = md.indexOf('```sql');
  if (idx < 0) return '';
  const afterSql = md.indexOf('```', idx + 6);
  if (afterSql < 0) return '';
  const rest = md.slice(afterSql + 3).trim();
  return rest.replace(/^##[^\n]+\n+/, '').trim();
}

// --- Rendering -----------------------------------------------------------

function renderAngle(container, angle, sql, explanation, rows) {
  const section = document.createElement('section');
  section.style.cssText = 'margin-bottom:40px;padding-bottom:32px;border-bottom:1px solid var(--rule-soft);';

  const h2 = document.createElement('h2');
  h2.style.cssText = 'font-family:var(--font-display);font-size:20px;font-weight:500;color:var(--ink);margin:0 0 4px;';
  h2.textContent = angle.title;
  section.appendChild(h2);

  const question = document.createElement('p');
  question.style.cssText = 'font-family:var(--font-body);font-size:14px;color:var(--ink-soft);margin:0 0 8px;';
  question.textContent = angle.question;
  section.appendChild(question);

  if (angle.caveat) {
    const caveat = document.createElement('p');
    caveat.style.cssText = 'font-family:var(--font-data);font-size:11px;color:var(--lead);background:var(--paper-2);padding:8px 12px;border-radius:4px;margin:0 0 12px;';
    caveat.textContent = '\u26a0 ' + angle.caveat;
    section.appendChild(caveat);
  }

  // SQL block (collapsible)
  const details = document.createElement('details');
  details.style.cssText = 'margin-bottom:12px;';
  const summary = document.createElement('summary');
  summary.style.cssText = 'font-family:var(--font-data);font-size:12px;color:var(--ink-soft);cursor:pointer;';
  summary.textContent = 'Voir la requête SQL';
  details.appendChild(summary);
  const pre = document.createElement('pre');
  pre.style.cssText = 'font-family:var(--font-data);font-size:12px;background:var(--paper-2);padding:12px;border-radius:4px;overflow-x:auto;margin:8px 0 0;';
  pre.textContent = sql;
  details.appendChild(pre);
  section.appendChild(details);

  if (explanation) {
    const p = document.createElement('p');
    p.style.cssText = 'font-family:var(--font-body);font-size:13px;color:var(--ink);line-height:1.5;margin:0 0 12px;';
    p.textContent = explanation;
    section.appendChild(p);
  }

  // Result count
  const count = document.createElement('p');
  count.style.cssText = 'font-family:var(--font-data);font-size:12px;color:var(--ink-soft);margin:0 0 8px;';
  count.textContent = `${rows.length} résultat${rows.length > 1 ? 's' : ''}`;
  section.appendChild(count);

  // Button bar
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:12px;align-items:center;';

  const btn = document.createElement('button');
  btn.style.cssText = 'font-family:var(--font-body);font-size:13px;padding:8px 16px;border:1px solid var(--moss);background:transparent;color:var(--moss);border-radius:4px;cursor:pointer;';
  btn.textContent = 'Télécharger CSV';
  btn.addEventListener('click', () => {
    downloadCsv(toCsv(rows), angle.file.replace('.md', '.csv'));
    btn.textContent = rows.length + ' lignes exportées';
    setTimeout(() => { btn.textContent = 'Télécharger CSV'; }, 2000);
  });
  bar.appendChild(btn);

  const previewBtn = document.createElement('button');
  previewBtn.style.cssText = 'font-family:var(--font-body);font-size:13px;padding:8px 16px;border:1px solid var(--rule);background:transparent;color:var(--ink-soft);border-radius:4px;cursor:pointer;';
  const previewCount = Math.min(rows.length, 10);
  previewBtn.textContent = `Aperçu (${previewCount} ligne${previewCount > 1 ? 's' : ''})`;
  previewBtn.addEventListener('click', () => {
    previewEl.innerHTML = rows.length === 0
      ? '<p style="font-size:13px;color:var(--ink-soft)">Aucun résultat.</p>'
      : renderTable(rows.slice(0, 10));
  });
  bar.appendChild(previewBtn);

  section.appendChild(bar);

  const previewEl = document.createElement('div');
  previewEl.style.cssText = 'margin-top:12px;overflow-x:auto;';
  section.appendChild(previewEl);

  container.appendChild(section);
}

// --- CSV generation ------------------------------------------------------

function toCsv(rows) {
  if (rows.length === 0) return '';
  const keys = Object.keys(rows[0]);
  const lines = [keys.join(',')];
  for (const row of rows) {
    const values = keys.map((k) => {
      const v = row[k];
      if (v == null) return '';
      const s = String(v);
      if (s.includes(',') || s.includes('"') || s.includes('\n')) {
        return '"' + s.replace(/"/g, '""') + '"';
      }
      return s;
    });
    lines.push(values.join(','));
  }
  return lines.join('\n');
}

function downloadCsv(content, filename) {
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// --- Table rendering -----------------------------------------------------

function renderTable(rows) {
  if (rows.length === 0) return '';
  const keys = Object.keys(rows[0]);
  let html = '<table style="border-collapse:collapse;font-family:var(--font-data);font-size:12px;width:100%;">';
  html += '<thead><tr>';
  for (const k of keys) {
    html += '<th style="text-align:left;padding:6px 10px;border-bottom:2px solid var(--rule);color:var(--ink-soft);white-space:nowrap;">' + escapeHtml(k) + '</th>';
  }
  html += '</tr></thead><tbody>';
  for (const row of rows) {
    html += '<tr>';
    for (const k of keys) {
      const v = row[k];
      const display = v == null ? '' : String(v);
      const truncated = display.length > 80 ? display.slice(0, 77) + '\u2026' : display;
      html += '<td style="padding:4px 10px;border-bottom:1px solid var(--rule-soft);white-space:nowrap;">' + escapeHtml(truncated) + '</td>';
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  return html;
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// --- Boot ----------------------------------------------------------------

init();
