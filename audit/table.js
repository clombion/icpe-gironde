/* ============================================================================
   audit/table.js — Decision table view for the coordinate audit.

   Standalone page that loads flagged.json + review files from GitHub,
   merges them into a filterable/sortable table, and supports CSV export.
   No polling, no BroadcastChannel, no localStorage lock — one-shot fetch.
============================================================================ */

(function () {
  'use strict';

  var Lib = window.AuditLib;
  var escapeHTML = window.IcpeUtil.escapeHTML;
  var safeNumber = window.IcpeUtil.safeNumber;

  if (!Lib) {
    console.error('AuditLib not loaded — check that lib.js is included before table.js');
    return;
  }
  if (!escapeHTML || !safeNumber) {
    console.error('IcpeUtil not loaded — check that shared/util.js is included before table.js');
    return;
  }

  // ---- Constants --------------------------------------------------------

  var GH_REPO = 'bononlouis-del/Les-ICPE-en-r-serve-naturelle-nationale';
  var REVIEWS_PATH = 'données-georisques/audit/coordonnees-audit-reviews';
  var FLAGGED_JSON_URL = '../données-georisques/audit/coordonnees-audit-flagged.json';

  // Verdict → human label
  var VERDICT_LABELS = {
    garder_stored: 'garder',
    utiliser_geocoded: 'géocodé',
    placer_manuellement: 'manuel',
    terrain: 'terrain',
    non_revu: 'non revu',
  };

  // Verdict → CSS class for chip coloring
  var VERDICT_CHIP_CLASS = {
    garder_stored: 'verdict-chip--moss',
    utiliser_geocoded: 'verdict-chip--azur',
    placer_manuellement: 'verdict-chip--ochre',
    terrain: 'verdict-chip--rust',
    non_revu: 'verdict-chip--lead',
  };

  // ---- State ------------------------------------------------------------

  var rows = [];          // merged data: array of row objects
  var filteredRows = [];  // after filter + sort
  var sortCol = null;
  var sortAsc = true;

  // ---- DOM refs ---------------------------------------------------------

  var filterVerdict = document.getElementById('filter-verdict');
  var filterGroup = document.getElementById('filter-group');
  var filterReviewer = document.getElementById('filter-reviewer');
  var filterSearch = document.getElementById('filter-search');
  var btnExport = document.getElementById('btn-export-csv');
  var tbody = document.getElementById('decisions-tbody');
  var emptyMsg = document.getElementById('table-empty');
  var noMatchMsg = document.getElementById('table-no-match');

  // ---- Diacritics-insensitive search helper -----------------------------

  function normalize(s) {
    if (!s) return '';
    return String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  // ---- Load data --------------------------------------------------------

  async function loadAll() {
    var flagged;
    try {
      var res = await fetch(FLAGGED_JSON_URL);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      flagged = await res.json();
    } catch (err) {
      console.error('table: failed to load flagged.json', err);
      emptyMsg.hidden = false;
      return;
    }

    // Build id_icpe → item index + navigation index
    var itemById = {};    // id_icpe → flagged item
    var navIndex = {};    // id_icpe → {group_id, bucket_index, item_index}
    var bucketSize = flagged.bucket_size || 25;

    for (var gi = 0; gi < (flagged.groups || []).length; gi++) {
      var group = flagged.groups[gi];
      var items = group.items || [];
      for (var ii = 0; ii < items.length; ii++) {
        var item = items[ii];
        itemById[item.id_icpe] = item;
        var bucketIndex = Math.floor(ii / bucketSize) + 1;
        var itemIndex = ii % bucketSize;
        navIndex[item.id_icpe] = {
          group_id: group.id,
          bucket_index: bucketIndex,
          item_index: itemIndex,
        };
      }
    }

    // Fetch review files from GitHub (one-shot)
    var decisionById = {};   // id_icpe → decision object
    var reviewerById = {};   // id_icpe → reviewer string

    try {
      var url = 'https://api.github.com/repos/' + GH_REPO + '/contents/' + encodeURI(REVIEWS_PATH);
      var listRes = await fetch(url, {
        headers: { Accept: 'application/vnd.github.v3+json' },
      });

      if (listRes.ok) {
        var listing = await listRes.json();
        if (Array.isArray(listing)) {
          var reviewFiles = listing.filter(function (entry) {
            return entry.type === 'file' && /\.json$/.test(entry.name);
          });

          await Promise.all(reviewFiles.map(async function (entry) {
            var parsed = Lib.parseBucketFilename(entry.name);
            if (!parsed) return;
            try {
              var fileRes = await fetch(entry.download_url);
              if (!fileRes.ok) return;
              var fileJson = await fileRes.json();
              var validation = Lib.validateReviewFile(fileJson, flagged, parsed.group, parsed.index);
              if (validation.status === 'valid' || validation.status === 'stale') {
                var reviewer = validation.reviewer || '';
                for (var di = 0; di < validation.decisions.length; di++) {
                  var dec = validation.decisions[di];
                  decisionById[dec.id_icpe] = dec;
                  reviewerById[dec.id_icpe] = reviewer;
                }
              }
            } catch (err) {
              console.warn('table: failed to fetch', entry.name, err);
            }
          }));
        }
      } else if (listRes.status !== 404) {
        console.warn('table: GitHub API returned', listRes.status);
      }
      // 404 = no reviews directory yet, which is fine
    } catch (err) {
      console.warn('table: GitHub API unreachable', err);
    }

    // Merge flagged items with decisions
    var allIds = Object.keys(itemById);
    var reviewerSet = new Set();

    for (var ri = 0; ri < allIds.length; ri++) {
      var id = allIds[ri];
      var it = itemById[id];
      var dec = decisionById[id];
      var reviewer = reviewerById[id] || '';
      var verdict = dec ? dec.verdict : 'non_revu';
      var note = dec ? (dec.note || '') : '';
      var pertinent = dec ? !!dec.pertinent_enquete : false;
      var nav = navIndex[id];
      var distance = it.forward_distance_m != null ? Math.round(Number(it.forward_distance_m)) : null;

      if (reviewer) reviewerSet.add(reviewer);

      rows.push({
        id_icpe: it.id_icpe || '',
        nom_complet: it.nom_complet || '',
        commune: it.commune || '',
        audit_class: it.audit_class || '',
        verdict: verdict,
        reviewer: reviewer,
        distance: distance,
        note: note,
        pertinent: pertinent,
        group_id: nav ? nav.group_id : '',
        bucket_index: nav ? nav.bucket_index : 0,
        item_index: nav ? nav.item_index : 0,
        // pre-computed normalized strings for search
        _search: normalize(it.id_icpe) + ' ' + normalize(it.nom_complet) + ' ' + normalize(it.commune),
      });
    }

    // Populate reviewer dropdown
    var reviewers = Array.from(reviewerSet).sort();
    for (var rvi = 0; rvi < reviewers.length; rvi++) {
      var opt = document.createElement('option');
      opt.value = reviewers[rvi];
      opt.textContent = reviewers[rvi];
      filterReviewer.appendChild(opt);
    }

    // Check if any reviews exist at all
    var hasReviews = Object.keys(decisionById).length > 0;
    if (!hasReviews) {
      emptyMsg.hidden = false;
    }

    applyFiltersAndSort();
  }

  // ---- Filter + Sort ----------------------------------------------------

  function applyFiltersAndSort() {
    var vFilter = filterVerdict.value;
    var gFilter = filterGroup.value;
    var rFilter = filterReviewer.value;
    var sFilter = normalize(filterSearch.value);

    filteredRows = rows.filter(function (row) {
      if (vFilter && row.verdict !== vFilter) return false;
      if (gFilter && row.group_id !== gFilter) return false;
      if (rFilter && row.reviewer !== rFilter) return false;
      if (sFilter && row._search.indexOf(sFilter) === -1) return false;
      return true;
    });

    if (sortCol) {
      filteredRows.sort(function (a, b) {
        var va = a[sortCol];
        var vb = b[sortCol];
        // Null/undefined sort last
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        if (typeof va === 'number' && typeof vb === 'number') {
          return sortAsc ? va - vb : vb - va;
        }
        var sa = String(va).toLowerCase();
        var sb = String(vb).toLowerCase();
        if (sa < sb) return sortAsc ? -1 : 1;
        if (sa > sb) return sortAsc ? 1 : -1;
        return 0;
      });
    }

    renderTable();
  }

  // ---- Render -----------------------------------------------------------

  function renderTable() {
    // Hide/show empty states
    var hasAnyRows = rows.length > 0;
    var hasFilteredRows = filteredRows.length > 0;

    emptyMsg.hidden = hasAnyRows;
    noMatchMsg.hidden = !hasAnyRows || hasFilteredRows;

    if (!hasFilteredRows) {
      tbody.innerHTML = '';
      return;
    }

    var html = [];
    for (var i = 0; i < filteredRows.length; i++) {
      var row = filteredRows[i];
      var chipClass = VERDICT_CHIP_CLASS[row.verdict] || 'verdict-chip--lead';
      var verdictLabel = VERDICT_LABELS[row.verdict] || row.verdict;
      var distStr = row.distance != null ? safeNumber(row.distance, '') : '';

      html.push(
        '<tr class="decisions-table__row" data-idx="' + i + '">' +
          '<td>' + escapeHTML(row.id_icpe) + '</td>' +
          '<td>' + escapeHTML(row.nom_complet) + '</td>' +
          '<td>' + escapeHTML(row.commune) + '</td>' +
          '<td><code>' + escapeHTML(row.audit_class) + '</code></td>' +
          '<td><span class="verdict-chip ' + chipClass + '">' + escapeHTML(verdictLabel) + '</span></td>' +
          '<td>' + escapeHTML(row.reviewer) + '</td>' +
          '<td class="td-num">' + escapeHTML(distStr) + '</td>' +
          '<td>' + escapeHTML(row.note) + '</td>' +
          '<td>' + (row.pertinent ? 'oui' : '') + '</td>' +
        '</tr>'
      );
    }
    tbody.innerHTML = html.join('');
  }

  // ---- Row click → deep-link back to review tool ------------------------

  tbody.addEventListener('click', function (e) {
    var tr = e.target.closest('tr');
    if (!tr) return;
    var idx = parseInt(tr.getAttribute('data-idx'), 10);
    if (isNaN(idx) || !filteredRows[idx]) return;
    var row = filteredRows[idx];
    var paddedBucket = String(row.bucket_index).padStart(2, '0');
    var itemNum = row.item_index + 1;
    window.location.href = './#' + row.group_id + '-' + paddedBucket + '/' + itemNum;
  });

  // ---- Column sort ------------------------------------------------------

  document.querySelector('.decisions-table thead').addEventListener('click', function (e) {
    var th = e.target.closest('th');
    if (!th) return;
    var col = th.getAttribute('data-col');
    if (!col) return;
    if (sortCol === col) {
      sortAsc = !sortAsc;
    } else {
      sortCol = col;
      sortAsc = true;
    }
    // Update header indicators
    var ths = document.querySelectorAll('.decisions-table th');
    for (var i = 0; i < ths.length; i++) {
      ths[i].classList.remove('sort-asc', 'sort-desc');
    }
    th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
    applyFiltersAndSort();
  });

  // ---- Filter event listeners -------------------------------------------

  filterVerdict.addEventListener('change', applyFiltersAndSort);
  filterGroup.addEventListener('change', applyFiltersAndSort);
  filterReviewer.addEventListener('change', applyFiltersAndSort);
  filterSearch.addEventListener('input', applyFiltersAndSort);

  // ---- CSV export -------------------------------------------------------

  btnExport.addEventListener('click', function () {
    if (!filteredRows.length) return;

    var csvCols = ['id_icpe', 'nom_complet', 'commune', 'audit_class', 'verdict', 'reviewer', 'distance', 'note', 'pertinent'];
    var lines = [csvCols.join(',')];

    for (var i = 0; i < filteredRows.length; i++) {
      var row = filteredRows[i];
      var vals = [
        csvEscape(row.id_icpe),
        csvEscape(row.nom_complet),
        csvEscape(row.commune),
        csvEscape(row.audit_class),
        csvEscape(VERDICT_LABELS[row.verdict] || row.verdict),
        csvEscape(row.reviewer),
        row.distance != null ? String(row.distance) : '',
        csvEscape(row.note),
        row.pertinent ? 'oui' : '',
      ];
      lines.push(vals.join(','));
    }

    var blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'decisions-audit-icpe.csv';
    a.click();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  });

  function csvEscape(val) {
    if (val == null) return '';
    var s = String(val);
    if (s.indexOf(',') !== -1 || s.indexOf('"') !== -1 || s.indexOf('\n') !== -1) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  // ---- Init -------------------------------------------------------------

  loadAll();
})();
