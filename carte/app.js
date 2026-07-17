/* ============================================================================
   Cahier d'enquête — ICPE en Gironde
   Map logic: CSV loading, filter compilation, color switching, layer control.
   No framework. Vanilla JS. Designed for speed at 2,888 markers.
============================================================================ */

(function () {
  'use strict';

  // ---------- constants ----------
  const CSV_URL = 'data/liste-icpe-gironde_enrichi.csv';
  const RNN_URL = 'data/reserves-naturelles-nationales.geojson';
  const RNR_URL = 'data/reserves-naturelles-regionales.geojson';
  const GIRONDE_CONTOUR_URL = 'data/gironde-contour.geojson';
  const GIRONDE_COMMUNES_URL = 'data/gironde-communes.geojson';
  const EPCI_OUTLINES_URL = 'data/gironde-epci-outlines.geojson';
  const SUGGESTION_LIMIT_PER_GROUP = 5;

  const CSS = (() => {
    const s = getComputedStyle(document.documentElement);
    const get = (k) => s.getPropertyValue(k).trim();
    return {
      ink: get('--ink'),
      paper: get('--paper'),
      rust: get('--rust'),
      ochre: get('--ochre'),
      lead: get('--lead'),
      fog: get('--fog'),
      rustDeep: get('--rust-deep'),
      rustMid: get('--rust-mid'),
      moss: get('--moss'),
      mossDeep: get('--moss-deep'),
      olive: get('--olive'),
      oliveDeep: get('--olive-deep'),
      copper: get('--copper'),
      azur: get('--azur'),
      rule: get('--rule'),
    };
  })();

  // ---------- color palette per dimension ----------
  const PALETTE = {
    regime: {
      AUTORISATION: CSS.rust,
      ENREGISTREMENT: CSS.ochre,
      NON_ICPE: CSS.lead,
      AUTRE: CSS.fog,
    },
    seveso: {
      SEUIL_HAUT: CSS.rustDeep,
      SEUIL_BAS: CSS.rustMid,
      NON_SEVESO: CSS.lead,
      '': CSS.fog, // non classé
    },
    priority: {
      true: CSS.copper,
      false: CSS.fog,
    },
    ied: {
      true: CSS.azur,
      false: CSS.fog,
    },
    secteur: {
      industrie: CSS.ink,
      carriere: CSS.ochre,
      autre: CSS.fog,
    },
  };

  const LEGEND_LABELS = {
    regime: [
      ['Autorisation', CSS.rust],
      ['Enregistrement', CSS.ochre],
      ['Non-ICPE', CSS.lead],
      ['Autre', CSS.fog],
    ],
    seveso: [
      ['Seuil haut', CSS.rustDeep],
      ['Seuil bas', CSS.rustMid],
      ['Non Seveso', CSS.lead],
      ['Non classé', CSS.fog],
    ],
    priority: [
      ['Priorité nationale', CSS.copper],
      ['Autre', CSS.fog],
    ],
    ied: [
      ['IED', CSS.azur],
      ['Autre', CSS.fog],
    ],
    secteur: [
      ['Industrie', CSS.ink],
      ['Carrière', CSS.ochre],
      ['Autre', CSS.fog],
    ],
  };

  const DIM_HUMAN = {
    regime: 'Régime',
    seveso: 'Seveso',
    priority: 'Priorité nationale',
    ied: 'IED',
    secteur: 'Secteur',
  };

  // NAF Rev 2 division labels for popup activité affichage
  // (subset — just the divisions that actually appear in the dataset)
  const NAF_DIVISIONS = {
    '1': 'Agriculture, chasse et services annexes',
    '2': 'Sylviculture et exploitation forestière',
    '3': 'Pêche et aquaculture',
    '5': 'Extraction de houille et de lignite',
    '6': 'Extraction d\'hydrocarbures',
    '7': 'Extraction de minerais métalliques',
    '8': 'Autres industries extractives',
    '9': 'Services de soutien aux industries extractives',
    '10': 'Industries alimentaires',
    '11': 'Fabrication de boissons',
    '13': 'Fabrication de textiles',
    '14': 'Industrie de l\'habillement',
    '15': 'Industrie du cuir',
    '16': 'Travail du bois',
    '17': 'Industrie du papier et du carton',
    '18': 'Imprimerie et reproduction',
    '19': 'Cokéfaction et raffinage',
    '20': 'Industrie chimique',
    '21': 'Industrie pharmaceutique',
    '22': 'Fabrication de produits en caoutchouc et en plastique',
    '23': 'Fabrication d\'autres produits minéraux non métalliques',
    '24': 'Métallurgie',
    '25': 'Fabrication de produits métalliques',
    '26': 'Fabrication de produits informatiques, électroniques',
    '27': 'Fabrication d\'équipements électriques',
    '28': 'Fabrication de machines et équipements',
    '29': 'Industrie automobile',
    '30': 'Fabrication d\'autres matériels de transport',
    '31': 'Fabrication de meubles',
    '32': 'Autres industries manufacturières',
    '33': 'Réparation et installation de machines',
    '35': 'Production et distribution d\'électricité, gaz',
    '36': 'Captage, traitement et distribution d\'eau',
    '37': 'Collecte et traitement des eaux usées',
    '38': 'Collecte, traitement et élimination des déchets',
    '39': 'Dépollution et autres services',
    '41': 'Construction de bâtiments',
    '42': 'Génie civil',
    '43': 'Travaux de construction spécialisés',
    '45': 'Commerce et réparation d\'automobiles',
    '46': 'Commerce de gros',
    '47': 'Commerce de détail',
    '49': 'Transports terrestres',
    '52': 'Entreposage et services auxiliaires des transports',
    '56': 'Restauration',
    '68': 'Activités immobilières',
    '77': 'Activités de location et location-bail',
    '81': 'Services relatifs aux bâtiments et aménagement paysager',
    '84': 'Administration publique et défense',
    '85': 'Enseignement',
    '86': 'Activités pour la santé humaine',
    '91': 'Bibliothèques, archives, musées',
    '93': 'Activités sportives, récréatives',
    '96': 'Autres services personnels',
  };

  const REGIME_LABEL = {
    AUTORISATION: 'Autorisation',
    ENREGISTREMENT: 'Enregistrement',
    NON_ICPE: 'Non-ICPE',
    AUTRE: 'Autre',
  };
  const REGIME_BADGE = {
    AUTORISATION: 'badge--rust',
    ENREGISTREMENT: 'badge--ochre',
    NON_ICPE: 'badge--lead',
    AUTRE: 'badge--fog',
  };
  const SEVESO_LABEL = {
    SEUIL_HAUT: 'Seveso seuil haut',
    SEUIL_BAS: 'Seveso seuil bas',
    NON_SEVESO: 'Non Seveso',
  };
  const SEVESO_BADGE = {
    SEUIL_HAUT: 'badge--rust-deep',
    SEUIL_BAS: 'badge--rust-mid',
    NON_SEVESO: 'badge--lead',
  };

  // Default values used by BOTH the URL serializer (to decide whether to
  // emit a param) AND the state initializer / reset handler (so all three
  // agree on "the default state"). Declared early so the state object can
  // reference it.
  const URL_DEFAULTS = {
    regime: ['AUTORISATION', 'ENREGISTREMENT', 'NON_ICPE', 'AUTRE'],
    seveso: ['SEUIL_HAUT', 'SEUIL_BAS', 'NON_SEVESO', ''],
  };

  // ---------- state ----------
  const state = {
    rows: [],
    visibleRows: [],
    colorDim: 'regime',
    filters: {
      freeSearch: '',
      regime: new Set(URL_DEFAULTS.regime),
      seveso: new Set(URL_DEFAULTS.seveso),
      priority: 'all',
      ied: 'all',
      secteur: new Set(), // empty = no secteur filter; populated = OR of active secteurs
      // Pill-based filters (OR within each Set, AND across sets)
      commune: new Set(),   // Set<INSEE code>  e.g. '33063'
      epci: new Set(),      // Set<EPCI siren>  e.g. '243300316'
      structure: new Set(), // Set<normalised structure name>
      // Month window filter (disabled by default)
      monthEnabled: false,
      month: null,
    },
    mdateMax: null,
    // month keys derived from the dataset — set after CSV load
    monthSteps: [],
  };

  // Reference data for suggestions & EPCI checkbox list. Derived from the
  // enriched CSV at load time (the CSV already carries commune/EPCI via
  // scripts/enrichir_libelles.py).
  const reference = {
    communes: [],   // [{code, nom, norm, epci_siren, epci_nom, count}]
    epcis: [],      // [{code, nom, norm, site_count}]
    structures: [], // [{name, norm, count}]
    communeByInsee: new Map(),  // code → commune object
    epciByCode: new Map(),       // siren → epci object
    structureByNorm: new Map(),  // normalised name → structure object
  };

  function monthKey(isoDate) {
    // isoDate like "2025-02-10T..." → "2025-02"
    if (!isoDate || isoDate.length < 7) return '';
    return isoDate.substring(0, 7);
  }
  const MONTHS_FR = [
    'janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin',
    'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.',
  ];
  function formatMonthFR(ymkey) {
    // ymkey like "2025-02" → "févr. 2025"
    if (!ymkey) return '—';
    const [y, m] = ymkey.split('-');
    const idx = parseInt(m, 10) - 1;
    if (idx < 0 || idx > 11) return ymkey;
    return `${MONTHS_FR[idx]} ${y}`;
  }

  // ---------- utilities ----------
  const nfFR = new Intl.NumberFormat('fr-FR');
  function formatCount(n) { return nfFR.format(n); }

  function formatDateFR(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return '—';
    return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric' });
  }

  // Shared HTML-escape from window.IcpeUtil (shared/util.js).
  const escapeHTML = window.IcpeUtil.escapeHTML;

  // Normalise a string for search: lowercase + strip diacritics + collapse
  // whitespace. Lets the user type "reserve" and match "RÉSERVE".
  function normaliseForSearch(s) {
    if (!s) return '';
    return String(s)
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '') // remove combining diacritical marks
      .toLowerCase()
      .replace(/\s+/g, ' ')
      .trim();
  }

  // Only allow http(s) URLs in href attributes — blocks javascript:, data:, etc.
  function safeHref(url) {
    if (!url) return '';
    try {
      const u = new URL(url);
      if (u.protocol !== 'https:' && u.protocol !== 'http:') return '';
      return escapeHTML(url);
    } catch (_) {
      return '';
    }
  }

  async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`fetch ${url}: ${res.status}`);
    return res.json();
  }

  // ---------- URL query-param hydration & serialization ----------
  //
  // Every filter that is "non-default" can be reflected in the URL, so
  // a given view can be shared or embedded with a single link. Parsing
  // happens once at init. Codes are currently NOT validated against the
  // reference data — an invalid INSEE or SIREN will simply match zero
  // rows, which fails safely but silently.

  function parseUrlToFilters() {
    const p = new URLSearchParams(window.location.search);
    const out = {};
    // Multi-value filters (comma-separated); a missing param means "keep default"
    const readSet = (name) => {
      const raw = p.get(name);
      if (raw == null) return null;
      return raw.split(',').map((s) => s.trim()).filter(Boolean);
    };
    const regime = readSet('regime');
    if (regime) out.regime = new Set(regime);
    const seveso = readSet('seveso');
    if (seveso) {
      // Special-case: 'nonclasse' stands in for the empty-string Seveso bucket
      out.seveso = new Set(seveso.map((v) => (v === 'nonclasse' ? '' : v)));
    }
    const secteur = readSet('secteur');
    if (secteur) out.secteur = new Set(secteur);
    const commune = readSet('commune');
    if (commune) out.commune = new Set(commune);
    const epci = readSet('epci');
    if (epci) out.epci = new Set(epci);
    const structure = readSet('structure');
    if (structure) {
      // URL carries raw strings; normalise to match row.structure_norm
      out.structure = new Set(structure.map((s) => normaliseForSearch(s)));
    }
    // Scalar filters
    const priority = p.get('priority');
    if (priority === 'yes' || priority === 'no' || priority === 'all') out.priority = priority;
    const ied = p.get('ied');
    if (ied === 'yes' || ied === 'no' || ied === 'all') out.ied = ied;
    const q = p.get('q');
    if (q != null) out.freeSearch = q;
    const month = p.get('month');
    if (month && /^\d{4}-\d{2}$/.test(month)) {
      out.month = month;
      out.monthEnabled = true;
    }
    // Color dimension
    const color = p.get('color');
    if (color && ['regime', 'seveso', 'priority', 'ied', 'secteur'].includes(color)) {
      out.colorDim = color;
    }
    // Boundary lock — "bounds=south,west,north,east" restricts panning.
    // Used by embeds that want to lock the view to a specific area.
    const bounds = p.get('bounds');
    if (bounds) {
      const parts = bounds.split(',').map(Number);
      if (parts.length === 4 && parts.every(Number.isFinite)) {
        out.bounds = [[parts[0], parts[1]], [parts[2], parts[3]]]; // [[south,west],[north,east]]
      }
    }
    // Embed flag is handled separately (it's not a filter)
    return out;
  }

  function isEmbedMode() {
    return new URLSearchParams(window.location.search).get('embed') === '1';
  }

  // Features that can be toggled off from an embed URL via ?hide=
  // Names map 1:1 to body classes (.hide-sidebar, .hide-timebar, …) so CSS
  // is all that's needed on top. Listed here for validation + the dialog UI.
  const HIDEABLE_FEATURES = ['sidebar', 'timebar', 'legend', 'layers', 'zoom', 'reserves'];

  function parseHiddenFeatures() {
    const raw = new URLSearchParams(window.location.search).get('hide');
    if (!raw) return new Set();
    return new Set(
      raw.split(',')
        .map((s) => s.trim())
        .filter((s) => HIDEABLE_FEATURES.includes(s))
    );
  }

  function applyHiddenFeatures(hidden) {
    for (const feat of HIDEABLE_FEATURES) {
      document.body.classList.toggle(`hide-${feat}`, hidden.has(feat));
    }
    // Leaflet-controlled elements that CSS alone can't tidy:
    if (typeof map !== 'undefined' && map.zoomControl) {
      if (hidden.has('zoom')) map.zoomControl.remove();
      else if (!map.hasLayer(map.zoomControl)) map.zoomControl.addTo(map);
    }
    // Reserves layers: remove from map entirely when hidden, so they
    // don't appear in the layer control either.
    if (typeof rnnLayer !== 'undefined' && typeof rnrLayer !== 'undefined') {
      if (hidden.has('reserves')) {
        if (map.hasLayer(rnnLayer)) map.removeLayer(rnnLayer);
        if (map.hasLayer(rnrLayer)) map.removeLayer(rnrLayer);
      }
    }
  }

  function applyParsedUrlState(parsed) {
    if (!parsed) return;
    // Only assign fields that were present in the URL — unset fields keep
    // their current default so partial URLs do partial things.
    if (parsed.regime)   state.filters.regime   = parsed.regime;
    if (parsed.seveso)   state.filters.seveso   = parsed.seveso;
    if (parsed.secteur)  state.filters.secteur  = parsed.secteur;
    if (parsed.commune)  state.filters.commune  = parsed.commune;
    if (parsed.epci)     state.filters.epci     = parsed.epci;
    if (parsed.structure) state.filters.structure = parsed.structure;
    if (parsed.priority !== undefined) state.filters.priority = parsed.priority;
    if (parsed.ied !== undefined)       state.filters.ied       = parsed.ied;
    if (parsed.freeSearch !== undefined) state.filters.freeSearch = parsed.freeSearch;
    if (parsed.month) {
      state.filters.month = parsed.month;
      state.filters.monthEnabled = true;
    }
    if (parsed.colorDim) state.colorDim = parsed.colorDim;
    if (parsed.bounds) {
      // Lock the map to the specified bounding box — user can't pan outside.
      // Also fit the view to these bounds on load.
      map.setMaxBounds(parsed.bounds);
      map.fitBounds(parsed.bounds);
      state.boundaryLocked = true;
    }
  }

  // Build a shareable URL reflecting the current filter state. Only
  // non-default fields are serialised; the URL stays short for the
  // common "no filters" case.
  function buildShareableUrl({ embed = false, includeFilters = true, hide = null, lockBounds = false } = {}) {
    const params = new URLSearchParams();
    if (embed) params.set('embed', '1');
    if (lockBounds) {
      const b = map.getBounds();
      params.set('bounds', [
        b.getSouth().toFixed(5),
        b.getWest().toFixed(5),
        b.getNorth().toFixed(5),
        b.getEast().toFixed(5),
      ].join(','));
    }
    if (hide && hide.size > 0) {
      params.set('hide', [...hide].sort().join(','));
    }
    if (includeFilters) {
      const f = state.filters;
      // Multi-value sets — only emit if the set is different from the default
      const setsEqual = (a, b) => a.size === b.length && b.every((v) => a.has(v));
      if (!setsEqual(f.regime, URL_DEFAULTS.regime)) {
        params.set('regime', [...f.regime].join(','));
      }
      if (!setsEqual(f.seveso, URL_DEFAULTS.seveso)) {
        params.set('seveso', [...f.seveso].map((v) => v === '' ? 'nonclasse' : v).join(','));
      }
      if (f.secteur.size > 0) params.set('secteur', [...f.secteur].join(','));
      if (f.commune.size > 0) params.set('commune', [...f.commune].join(','));
      if (f.epci.size > 0)    params.set('epci',    [...f.epci].join(','));
      if (f.structure.size > 0) {
        // Serialise using the original display name so the URL is human-readable
        const names = [...f.structure].map((norm) => {
          const s = reference.structureByNorm.get(norm);
          return s ? s.name : norm;
        });
        params.set('structure', names.join(','));
      }
      if (f.priority !== 'all') params.set('priority', f.priority);
      if (f.ied !== 'all')      params.set('ied', f.ied);
      if (f.freeSearch.trim())  params.set('q', f.freeSearch.trim());
      if (f.monthEnabled && f.month) params.set('month', f.month);
      if (state.colorDim !== 'regime') params.set('color', state.colorDim);
    }
    const qs = params.toString();
    const origin = window.location.origin + window.location.pathname;
    return qs ? `${origin}?${qs}` : origin;
  }

  // postMessage bridge for parent-page auto-resize when embedded.
  // Parent listens for { type: 'icpe-map-height', height } and resizes
  // the iframe accordingly. Throttled by rAF and only active in embed mode.
  function installEmbedResizeBridge() {
    if (!isEmbedMode() || window.parent === window) return;
    let rafPending = false;
    const report = () => {
      rafPending = false;
      const h = document.documentElement.scrollHeight;
      try {
        window.parent.postMessage({ type: 'icpe-map-height', height: h }, '*');
      } catch (_) { /* cross-origin restrictions — fine */ }
    };
    const queue = () => {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(report);
    };
    window.addEventListener('resize', queue);
    // Report once on load, then on any significant DOM change
    queue();
    const ro = new ResizeObserver(queue);
    ro.observe(document.documentElement);
  }

  // ---------- CSV loading (main thread; worker mode has silent-failure issues) ----------
  async function parseCSV() {
    const res = await fetch(CSV_URL);
    if (!res.ok) throw new Error(`CSV fetch ${res.status}`);
    const text = await res.text();
    const result = Papa.parse(text, {
      header: true,
      skipEmptyLines: true,
    });
    if (result.errors && result.errors.length > 0) {
      console.warn('PapaParse errors:', result.errors.slice(0, 5));
    }
    return result.data;
  }

  function transformRows(rawRows) {
    // Transform each CSV row into a compact object with pre-computed colors.
    // The CSV is the aliased enrichment output produced by
    // scripts/enrichir_libelles.py — see data/metadonnees_colonnes.csv for
    // the column dictionary.
    const rows = [];
    let mdateMax = null;
    let mdateMaxDate = null;
    for (const r of rawRows) {
      const geoPoint = r.coordonnees_lat_lon;
      if (!geoPoint) continue;
      const parts = geoPoint.split(',');
      if (parts.length !== 2) continue;
      const lat = parseFloat(parts[0]);
      const lon = parseFloat(parts[1]);
      if (!isFinite(lat) || !isFinite(lon)) continue;

      const regime = r.regime_icpe || 'AUTRE';
      const seveso = (r.categorie_seveso || '').trim();
      const priority = r.priorite_nationale === 'TRUE';
      const ied = r.directive_ied === 'TRUE';
      const industrie = r.activite_industrielle === 'TRUE';
      const carriere = r.activite_carriere === 'TRUE';
      const libelle = (r.nom_complet || r.nom_original || '(sans nom)').trim();
      const structure = (r.structure || '').trim();
      const structure_norm = normaliseForSearch(structure);
      const etablissement = (r.etablissement || '').trim();
      const insee = (r.code_insee_commune || '').trim();
      // Commune / EPCI are now carried directly in the enriched CSV
      // (scripts/enrichir_libelles.py joins on code INSEE before export).
      const commune_nom = (r.nom_commune || '').trim();
      const epci_siren = (r.epci_siren || '').trim();
      const epci_nom = (r.epci_nom || '').trim();

      // pre-compute per-dimension color
      const color = {
        regime: PALETTE.regime[regime] || CSS.fog,
        seveso: PALETTE.seveso[seveso] || CSS.fog,
        priority: priority ? PALETTE.priority.true : PALETTE.priority.false,
        ied: ied ? PALETTE.ied.true : PALETTE.ied.false,
        secteur: industrie ? PALETTE.secteur.industrie
                 : carriere ? PALETTE.secteur.carriere
                 : PALETTE.secteur.autre,
      };

      // date_enregistrement is the only date column in the aliased bulk export.
      // (mdate/cdate were strict duplicates and the pipeline dropped the extra.)
      const dateEnreg = r.date_enregistrement || '';
      // Track latest date via Date() comparison — robust against non-padded
      // ISO. Keep a parallel Date object so we don't re-parse mdateMax on
      // every row during a 2,888-row scan.
      if (dateEnreg) {
        const d = new Date(dateEnreg);
        if (!isNaN(d) && (!mdateMaxDate || d > mdateMaxDate)) {
          mdateMax = dateEnreg;
          mdateMaxDate = d;
        }
      }
      const cdate_month = monthKey(dateEnreg);

      rows.push({
        lat, lon,
        libelle,
        structure,
        structure_norm,
        etablissement,
        insee,
        commune_nom,
        epci_siren,
        epci_nom,
        // Search index: all the human-facing strings a journalist might type.
        // Accent-stripped and lowercased so "reserve" matches "RÉSERVE" etc.
        // Includes commune + EPCI names so free-text search finds them.
        search_index: normaliseForSearch(
          [libelle, structure, etablissement, r.siret, insee, commune_nom, epci_nom]
            .filter(Boolean).join(' ')
        ),
        regime,
        seveso,
        priority,
        ied,
        industrie,
        carriere,
        cdate_month,
        fiche: r.url_fiche_georisques || '',
        siret: r.siret || '',
        date_enregistrement: dateEnreg,
        activite: (r.code_naf_division || '').toString(),
        isSeveso: seveso === 'SEUIL_HAUT' || seveso === 'SEUIL_BAS',
        color,
      });
    }
    state.mdateMax = mdateMax;
    return rows;
  }

  // ---------- reference data (communes / EPCIs / structures) ----------
  function buildReferenceData() {
    // Communes: deduplicated by INSEE, with a row count so the user sees
    // which ones actually have sites (useful for "why is my filter empty?")
    const communeMap = new Map();   // insee → commune object
    const epciMap = new Map();       // siren → epci object
    const structureMap = new Map();  // norm → structure object

    for (const row of state.rows) {
      if (row.insee && row.commune_nom) {
        const c = communeMap.get(row.insee);
        if (c) {
          c.count++;
        } else {
          communeMap.set(row.insee, {
            code: row.insee,
            nom: row.commune_nom,
            norm: normaliseForSearch(row.commune_nom),
            epci_siren: row.epci_siren,
            epci_nom: row.epci_nom,
            count: 1,
          });
        }
      }
      if (row.epci_siren && row.epci_nom) {
        const e = epciMap.get(row.epci_siren);
        if (e) {
          e.site_count++;
        } else {
          epciMap.set(row.epci_siren, {
            code: row.epci_siren,
            nom: row.epci_nom,
            norm: normaliseForSearch(row.epci_nom),
            site_count: 1,
          });
        }
      }
      if (row.structure && row.structure_norm) {
        const s = structureMap.get(row.structure_norm);
        if (s) {
          s.count++;
        } else {
          structureMap.set(row.structure_norm, {
            name: row.structure,
            norm: row.structure_norm,
            count: 1,
          });
        }
      }
    }

    reference.communes = [...communeMap.values()].sort((a, b) => a.nom.localeCompare(b.nom, 'fr'));
    reference.epcis = [...epciMap.values()].sort((a, b) => a.nom.localeCompare(b.nom, 'fr'));
    // Structures: only those with at least 2 sites are useful as a filter
    // option (otherwise they're single sites and the site search handles them)
    reference.structures = [...structureMap.values()]
      .filter((s) => s.count >= 2)
      .sort((a, b) => b.count - a.count);

    // O(1) lookups keyed by the code that the filter Sets store
    reference.communeByInsee = communeMap;
    reference.epciByCode = epciMap;
    reference.structureByNorm = structureMap;
  }

  // ---------- suggestion engine ----------
  function getSuggestions(query) {
    const tokens = normaliseForSearch(query).split(' ').filter(Boolean);
    if (tokens.length === 0) return [];
    const matches = (norm) => {
      for (const t of tokens) if (!norm.includes(t)) return false;
      return true;
    };
    const groups = [];
    // Communes
    const commHits = [];
    for (const c of reference.communes) {
      if (matches(c.norm) && !state.filters.commune.has(c.code)) {
        commHits.push(c);
        if (commHits.length >= SUGGESTION_LIMIT_PER_GROUP) break;
      }
    }
    if (commHits.length) groups.push({ type: 'commune', label: 'Communes', items: commHits });
    // EPCI
    const epciHits = [];
    for (const e of reference.epcis) {
      if (matches(e.norm) && !state.filters.epci.has(e.code)) {
        epciHits.push(e);
        if (epciHits.length >= SUGGESTION_LIMIT_PER_GROUP) break;
      }
    }
    if (epciHits.length) groups.push({ type: 'epci', label: 'EPCI', items: epciHits });
    // Structures (entreprises)
    const structHits = [];
    for (const s of reference.structures) {
      if (matches(s.norm) && !state.filters.structure.has(s.norm)) {
        structHits.push(s);
        if (structHits.length >= SUGGESTION_LIMIT_PER_GROUP) break;
      }
    }
    if (structHits.length) groups.push({ type: 'structure', label: 'Structures', items: structHits });
    // Sites (individual ICPE)
    const siteHits = [];
    for (const row of state.rows) {
      if (matches(row.search_index)) {
        siteHits.push(row);
        if (siteHits.length >= SUGGESTION_LIMIT_PER_GROUP) break;
      }
    }
    if (siteHits.length) groups.push({ type: 'site', label: 'Sites', items: siteHits });
    return groups;
  }

  // ---------- filter predicate ----------
  function buildPredicate() {
    const f = state.filters;
    // Free-text (substring) search — token-wise AND, accent-insensitive.
    const searchTokens = normaliseForSearch(f.freeSearch).split(' ').filter(Boolean);
    const hasSearch = searchTokens.length > 0;
    const hasSecteur = f.secteur.size > 0;
    const hasCommune = f.commune.size > 0;
    const hasEpci = f.epci.size > 0;
    const hasStructure = f.structure.size > 0;
    const monthActive = f.monthEnabled && f.month;

    return function (row) {
      if (!f.regime.has(row.regime)) return false;
      if (!f.seveso.has(row.seveso)) return false;
      if (f.priority === 'yes' && !row.priority) return false;
      if (f.priority === 'no' && row.priority) return false;
      if (f.ied === 'yes' && !row.ied) return false;
      if (f.ied === 'no' && row.ied) return false;
      if (hasSecteur) {
        let any = false;
        if (f.secteur.has('industrie') && row.industrie) any = true;
        if (f.secteur.has('carriere') && row.carriere) any = true;
        if (f.secteur.has('autre') && !row.industrie && !row.carriere) any = true;
        if (!any) return false;
      }
      // Pill filters: OR within each Set, AND across Sets.
      if (hasCommune && !f.commune.has(row.insee)) return false;
      if (hasEpci && !f.epci.has(row.epci_siren)) return false;
      if (hasStructure && !f.structure.has(row.structure_norm)) return false;
      if (hasSearch) {
        const idx = row.search_index;
        for (let i = 0; i < searchTokens.length; i++) {
          if (!idx.includes(searchTokens[i])) return false;
        }
      }
      // Month window — only show rows whose cdate falls in the selected month.
      // Rows without any recorded date pass through (they exist but have no
      // temporal anchor; hiding them would be silent data loss).
      if (monthActive && row.cdate_month && row.cdate_month !== f.month) return false;
      return true;
    };
  }

  // ---------- marker creation ----------
  const markerByRow = new WeakMap();

  function makeMarker(row) {
    const isSeveso = row.isSeveso;
    const marker = L.circleMarker([row.lat, row.lon], {
      radius: isSeveso ? 7 : 5,
      weight: isSeveso ? 2 : 1,
      color: isSeveso ? CSS.ink : CSS.paper,
      fillColor: row.color[state.colorDim],
      fillOpacity: 0.88,
      renderer: canvasRenderer,
    });
    marker._row = row;
    marker.bindTooltip(escapeHTML(row.libelle), {
      direction: 'top',
      offset: [0, -6],
      sticky: true,
      className: 'site-tooltip',
    });
    marker.on('click', () => {
      marker.bindPopup(buildPopupHTML(row), {
        className: 'site-popup',
        maxWidth: 340,
        minWidth: 260,
        autoPanPadding: [40, 40],
      }).openPopup();
    });
    markerByRow.set(row, marker);
    return marker;
  }

  function buildPopupHTML(row) {
    const parts = [];
    parts.push(`<h3 class="popup-name">${escapeHTML(row.libelle)}</h3>`);
    parts.push('<div class="popup-badges">');
    if (REGIME_LABEL[row.regime]) {
      parts.push(`<span class="badge ${REGIME_BADGE[row.regime]}">${REGIME_LABEL[row.regime]}</span>`);
    }
    if (SEVESO_LABEL[row.seveso]) {
      parts.push(`<span class="badge ${SEVESO_BADGE[row.seveso]}">${SEVESO_LABEL[row.seveso]}</span>`);
    }
    if (row.priority) parts.push(`<span class="badge badge--copper">Priorité nationale</span>`);
    if (row.ied) parts.push(`<span class="badge badge--azur">IED</span>`);
    parts.push('</div>');

    parts.push('<dl class="popup-grid">');
    if (row.structure && row.etablissement && row.structure !== row.libelle) {
      parts.push(`<dt>Structure</dt><dd>${escapeHTML(row.structure)}</dd>`);
      parts.push(`<dt>Établissement</dt><dd>${escapeHTML(row.etablissement)}</dd>`);
    }
    if (row.activite) {
      const label = NAF_DIVISIONS[row.activite] || null;
      if (label) {
        parts.push(`<dt>Activité</dt><dd class="popup-activity">${escapeHTML(label)} <em>(NAF ${escapeHTML(row.activite)})</em></dd>`);
      } else {
        parts.push(`<dt>Activité</dt><dd>NAF ${escapeHTML(row.activite)}</dd>`);
      }
    }
    if (row.date_enregistrement) parts.push(`<dt>Date d'enregistrement</dt><dd>${formatDateFR(row.date_enregistrement)}</dd>`);
    if (row.siret) parts.push(`<dt>SIRET</dt><dd>${escapeHTML(row.siret)}</dd>`);
    if (row.insee) parts.push(`<dt>INSEE</dt><dd>${escapeHTML(row.insee)}</dd>`);
    parts.push(`<dt>Lat, Lon</dt><dd>${row.lat.toFixed(5)}, ${row.lon.toFixed(5)}</dd>`);
    parts.push('</dl>');

    const href = safeHref(row.fiche);
    if (href) {
      parts.push(`<a class="popup-fiche" href="${href}" target="_blank" rel="noopener">Fiche Géorisques <span class="sr-only">(ouvre dans un nouvel onglet)</span>→</a>`);
    }
    return parts.join('');
  }

  // ---------- map setup ----------
  const canvasRenderer = L.canvas({ padding: 0.5 });
  const map = L.map('map', {
    preferCanvas: true,
    renderer: canvasRenderer,
    zoomControl: true,
    attributionControl: true,
    minZoom: 7,
    maxZoom: 18,
  });

  // initial view — will be overridden once we have Gironde bounds
  map.setView([44.85, -0.55], 9);

  // base layers
  const baseLayers = {
    'Voyager': L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
      subdomains: 'abcd',
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    }),
    'OSM France': L.tileLayer('https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png', {
      subdomains: 'abc',
      maxZoom: 19,
      attribution: '&copy; Contributeurs OpenStreetMap · OSM France',
    }),
    'Satellite': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom: 19,
      attribution: 'Tiles &copy; Esri',
    }),
  };
  baseLayers['Voyager'].addTo(map);

  // overlay groups (created empty, populated after data loads)
  const girondeLayer = L.geoJSON(null, {
    style: { color: CSS.rule, weight: 2, fill: false },
    interactive: false,
  });
  const communesLayer = L.geoJSON(null, {
    style: { color: CSS.lead, weight: 0.5, opacity: 0.6, fill: false },
    interactive: false,
  });
  // EPCI outlines — lazy-loaded on first EPCI filter activation. The file
  // is ~1.2 MB uncompressed (375 KB gzipped) and most users won't touch
  // an EPCI filter, so we pay the download cost only on demand. Once
  // loaded, `epciFeaturesBySiren` serves the layer without re-fetching.
  const epciOutlineLayer = L.geoJSON(null, {
    style: {
      color: CSS.azur,
      weight: 2.5,
      fillColor: CSS.azur,
      fillOpacity: 0.06,
      dashArray: '4 3',
    },
    interactive: false,
  });
  let epciFeaturesBySiren = null; // null = not loaded; Map<siren,Feature> once loaded
  let epciLoadPromise = null;     // in-flight fetch promise (coalesces rapid toggles)
  const rnnLayer = L.geoJSON(null, {
    style: { color: CSS.mossDeep, weight: 1.5, fillColor: CSS.moss, fillOpacity: 0.18 },
    onEachFeature: (feat, layer) => {
      const p = feat.properties || {};
      layer.bindPopup(buildReservePopup(p, 'Réserve Naturelle Nationale'), {
        className: 'site-popup reserve-popup',
      });
    },
  });
  const rnrLayer = L.geoJSON(null, {
    style: { color: CSS.oliveDeep, weight: 1.5, fillColor: CSS.olive, fillOpacity: 0.15 },
    onEachFeature: (feat, layer) => {
      const p = feat.properties || {};
      layer.bindPopup(buildReservePopup(p, 'Réserve Naturelle Régionale'), {
        className: 'site-popup reserve-popup',
      });
    },
  });

  // Lazy-load the EPCI outlines GeoJSON on first demand.
  async function ensureEpciOutlinesLoaded() {
    if (epciFeaturesBySiren) return epciFeaturesBySiren;
    if (epciLoadPromise) return epciLoadPromise;
    epciLoadPromise = fetchJSON(EPCI_OUTLINES_URL)
      .then((data) => {
        const lookup = new Map();
        for (const feat of (data && data.features) || []) {
          const siren = feat && feat.properties && feat.properties.siren;
          if (siren) lookup.set(siren, feat);
        }
        epciFeaturesBySiren = lookup;
        return lookup;
      })
      .catch((err) => {
        console.error('EPCI outlines load failed', err);
        epciLoadPromise = null; // allow retry
        throw err;
      });
    return epciLoadPromise;
  }

  // Redraw the EPCI outline layer based on state.filters.epci, and swap
  // the Gironde département contour in/out accordingly.
  async function updateEpciOutlines() {
    const active = state.filters.epci;
    if (active.size === 0) {
      // No EPCI filter: restore the département outline, clear the layer.
      epciOutlineLayer.clearLayers();
      if (!map.hasLayer(girondeLayer)) girondeLayer.addTo(map);
      return;
    }
    // At least one EPCI selected — hide the département contour,
    // show the EPCI outlines instead.
    if (map.hasLayer(girondeLayer)) map.removeLayer(girondeLayer);
    if (!map.hasLayer(epciOutlineLayer)) epciOutlineLayer.addTo(map);
    try {
      const lookup = await ensureEpciOutlinesLoaded();
      // State may have changed while awaiting; re-check.
      const current = state.filters.epci;
      if (current.size === 0) {
        epciOutlineLayer.clearLayers();
        if (!map.hasLayer(girondeLayer)) girondeLayer.addTo(map);
        return;
      }
      epciOutlineLayer.clearLayers();
      for (const siren of current) {
        const feat = lookup.get(siren);
        if (feat) epciOutlineLayer.addData(feat);
      }
    } catch (_) {
      // Fetch failed — keep the département contour as a safe fallback.
      if (!map.hasLayer(girondeLayer)) girondeLayer.addTo(map);
      epciOutlineLayer.clearLayers();
    }
  }

  function buildReservePopup(p, typeLabel) {
    const parts = [];
    parts.push(`<h3 class="popup-name">${escapeHTML(p.nom || 'Sans nom')}</h3>`);
    parts.push(`<div class="popup-badges"><span class="badge">${typeLabel}</span></div>`);
    parts.push('<dl class="popup-grid">');
    if (p.date_crea) parts.push(`<dt>Création</dt><dd>${formatDateFR(p.date_crea)}</dd>`);
    if (p.surf_ha) parts.push(`<dt>Surface</dt><dd>${Number(p.surf_ha).toLocaleString('fr-FR', {maximumFractionDigits: 1})} ha</dd>`);
    if (p.operateur) parts.push(`<dt>Opérateur</dt><dd>${escapeHTML(p.operateur)}</dd>`);
    if (p.gest_site) parts.push(`<dt>Gestionnaire</dt><dd>${escapeHTML(p.gest_site)}</dd>`);
    parts.push('</dl>');
    const href = safeHref(p.url_fiche);
    if (href) {
      parts.push(`<a class="popup-fiche" href="${href}" target="_blank" rel="noopener">Fiche INPN <span class="sr-only">(ouvre dans un nouvel onglet)</span>→</a>`);
    }
    return parts.join('');
  }

  // cluster group for ICPE markers
  const clusterGroup = L.markerClusterGroup({
    chunkedLoading: true,
    chunkedInterval: 100,
    removeOutsideVisibleBounds: true,
    maxClusterRadius: 48,
    showCoverageOnHover: false,
    spiderfyOnMaxZoom: true,
    iconCreateFunction: (cluster) => {
      const children = cluster.getAllChildMarkers();
      const n = children.length;
      // count categories at current color dim to pick accent ring
      const counts = new Map();
      for (const m of children) {
        const c = m._row.color[state.colorDim];
        counts.set(c, (counts.get(c) || 0) + 1);
      }
      let majority = CSS.rust, best = 0;
      for (const [c, k] of counts) {
        if (k > best) { best = k; majority = c; }
      }
      const size = n < 10 ? 32 : n < 100 ? 38 : n < 500 ? 44 : 52;
      return L.divIcon({
        html: `<div class="marker-cluster-ink" style="width:${size}px;height:${size}px;--cluster-accent:${majority};">${formatCount(n)}</div>`,
        className: '',
        iconSize: [size, size],
      });
    },
  });

  // ordering: reserves under markers, contours on top of tiles but below markers
  girondeLayer.addTo(map);
  rnnLayer.addTo(map);
  // communesLayer, rnrLayer and epciOutlineLayer added via control / on demand
  clusterGroup.addTo(map);

  // layer control
  // Scale bar + compass rose — both discreet, toggled via the layer
  // control as "Échelle & boussole". Uses a dummy LayerGroup: when
  // the user unchecks it in the COUCHES panel, Leaflet fires
  // overlayremove and we remove the controls. Persisted in localStorage.
  const scaleControl = L.control.scale({ metric: true, imperial: false, position: 'bottomleft' });
  const compassControl = (function () {
    const Compass = L.Control.extend({
      options: { position: 'bottomright' },
      onAdd: function () {
        const div = L.DomUtil.create('div', 'compass leaflet-control');
        div.setAttribute('aria-hidden', 'true');
        div.title = 'Nord en haut';
        div.innerHTML = '<span class="compass__n">N</span>' +
          '<span class="compass__cross">+</span>' +
          '<span class="compass__labels">' +
            '<span class="compass__w">O</span>' +
            '<span class="compass__e">E</span>' +
          '</span>' +
          '<span class="compass__s">S</span>';
        return div;
      },
    });
    return new Compass();
  })();

  const cartographicKey = 'carte:show-cartographic';
  const cartographicDummy = L.layerGroup(); // empty — just a toggle handle
  let cartographicVisible = false;
  function showCartographicControls() {
    if (cartographicVisible) return;
    scaleControl.addTo(map);
    compassControl.addTo(map);
    cartographicVisible = true;
  }
  function hideCartographicControls() {
    if (!cartographicVisible) return;
    scaleControl.remove();
    compassControl.remove();
    cartographicVisible = false;
  }
  map.on('overlayadd', function (e) {
    if (e.layer === cartographicDummy) {
      showCartographicControls();
      try { localStorage.setItem(cartographicKey, '1'); } catch (_) {}
    }
  });
  map.on('overlayremove', function (e) {
    if (e.layer === cartographicDummy) {
      hideCartographicControls();
      try { localStorage.setItem(cartographicKey, '0'); } catch (_) {}
    }
  });

  // Default: visible. Respect localStorage.
  const showCarto = (() => { try { return localStorage.getItem(cartographicKey) !== '0'; } catch (_) { return true; } })();
  if (showCarto) {
    cartographicDummy.addTo(map);
    showCartographicControls();
  }

  const overlays = {
    'Contour Gironde': girondeLayer,
    'Contours EPCI': epciOutlineLayer,
    'Communes': communesLayer,
    'Réserves Nat. Nationales': rnnLayer,
    'Réserves Nat. Régionales': rnrLayer,
    'ICPE': clusterGroup,
    'Échelle & boussole': cartographicDummy,
  };
  L.control.layers(baseLayers, overlays, { collapsed: true, position: 'topright' }).addTo(map);

  // ---------- cached DOM references (queried once at module load) ----------
  const siteCountEl    = document.getElementById('site-count');
  const siteMdateEl    = document.getElementById('site-mdate');
  const counterShown   = document.getElementById('counter-shown');
  const counterTotal   = document.getElementById('counter-total');
  const slider         = document.getElementById('time-slider');
  const sliderValue    = document.getElementById('time-slider-value');
  const sliderCountEl  = document.getElementById('time-slider-count');
  const legendEl       = document.getElementById('legend');
  const legendDimEl    = document.getElementById('legend-dim');
  const legendItemsEl  = document.getElementById('legend-items');

  // Pre-computed counts per month key, populated at CSV load
  const monthCounts = new Map();

  function showError(msg) {
    const existing = document.querySelector('.error-banner');
    if (existing) existing.remove();
    const div = document.createElement('div');
    div.className = 'error-banner';
    div.setAttribute('role', 'alert'); // implies aria-live=assertive
    div.textContent = msg;
    document.body.appendChild(div);
  }

  async function init() {
    // Start all data loads in parallel (all local static files).
    // Every fetch goes through fetchJSON so errors surface consistently
    // in Promise.allSettled's .reason instead of being swallowed.
    const [csvResult, girondeResult, rnnResult, rnrResult] = await Promise.allSettled([
      parseCSV(),
      fetchJSON(GIRONDE_CONTOUR_URL),
      fetchJSON(RNN_URL),
      fetchJSON(RNR_URL),
    ]);

    // Gironde contour
    if (girondeResult.status === 'fulfilled' && girondeResult.value) {
      girondeLayer.addData(girondeResult.value);
      try {
        map.fitBounds(girondeLayer.getBounds(), { padding: [20, 20] });
      } catch (_) { /* ignore */ }
    } else {
      console.warn('Gironde contour load failed', girondeResult.reason);
    }

    // RNN / RNR — log failures instead of silently hiding the layer
    if (rnnResult.status === 'fulfilled' && rnnResult.value && rnnResult.value.features) {
      rnnLayer.addData(rnnResult.value);
    } else if (rnnResult.status === 'rejected') {
      console.warn('RNN load failed', rnnResult.reason);
    }
    if (rnrResult.status === 'fulfilled' && rnrResult.value && rnrResult.value.features) {
      rnrLayer.addData(rnrResult.value);
    } else if (rnrResult.status === 'rejected') {
      console.warn('RNR load failed', rnrResult.reason);
    }

    // CSV
    if (csvResult.status !== 'fulfilled') {
      showError('Impossible de charger la liste des ICPE.');
      console.error(csvResult.reason);
      return;
    }
    state.rows = transformRows(csvResult.value);
    if (state.rows.length === 0) {
      showError('Aucun site géolocalisé dans les données — format de CSV inattendu.');
      return;
    }

    // Build reference lists used by the search suggestions and the EPCI
    // filter checkbox list. Everything is derived from the rows — the
    // enriched CSV now carries commune/EPCI directly.
    buildReferenceData();

    // Hydrate filter state from any ?param=value in the URL. This happens
    // after the reference data is built because we want to validate codes
    // against the known set (e.g. only honor an ?epci= that matches a
    // real Gironde SIREN).
    applyParsedUrlState(parseUrlToFilters());

    // Embed mode toggles a body class that hides the masthead and tightens
    // the layout. Safe to apply after Leaflet has initialised because the
    // grid row sizes just shift.
    if (isEmbedMode()) {
      document.body.classList.add('is-embed');
    }

    // ?hide=sidebar,timebar,legend,layers,zoom,reserves — strips specific
    // pieces of the UI. Used by embeds that only want a minimal map.
    const hiddenFeatures = parseHiddenFeatures();
    applyHiddenFeatures(hiddenFeatures);

    // Leaflet needs a hint to recompute its size after any grid row
    // collapses (embed mode or feature-hiding), otherwise tile math is
    // wrong on first paint.
    setTimeout(() => map.invalidateSize(), 0);

    // header metadata
    siteCountEl.textContent = `${formatCount(state.rows.length)} sites`;
    siteMdateEl.textContent = formatDateFR(state.mdateMax);
    siteMdateEl.setAttribute('datetime', state.mdateMax || '');
    counterTotal.textContent = formatCount(state.rows.length);

    // derive month keys from the data (unique YYYY-MM values, sorted)
    const mSet = new Set();
    for (const row of state.rows) {
      if (row.cdate_month) mSet.add(row.cdate_month);
    }
    state.monthSteps = Array.from(mSet).sort();
    // Default month selection = most recent. Honor any value already set
    // by URL hydration (applyParsedUrlState ran earlier).
    if (!state.filters.month) {
      state.filters.month = state.monthSteps.length
        ? state.monthSteps[state.monthSteps.length - 1]
        : null;
    }

    // Pre-compute per-month counts once (static after CSV load)
    monthCounts.clear();
    for (const row of state.rows) {
      if (row.cdate_month) {
        monthCounts.set(row.cdate_month, (monthCounts.get(row.cdate_month) || 0) + 1);
      }
    }

    // configure the slider (starts disabled; checkbox enables it)
    if (state.monthSteps.length >= 1) {
      slider.min = '0';
      slider.max = String(Math.max(0, state.monthSteps.length - 1));
      slider.step = '1';
      slider.value = slider.max;
      slider.disabled = state.monthSteps.length < 2;
      slider.setAttribute('aria-valuetext', formatMonthFR(state.filters.month));
      sliderValue.textContent = formatMonthFR(state.filters.month);
    } else {
      slider.disabled = true;
    }

    // Build markers once; applyFilters() below does the initial cluster add.
    for (const row of state.rows) makeMarker(row);

    // legend
    renderLegend();

    // wire up controls BEFORE applying filters so the DOM event listeners
    // exist (they don't need to fire yet, just to be bound)
    wireUp();

    // Sync any hydrated state from URL into the DOM (checkboxes, segmented
    // buttons, pills, EPCI outlines), then run the initial filter pass.
    syncUiFromState();
    applyFilters();
    updateEpciOutlines();

    // Install the postMessage resize bridge if we're inside an iframe.
    installEmbedResizeBridge();
  }

  // Reflect state.filters and state.colorDim back into the DOM. Used after
  // URL hydration and after the reset button clears state.
  function syncUiFromState() {
    // Free-text search input
    const searchInput = document.getElementById('search-input');
    if (searchInput) searchInput.value = state.filters.freeSearch || '';
    // Régime checkboxes
    document.querySelectorAll('input[type="checkbox"][data-filter="regime"]').forEach((cb) => {
      cb.checked = state.filters.regime.has(cb.value);
    });
    // Seveso checkboxes
    document.querySelectorAll('input[type="checkbox"][data-filter="seveso"]').forEach((cb) => {
      cb.checked = state.filters.seveso.has(cb.value);
    });
    // Secteur checkboxes
    document.querySelectorAll('input[type="checkbox"][data-filter="secteur"]').forEach((cb) => {
      cb.checked = state.filters.secteur.has(cb.value);
    });
    // EPCI checkboxes (rendered dynamically — happens in wireUp)
    document.querySelectorAll('input[type="checkbox"][data-filter="epci"]').forEach((cb) => {
      cb.checked = state.filters.epci.has(cb.value);
    });
    // Priority / IED radios
    for (const key of ['priority', 'ied']) {
      const current = state.filters[key];
      document.querySelectorAll(`[data-filter="${key}"]`).forEach((b) => {
        const isActive = b.dataset.value === current;
        b.classList.toggle('is-active', isActive);
        b.setAttribute('aria-checked', isActive ? 'true' : 'false');
        b.tabIndex = isActive ? 0 : -1;
      });
    }
    // Color dimension segmented control
    document.querySelectorAll('[data-color-dim]').forEach((b) => {
      const isActive = b.dataset.colorDim === state.colorDim;
      b.classList.toggle('is-active', isActive);
      b.setAttribute('aria-checked', isActive ? 'true' : 'false');
      b.tabIndex = isActive ? 0 : -1;
    });
    // Month filter: slider + checkbox. Handle BOTH the enabled and the
    // disabled case so the slider visually dims after Reset.
    const monthCheckbox = document.getElementById('month-enabled');
    const timebar = document.getElementById('timebar');
    if (monthCheckbox) {
      monthCheckbox.checked = state.filters.monthEnabled;
      if (state.filters.monthEnabled) {
        if (state.filters.month) {
          const idx = state.monthSteps.indexOf(state.filters.month);
          if (idx >= 0) slider.value = String(idx);
          sliderValue.textContent = formatMonthFR(state.filters.month);
          slider.setAttribute('aria-valuetext', formatMonthFR(state.filters.month));
        }
        slider.disabled = state.monthSteps.length < 2;
        if (timebar) timebar.classList.remove('is-disabled');
      } else {
        slider.disabled = true;
        if (timebar) timebar.classList.add('is-disabled');
      }
    }
    // Pills (commune / epci / structure) and EPCI checkbox list
    renderPills();
    syncEpciCheckboxes();
    // Legend updates if color dim changed
    renderLegend();
  }

  // ---------- filtering ----------
  function applyFilters() {
    const predicate = buildPredicate();
    const visible = state.rows.filter(predicate);
    state.visibleRows = visible;
    counterShown.textContent = formatCount(visible.length);

    // Bottom-bar month count:
    //   - filter active → count of currently visible rows (same as counter)
    //   - filter inactive → preview count from the pre-computed monthCounts
    //     map (static, O(1) lookup), intersected with other active filters
    //     for coherence between the preview and the eventual enabled view
    if (state.filters.monthEnabled) {
      sliderCountEl.textContent = formatCount(visible.length);
    } else if (state.filters.month) {
      // Preview = how many of state.visibleRows would remain if the month
      // filter were enabled right now. Scan visible (usually small after
      // other filters) rather than the full dataset.
      const m = state.filters.month;
      let n = 0;
      for (const r of visible) if (r.cdate_month === m) n++;
      sliderCountEl.textContent = formatCount(n);
    } else {
      sliderCountEl.textContent = '—';
    }

    // Rebuild cluster layer with the filtered subset
    clusterGroup.clearLayers();
    const markers = new Array(visible.length);
    for (let i = 0; i < visible.length; i++) markers[i] = markerByRow.get(visible[i]);
    clusterGroup.addLayers(markers);

    // If the embed dialog is open, keep its preview URL in sync with the
    // filter state so the user can't accidentally copy a stale URL.
    if (typeof window.__icpeEmbedDialogRefresh === 'function') {
      window.__icpeEmbedDialogRefresh();
    }
  }

  function switchColorDim(dim) {
    state.colorDim = dim;
    // Mutate each marker's fillColor option in place — no setStyle redraw
    // per marker. Then ask the canvas renderer to repaint once for the
    // whole layer, batched into a single frame.
    for (const row of state.rows) {
      const m = markerByRow.get(row);
      if (m) m.options.fillColor = row.color[dim];
    }
    requestAnimationFrame(() => {
      if (canvasRenderer._redraw) canvasRenderer._redraw();
      clusterGroup.refreshClusters();
    });
    renderLegend();
  }

  // ---------- legend ----------
  function renderLegend() {
    const dim = state.colorDim;
    legendDimEl.textContent = DIM_HUMAN[dim];
    const frag = document.createDocumentFragment();
    for (const [label, color] of LEGEND_LABELS[dim]) {
      const li = document.createElement('li');
      const swatch = document.createElement('span');
      swatch.className = 'legend-swatch';
      swatch.style.background = color;
      li.appendChild(swatch);
      li.appendChild(document.createTextNode(label));
      frag.appendChild(li);
    }
    legendItemsEl.replaceChildren(frag);
    legendEl.classList.toggle('hide-seveso-row', dim === 'seveso');
  }

  // ---------- EPCI checkbox list (populated from reference data) ----------
  function populateEpciList() {
    const container = document.getElementById('epci-list');
    if (!container) return;
    container.innerHTML = '';
    for (const epci of reference.epcis) {
      const label = document.createElement('label');
      label.className = 'check';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = epci.code;
      cb.dataset.filter = 'epci';
      cb.addEventListener('change', () => {
        if (cb.checked) state.filters.epci.add(epci.code);
        else state.filters.epci.delete(epci.code);
        renderPills();
        applyFilters();
        updateEpciOutlines();
      });
      const text = document.createElement('span');
      text.className = 'check__text';
      text.appendChild(document.createTextNode(epci.nom));
      const count = document.createElement('span');
      count.className = 'check__count';
      count.textContent = ` (${formatCount(epci.site_count)})`;
      text.appendChild(count);
      label.appendChild(cb);
      label.appendChild(text);
      container.appendChild(label);
    }
  }

  // ---------- search combobox: pills + suggestion dropdown ----------
  let searchDebounce;
  let sliderDebounce;

  function renderPills() {
    const container = document.getElementById('search-pills');
    if (!container) return;
    container.innerHTML = '';
    const add = (type, key, label) => {
      const pill = document.createElement('span');
      pill.className = `pill pill--${type}`;
      pill.setAttribute('role', 'listitem');
      pill.appendChild(document.createTextNode(label));
      const x = document.createElement('button');
      x.type = 'button';
      x.className = 'pill__remove';
      x.setAttribute('aria-label', `Retirer ${label}`);
      x.textContent = '×';
      x.addEventListener('click', () => {
        state.filters[type].delete(key);
        renderPills();
        if (type === 'epci') {
          syncEpciCheckboxes();
          updateEpciOutlines();
        }
        applyFilters();
        // Return focus to the search input so the keyboard user doesn't
        // land on document.body after the button they clicked is destroyed.
        const searchInput = document.getElementById('search-input');
        if (searchInput) searchInput.focus();
      });
      pill.appendChild(x);
      container.appendChild(pill);
    };
    for (const code of state.filters.commune) {
      const c = reference.communeByInsee.get(code);
      add('commune', code, c ? c.nom : code);
    }
    for (const code of state.filters.epci) {
      const e = reference.epciByCode.get(code);
      add('epci', code, e ? e.nom : code);
    }
    for (const norm of state.filters.structure) {
      const s = reference.structureByNorm.get(norm);
      add('structure', norm, s ? s.name : norm);
    }
  }

  // Sync the EPCI checkbox list's checked state from state.filters.epci.
  // Called only from the paths that mutate state.filters.epci (pill remove,
  // suggestion select, reset, URL hydration) — not on every renderPills().
  function syncEpciCheckboxes() {
    document.querySelectorAll('input[type="checkbox"][data-filter="epci"]').forEach((cb) => {
      cb.checked = state.filters.epci.has(cb.value);
    });
  }

  // Cache the last rendered item payloads so the delegated click handler
  // on the panel can look them up by index without rebuilding closures.
  let suggestionFlatItems = [];

  function renderSuggestions(groups) {
    const panel = document.getElementById('suggestions');
    const searchInput = document.getElementById('search-input');
    if (!panel) return;
    suggestionFlatItems = [];
    if (groups.length === 0) {
      panel.classList.add('is-hidden');
      panel.innerHTML = '';
      if (searchInput) searchInput.setAttribute('aria-expanded', 'false');
      return;
    }
    panel.classList.remove('is-hidden');
    if (searchInput) searchInput.setAttribute('aria-expanded', 'true');
    const frag = document.createDocumentFragment();
    let flatIndex = 0;
    for (const group of groups) {
      // Group heading — labelled so screen readers get context without
      // being exposed as a separate control.
      const h = document.createElement('div');
      h.className = 'suggestions__group-label';
      h.setAttribute('role', 'presentation');
      h.textContent = group.label;
      frag.appendChild(h);
      for (const item of group.items) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('role', 'option');
        btn.setAttribute('aria-selected', 'false');
        btn.dataset.flatIndex = String(flatIndex);
        btn.className = `suggestion suggestion--${group.type}`;
        const title = group.type === 'site'
          ? item.libelle
          : group.type === 'structure'
            ? item.name
            : item.nom;
        const count = group.type === 'commune'
          ? item.count
          : group.type === 'epci'
            ? item.site_count
            : group.type === 'structure'
              ? item.count
              : null;
        btn.appendChild(document.createTextNode(title));
        if (count != null) {
          const c = document.createElement('span');
          c.className = 'suggestion__count';
          c.textContent = ` (${formatCount(count)})`;
          btn.appendChild(c);
        }
        suggestionFlatItems.push({ type: group.type, item });
        flatIndex++;
        frag.appendChild(btn);
      }
    }
    panel.replaceChildren(frag);
  }

  function selectSuggestion(type, item) {
    const searchInput = document.getElementById('search-input');
    if (type === 'commune') {
      state.filters.commune.add(item.code);
    } else if (type === 'epci') {
      state.filters.epci.add(item.code);
    } else if (type === 'structure') {
      state.filters.structure.add(item.norm);
    } else if (type === 'site') {
      // Zoom + open popup, don't create a pill.
      map.flyTo([item.lat, item.lon], 15, { duration: 0.7 });
      const m = markerByRow.get(item);
      if (m) setTimeout(() => m.fire('click'), 700);
    }
    searchInput.value = '';
    state.filters.freeSearch = '';
    renderPills();
    renderSuggestions([]);
    applyFilters();
    if (type === 'epci') {
      syncEpciCheckboxes();
      updateEpciOutlines();
    }
    searchInput.focus();
  }

  // ---------- embed dialog ----------
  function wireEmbedDialog() {
    const openBtn = document.getElementById('embed-open');
    const dialog = document.getElementById('embed-dialog');
    const closeBtn = document.getElementById('embed-close');
    const includeFiltersCb = document.getElementById('embed-include-filters');
    const compactCb = document.getElementById('embed-compact');
    const lockBoundsCb = document.getElementById('embed-lock-bounds');
    const urlInput = document.getElementById('embed-url');
    const iframeOut = document.getElementById('embed-iframe-code');
    const heightInput = document.getElementById('embed-height');
    const copyUrlBtn = document.getElementById('embed-copy-url');
    const copyIframeBtn = document.getElementById('embed-copy-iframe');
    const copyScriptBtn = document.getElementById('embed-copy-script');
    const scriptOut = document.getElementById('embed-script-code');
    if (!openBtn || !dialog) return;

    // Build the iframe selector from the current URL so the generated
    // auto-resize script actually matches the embed the user is pasting.
    // The previous hardcoded slug never matched and the script silently
    // did nothing. encodeURI keeps it safe inside a CSS-ish attribute.
    const pageSlug = window.location.pathname.split('/').filter(Boolean).pop() || '/';
    const safeSelector = pageSlug.replace(/"/g, '\\"');

    let dialogOpen = false;

    const refresh = () => {
      const includeFilters = includeFiltersCb.checked;
      const compact = compactCb.checked;
      const lockBounds = lockBoundsCb ? lockBoundsCb.checked : false;
      // Collect hide-feature checkboxes (data-hide="sidebar" etc.)
      const hide = new Set();
      dialog.querySelectorAll('input[type="checkbox"][data-hide]').forEach((cb) => {
        if (cb.checked) hide.add(cb.dataset.hide);
      });
      const url = buildShareableUrl({ embed: compact, includeFilters, hide, lockBounds });
      urlInput.value = url;
      const height = Math.max(400, parseInt(heightInput.value, 10) || 780);
      iframeOut.value =
        `<iframe\n  src="${url}"\n  width="100%"\n  height="${height}"\n  style="border: 1px solid #d1ccc2; max-width: 100%;"\n  title="Carte des ICPE en Gironde"\n  loading="lazy"\n  allowfullscreen\n  referrerpolicy="no-referrer-when-downgrade"\n></iframe>`;
      scriptOut.value =
        `<script>\n// Optional: auto-resize the iframe based on its content.\n// Validate origin and clamp the height to safe bounds before applying.\nwindow.addEventListener('message', function (e) {\n  if (!e.data || e.data.type !== 'icpe-map-height') return;\n  var f = document.querySelector('iframe[src*="${safeSelector}"]');\n  if (!f) return;\n  var h = Math.max(400, Math.min(5000, Number(e.data.height) || 0));\n  if (h) f.style.height = h + 'px';\n});\n<\/script>`;
    };
    const open = () => {
      refresh();
      dialogOpen = true;
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    };
    const close = () => {
      dialogOpen = false;
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
      // Return focus to the button that opened the dialog.
      if (openBtn) openBtn.focus();
    };

    // Expose a refresh hook so applyFilters() can keep the dialog's URL
    // fresh when the underlying filter state changes while it's open.
    window.__icpeEmbedDialogRefresh = () => {
      if (dialogOpen) refresh();
    };
    const copy = async (text, btn) => {
      try {
        await navigator.clipboard.writeText(text);
        const prev = btn.textContent;
        btn.textContent = 'Copié ✓';
        setTimeout(() => { btn.textContent = prev; }, 1500);
      } catch (_) {
        btn.textContent = 'Échec';
      }
    };

    openBtn.addEventListener('click', open);
    closeBtn.addEventListener('click', close);
    includeFiltersCb.addEventListener('change', refresh);
    compactCb.addEventListener('change', refresh);
    if (lockBoundsCb) lockBoundsCb.addEventListener('change', refresh);
    dialog.querySelectorAll('input[type="checkbox"][data-hide]').forEach((cb) => {
      cb.addEventListener('change', refresh);
    });
    heightInput.addEventListener('input', refresh);
    copyUrlBtn.addEventListener('click', () => copy(urlInput.value, copyUrlBtn));
    copyIframeBtn.addEventListener('click', () => copy(iframeOut.value, copyIframeBtn));
    copyScriptBtn.addEventListener('click', () => copy(scriptOut.value, copyScriptBtn));
    // Close on backdrop click
    dialog.addEventListener('click', (e) => {
      if (e.target === dialog) close();
    });
  }

  // ---------- mobile sidebar (FAB-driven slide-in) ----------
  function wireMobileSidebar() {
    const fab = document.getElementById('mobile-filters-fab');
    const closeBtn = document.getElementById('mobile-filters-close');
    const fabLabel = fab ? fab.querySelector('.mobile-filters-fab__label') : null;
    if (!fab) return;
    const open = () => {
      document.body.classList.add('mobile-filters-open');
      fab.setAttribute('aria-expanded', 'true');
      fab.setAttribute('aria-label', 'Masquer les filtres');
      if (fabLabel) fabLabel.textContent = 'Fermer';
    };
    const close = () => {
      document.body.classList.remove('mobile-filters-open');
      fab.setAttribute('aria-expanded', 'false');
      fab.setAttribute('aria-label', 'Afficher les filtres');
      if (fabLabel) fabLabel.textContent = 'Filtres';
      fab.focus(); // return focus to the control that opened the panel
    };
    fab.addEventListener('click', () => {
      if (document.body.classList.contains('mobile-filters-open')) close();
      else open();
    });
    if (closeBtn) closeBtn.addEventListener('click', close);
    // Escape key closes
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && document.body.classList.contains('mobile-filters-open')) {
        close();
      }
    });
  }

  function wireSearchCombobox() {
    const searchInput = document.getElementById('search-input');
    const panel = document.getElementById('suggestions');
    if (!searchInput || !panel) return;

    // Delegated click handler — one listener for the whole panel, looks
    // up the payload via data-flat-index instead of per-button closures.
    panel.addEventListener('click', (e) => {
      const btn = e.target.closest('.suggestion');
      if (!btn) return;
      const idx = parseInt(btn.dataset.flatIndex, 10);
      const entry = suggestionFlatItems[idx];
      if (entry) selectSuggestion(entry.type, entry.item);
    });

    // Both the suggestion dropdown and the free-text filter share a single
    // debounce timer. getSuggestions() scans up to 2,888 rows on each
    // keystroke, so running it inside the 150 ms debounce (instead of
    // synchronously) is the single biggest perf win for search.
    const runSearch = (q) => {
      renderSuggestions(getSuggestions(q));
      state.filters.freeSearch = q;
      applyFilters();
    };
    searchInput.addEventListener('input', () => {
      const q = searchInput.value;
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => runSearch(q), 150);
    });
    searchInput.addEventListener('focus', () => {
      if (searchInput.value) {
        renderSuggestions(getSuggestions(searchInput.value));
      }
    });
    // Close suggestions on outside click
    document.addEventListener('click', (e) => {
      if (!panel.contains(e.target) && e.target !== searchInput) {
        panel.classList.add('is-hidden');
        searchInput.setAttribute('aria-expanded', 'false');
      }
    });
    // Keyboard: Escape closes, Enter picks first suggestion, Down moves
    // focus into the first visible suggestion option.
    searchInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        panel.classList.add('is-hidden');
        searchInput.setAttribute('aria-expanded', 'false');
      } else if (e.key === 'Enter') {
        const firstBtn = panel.querySelector('.suggestion');
        if (firstBtn) {
          e.preventDefault();
          firstBtn.click();
        }
      } else if (e.key === 'ArrowDown') {
        const firstBtn = panel.querySelector('.suggestion');
        if (firstBtn) {
          e.preventDefault();
          firstBtn.focus();
        }
      }
    });
    // Arrow-key navigation within the suggestions panel itself.
    panel.addEventListener('keydown', (e) => {
      const items = Array.from(panel.querySelectorAll('.suggestion'));
      if (items.length === 0) return;
      const idx = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        items[(idx + 1) % items.length].focus();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (idx <= 0) searchInput.focus();
        else items[idx - 1].focus();
      } else if (e.key === 'Escape') {
        panel.classList.add('is-hidden');
        searchInput.setAttribute('aria-expanded', 'false');
        searchInput.focus();
      }
    });
  }

  // Wire up a single role=radiogroup: sync aria-checked, roving tabindex,
  // arrow-key navigation, and activation on click or Enter/Space.
  function wireRadioGroup(selector, onChange) {
    const buttons = Array.from(document.querySelectorAll(selector));
    if (buttons.length === 0) return;
    const activate = (btn) => {
      for (const b of buttons) {
        const isActive = b === btn;
        b.classList.toggle('is-active', isActive);
        b.setAttribute('aria-checked', isActive ? 'true' : 'false');
        b.tabIndex = isActive ? 0 : -1;
      }
      onChange(btn);
    };
    // initialise aria-checked / tabindex from whatever has is-active already
    const initial = buttons.find((b) => b.classList.contains('is-active')) || buttons[0];
    for (const b of buttons) {
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', b === initial ? 'true' : 'false');
      b.tabIndex = b === initial ? 0 : -1;
      b.addEventListener('click', () => activate(b));
      b.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
          e.preventDefault();
          const i = buttons.indexOf(b);
          const next = buttons[(i + 1) % buttons.length];
          next.focus();
          activate(next);
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
          e.preventDefault();
          const i = buttons.indexOf(b);
          const prev = buttons[(i - 1 + buttons.length) % buttons.length];
          prev.focus();
          activate(prev);
        } else if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          activate(b);
        }
      });
    }
  }

  function wireUp() {
    // color-by segmented
    wireRadioGroup('[data-color-dim]', (btn) => switchColorDim(btn.dataset.colorDim));

    // régime/seveso/secteur checkboxes
    const bindCheckGroup = (filterKey) => {
      document.querySelectorAll(`input[type="checkbox"][data-filter="${filterKey}"]`).forEach((cb) => {
        cb.addEventListener('change', () => {
          if (cb.checked) state.filters[filterKey].add(cb.value);
          else state.filters[filterKey].delete(cb.value);
          applyFilters();
        });
      });
    };
    bindCheckGroup('regime');
    bindCheckGroup('seveso');
    bindCheckGroup('secteur');

    // priority / ied radios
    for (const key of ['priority', 'ied']) {
      wireRadioGroup(`[data-filter="${key}"]`, (btn) => {
        state.filters[key] = btn.dataset.value;
        applyFilters();
      });
    }

    // populate the EPCI checkbox list dynamically from reference data
    populateEpciList();

    // search with suggestions + pills
    wireSearchCombobox();

    // month filter — checkbox toggles it on/off, slider picks the month
    const monthCheckbox = document.getElementById('month-enabled');
    const timebar = document.getElementById('timebar');
    const setSliderEnabled = (on) => {
      slider.disabled = !on || state.monthSteps.length < 2;
      timebar.classList.toggle('is-disabled', !on);
    };
    // Mirror the DOM checkbox from the state model. This defensively fixes
    // browser bfcache restoring a stale checked attribute, WITHOUT
    // clobbering URL-hydrated monthEnabled (which would break ?month=).
    monthCheckbox.checked = state.filters.monthEnabled;
    setSliderEnabled(state.filters.monthEnabled);

    // Snap the slider back to the earliest month. Used when enabling the
    // month filter or starting playback — keeping the previous slider
    // position would make the default view confusing ("why did everything
    // jump to April?"). Starting from the beginning is a predictable
    // chronology.
    const snapSliderToStart = () => {
      if (state.monthSteps.length === 0) return;
      slider.value = '0';
      const m = state.monthSteps[0];
      state.filters.month = m;
      sliderValue.textContent = formatMonthFR(m);
      slider.setAttribute('aria-valuetext', formatMonthFR(m));
    };
    slider.addEventListener('input', () => {
      const idx = parseInt(slider.value, 10);
      const m = state.monthSteps[idx];
      state.filters.month = m;
      sliderValue.textContent = formatMonthFR(m);
      slider.setAttribute('aria-valuetext', formatMonthFR(m));
      if (state.filters.monthEnabled) {
        clearTimeout(sliderDebounce);
        sliderDebounce = setTimeout(applyFilters, 60);
      }
    });

    // Play / pause for the month slider — auto-advances one month per tick.
    // Enabling Play also activates the month filter (the animation is
    // meaningless while every site is visible).
    const playBtn = document.getElementById('time-play');
    const playIcon = playBtn.querySelector('.time-play-icon');
    const loopCheckbox = document.getElementById('time-loop');
    const PLAY_INTERVAL_MS = 900;
    let playTimer = null;

    // Enable the play button whenever we have at least 2 months to walk between.
    playBtn.disabled = state.monthSteps.length < 2;

    const hud = document.getElementById('playback-hud');
    const hudText = document.getElementById('playback-hud-text');
    const showHud = (month) => {
      if (!hud || !hudText) return;
      hudText.textContent = formatMonthFR(month);
      hud.classList.add('is-visible');
    };
    const hideHud = () => {
      if (hud) hud.classList.remove('is-visible');
    };

    const stopPlayback = () => {
      if (playTimer) {
        clearInterval(playTimer);
        playTimer = null;
      }
      playIcon.textContent = '▶';
      playBtn.setAttribute('aria-label', 'Lecture');
      playBtn.setAttribute('title', 'Lecture');
      hideHud();
    };
    const startPlayback = () => {
      if (state.monthSteps.length < 2) return;
      // Auto-enable the month filter if it's off, AND snap to the earliest
      // month so playback always walks the full timeline from the start.
      if (!state.filters.monthEnabled) {
        state.filters.monthEnabled = true;
        monthCheckbox.checked = true;
        setSliderEnabled(true);
      }
      snapSliderToStart();
      applyFilters();
      playIcon.textContent = '⏸';
      playBtn.setAttribute('aria-label', 'Pause');
      playBtn.setAttribute('title', 'Pause');
      showHud(state.filters.month);
      playTimer = setInterval(() => {
        const maxIdx = state.monthSteps.length - 1;
        let idx = parseInt(slider.value, 10);
        if (idx >= maxIdx) {
          if (loopCheckbox.checked) {
            idx = 0;
          } else {
            stopPlayback();
            return;
          }
        } else {
          idx += 1;
        }
        slider.value = String(idx);
        // Reuse the same path as manual input
        const m = state.monthSteps[idx];
        state.filters.month = m;
        sliderValue.textContent = formatMonthFR(m);
        slider.setAttribute('aria-valuetext', formatMonthFR(m));
        showHud(m);
        applyFilters();
      }, PLAY_INTERVAL_MS);
    };
    playBtn.addEventListener('click', () => {
      if (playTimer) stopPlayback(); else startPlayback();
    });

    // Consolidated monthCheckbox handler: toggles filter state, enables/
    // disables the slider, snaps to start when enabling, and stops
    // playback when disabling. Single source of truth — the previous
    // code had two separate 'change' listeners racing.
    monthCheckbox.addEventListener('change', () => {
      state.filters.monthEnabled = monthCheckbox.checked;
      setSliderEnabled(monthCheckbox.checked);
      if (monthCheckbox.checked) {
        snapSliderToStart();
      } else {
        stopPlayback();
      }
      applyFilters();
    });

    // reset — clears every filter and resets the color dim to default
    document.getElementById('reset-button').addEventListener('click', () => {
      state.filters.freeSearch = '';
      state.filters.regime = new Set(URL_DEFAULTS.regime);
      state.filters.seveso = new Set(URL_DEFAULTS.seveso);
      state.filters.priority = 'all';
      state.filters.ied = 'all';
      state.filters.secteur = new Set();
      state.filters.commune = new Set();
      state.filters.epci = new Set();
      state.filters.structure = new Set();
      state.filters.monthEnabled = false;
      if (state.monthSteps.length) {
        state.filters.month = state.monthSteps[state.monthSteps.length - 1];
      }
      state.colorDim = 'regime';
      stopPlayback();
      if (loopCheckbox) loopCheckbox.checked = false;
      syncUiFromState();
      updateEpciOutlines();
      applyFilters();
    });

    // lazy communes: fetch from static file on first enable.
    // Uses an in-flight flag to avoid a race where rapid toggling while the
    // fetch is pending would double-add the features.
    let communesFetchStarted = false;
    map.on('overlayadd', async (e) => {
      if (e.layer === communesLayer && !communesFetchStarted) {
        communesFetchStarted = true;
        try {
          const data = await fetchJSON(GIRONDE_COMMUNES_URL);
          communesLayer.addData(data);
        } catch (err) {
          communesFetchStarted = false; // allow retry on next toggle
          console.error('communes load failed', err);
          showError('Impossible de charger les communes.');
        }
      }
    });

    // embed dialog
    wireEmbedDialog();

    // mobile sidebar toggle (FAB-driven slide-in)
    wireMobileSidebar();

    // recenter button — snap back to Gironde bounds
    const recenterBtn = document.getElementById('recenter-btn');
    if (recenterBtn) {
      recenterBtn.addEventListener('click', () => {
        if (girondeLayer && map.hasLayer(girondeLayer)) {
          map.fitBounds(girondeLayer.getBounds(), { padding: [20, 20] });
        } else {
          map.setView([44.85, -0.55], 9);
        }
      });
    }

    // legend toggle
    const legendToggle = document.getElementById('legend-toggle');
    const legendClose = document.getElementById('legend-close');
    const setLegendOpen = (open) => {
      legendEl.classList.toggle('is-hidden', !open);
      legendToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      try { localStorage.setItem('legend-open', open ? '1' : '0'); } catch (_) {}
    };
    const stored = (() => { try { return localStorage.getItem('legend-open'); } catch (_) { return null; } })();
    setLegendOpen(stored === null ? true : stored === '1');
    legendToggle.addEventListener('click', () => {
      const isHidden = legendEl.classList.contains('is-hidden');
      setLegendOpen(isHidden); // toggle open
    });
    legendClose.addEventListener('click', () => setLegendOpen(false));
  }

  // ---------- go ----------
  init().catch((err) => {
    showError('Erreur au chargement de la carte.');
    console.error(err);
  });

})();
