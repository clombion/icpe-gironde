/**
 * methodo.js — Tab switching + methodology rendering via marked.js CDN.
 *
 * Loads two markdown docs:
 * 1. docs/methodo-carte.md       — pipeline carte + audit des coordonnées
 * 2. rapports/methodologie.md    — pipeline d'extraction des rapports
 */

const MARKED_CDN = 'https://cdn.jsdelivr.net/npm/marked@14.1.3/+esm';

const DOCS = [
  { url: '../docs/methodo-carte.md', label: 'Carte interactive et audit des coordonnées' },
  { url: '../rapports/methodologie.md', label: 'Rapports d\'inspection' },
];

// --- Tab switching -----------------------------------------------------------

const tabNav = document.getElementById('tab-nav');
const panelMethodo = document.getElementById('panel-methodo');
const panelCatalogue = document.getElementById('panel-catalogue');

tabNav.addEventListener('click', (e) => {
  const btn = e.target.closest('.tab-nav__btn');
  if (!btn) return;
  const tab = btn.dataset.tab;
  tabNav.querySelectorAll('.tab-nav__btn').forEach((b) => b.classList.remove('active'));
  btn.classList.add('active');
  panelMethodo.hidden = tab !== 'methodo';
  panelCatalogue.hidden = tab !== 'catalogue';
  // Update URL hash for deep linking
  history.replaceState(null, '', tab === 'methodo' ? '#methodo' : '#catalogue');
});

// Check hash on load
if (location.hash === '#catalogue') {
  tabNav.querySelector('[data-tab="catalogue"]').click();
}

// --- Methodology rendering ---------------------------------------------------

async function loadMethodo() {
  const container = document.getElementById('methodo-content');
  try {
    const markedModule = await import(MARKED_CDN);
    const responses = await Promise.all(DOCS.map((d) => fetch(d.url, { cache: 'no-store' })));
    let html = '';
    for (let i = 0; i < DOCS.length; i++) {
      if (!responses[i].ok) {
        html += `<p style="color:var(--rust)">Impossible de charger ${DOCS[i].url}.</p>`;
        continue;
      }
      const text = await responses[i].text();
      if (i > 0) {
        html += '<hr style="margin:48px 0;border:none;border-top:2px solid var(--rule);">';
      }
      html += markedModule.marked.parse(text);
    }
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = '<p style="color:var(--rust)">Erreur de chargement. Rechargez la page.</p>';
    console.error('Methodology load error:', err);
  }
}

// --- Back to top button ---

function setupBackToTop() {
  const btn = document.getElementById('back-to-top');
  if (!btn) return;
  window.addEventListener('scroll', () => {
    btn.hidden = window.scrollY < 400;
  }, { passive: true });
  btn.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
}

loadMethodo();
setupBackToTop();
