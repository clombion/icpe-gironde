/**
 * lib.js — Pure helpers for the /enquete/ findings page.
 * Testable in enquete/test.html. No DOM access, no imports, no side effects.
 */

const THIN_NBSP = ' '; // French thousands separator (narrow no-break space)

/**
 * Format an integer with French thousands separators.
 * @param {number} n
 * @returns {string}
 */
export function formatFr(n) {
  const s = Math.round(Math.abs(n)).toString();
  let out = '';
  for (let i = 0; i < s.length; i++) {
    if (i > 0 && (s.length - i) % 3 === 0) out += THIN_NBSP;
    out += s[i];
  }
  return (n < 0 ? '-' : '') + out;
}

/**
 * Bar width as a percentage string of the max value, clamped to [0, 100].
 * @param {number} n
 * @param {number} max
 * @returns {string} e.g. "42.5%"
 */
export function barWidthPct(n, max) {
  if (!max || max <= 0) return '0%';
  const pct = Math.max(0, Math.min(100, (n / max) * 100));
  return pct.toFixed(1) + '%';
}
