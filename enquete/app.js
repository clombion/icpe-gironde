/**
 * app.js — Renders the /enquete/ findings from the pre-baked angles.json.
 * No sql.js, no DB — a tiny static JSON computed at build time.
 */
import { formatFr, barWidthPct } from './lib.js?v=1';

const pct = (x) => x.toFixed(1).replace('.', ',') + ' %';

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function creuserLink(href) {
  const a = el('a', 'creuser', 'Creuser dans l’explorateur →');
  a.href = href;
  return a;
}

function renderRoutine(a) {
  const s = el('section', 'angle');
  s.appendChild(el('h2', 'angle__title', a.title));
  const big = el('div', 'angle__big');
  big.appendChild(el('span', 'angle__big-num', formatFr(a.big.value)));
  big.appendChild(el('span', 'angle__big-unit',
    `des ${formatFr(a.big.of)} constats classés · ${pct(a.big.pct)}`));
  s.appendChild(big);
  s.appendChild(el('p', 'angle__note',
    `Le motif le plus fréquent est « ${a.detail.m08_label} » : ${formatFr(a.detail.m08)} fiches. ` +
    'La plupart des inspections confirment la conformité ou relèvent un écart mineur.'));
  s.appendChild(creuserLink(a.creuser));
  return s;
}

function renderBars(rows) {
  const max = rows.reduce((m, r) => Math.max(m, r.n), 0);
  const wrap = el('div', 'bars');
  for (const r of rows) {
    const row = el('div', 'bar');
    row.appendChild(el('span', 'bar__lbl', `${r.code} · ${r.label}`));
    const track = el('span', 'bar__track');
    const fill = el('span', 'bar__fill');
    fill.style.width = barWidthPct(r.n, max);
    track.appendChild(fill);
    row.appendChild(track);
    row.appendChild(el('span', 'bar__n', formatFr(r.n)));
    wrap.appendChild(row);
  }
  return wrap;
}

function renderRisques(a) {
  const s = el('section', 'angle');
  s.appendChild(el('h2', 'angle__title', a.title));
  const big = el('div', 'angle__big');
  big.appendChild(el('span', 'angle__big-num', formatFr(a.big.value)));
  big.appendChild(el('span', 'angle__big-unit',
    `constats à risque avéré ou incident · ${pct(a.big.pct)}`));
  s.appendChild(big);
  s.appendChild(el('p', 'angle__note',
    'Ils se concentrent sur quelques domaines — incendie, eaux, déchets en tête :'));
  s.appendChild(renderBars(a.bars));
  s.appendChild(creuserLink(a.creuser));
  return s;
}

function renderRecidivistes(a) {
  const s = el('section', 'angle');
  s.appendChild(el('h2', 'angle__title', a.title));
  const sig = el('div', 'signals');
  for (const x of a.signals) {
    const b = el('div', 'signal');
    b.appendChild(el('div', 'signal__n', formatFr(x.n)));
    b.appendChild(el('div', 'signal__lbl', x.label));
    sig.appendChild(b);
  }
  s.appendChild(sig);
  s.appendChild(el('p', 'angle__note',
    'Deux signaux distincts : une trajectoire qui se dégrade (aggravation, écart chronique) ' +
    'et l’escalade formelle (mise en demeure). Installations les plus concernées par une ' +
    'trajectoire dégradée :'));
  const named = el('div', 'named');
  for (const x of a.named) {
    const row = el('div', 'named__row');
    const left = el('div', 'named__label');
    left.appendChild(el('span', 'named__name', x.nom));
    if (x.commune) left.appendChild(el('span', 'named__commune', ' · ' + x.commune));
    row.appendChild(left);
    row.appendChild(el('span', 'named__n', `${x.n} fiche${x.n > 1 ? 's' : ''}`));
    named.appendChild(row);
  }
  s.appendChild(named);
  s.appendChild(creuserLink(a.creuser));
  return s;
}

const RENDERERS = {
  routine: renderRoutine,
  'risques-averes': renderRisques,
  recidivistes: renderRecidivistes,
};

async function init() {
  const host = document.getElementById('angles');
  try {
    const doc = await fetch(new URL('angles.json', import.meta.url).href).then((r) => r.json());
    host.innerHTML = '';
    host.removeAttribute('aria-busy');
    for (const angle of doc.angles) {
      const render = RENDERERS[angle.id];
      if (render) host.appendChild(render(angle));
    }
  } catch (err) {
    host.innerHTML = '<p class="enquete__loading">Chargement impossible. Rechargez la page.</p>';
    console.error('enquete init:', err);
  }
}

init();
