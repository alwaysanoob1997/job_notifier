(function () {
  "use strict";

  var THEME_KEY = "li-scraper-theme";

  function getStoredTheme() {
    try {
      return localStorage.getItem(THEME_KEY);
    } catch (_) {
      return null;
    }
  }

  function setStoredTheme(mode) {
    try {
      localStorage.setItem(THEME_KEY, mode);
    } catch (_) {}
  }

  function setDomTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    var btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.setAttribute("aria-label", mode === "dark" ? "Switch to light mode" : "Switch to dark mode");
      btn.textContent = mode === "dark" ? "Light mode" : "Dark mode";
    }
  }

  function resolveInitialTheme() {
    var stored = getStoredTheme();
    if (stored === "light" || stored === "dark") return stored;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  }

  function initThemeToggle() {
    setDomTheme(resolveInitialTheme());
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var cur = document.documentElement.getAttribute("data-theme") || "dark";
      var next = cur === "dark" ? "light" : "dark";
      setDomTheme(next);
      setStoredTheme(next);
    });
  }

  function cellSortValue(td) {
    var a = td.querySelector("a[href]");
    if (a) return (a.getAttribute("href") || "").trim();
    return (td.textContent || "").trim();
  }

  function cellTsvValue(td) {
    var a = td.querySelector("a[href]");
    var raw = a ? a.getAttribute("href") || "" : (td.textContent || "").trim();
    return raw.replace(/\r/g, " ").replace(/\n/g, " ").replace(/\t/g, " ");
  }

  function inferColumnType(rows, colIndex) {
    var numeric = 0;
    var total = 0;
    for (var i = 0; i < rows.length; i++) {
      var cells = rows[i].cells;
      if (colIndex >= cells.length) continue;
      var v = cellSortValue(cells[colIndex]);
      if (!v) continue;
      total++;
      if (/^-?\d+(\.\d+)?$/.test(v)) numeric++;
    }
    if (total > 0 && numeric === total) return "number";
    var dateOk = 0;
    var dateTotal = 0;
    for (var j = 0; j < rows.length; j++) {
      var c2 = rows[j].cells;
      if (colIndex >= c2.length) continue;
      var v2 = cellSortValue(c2[colIndex]);
      if (!v2) continue;
      dateTotal++;
      var t = Date.parse(v2);
      if (!isNaN(t)) dateOk++;
    }
    if (dateTotal > 0 && dateOk >= Math.ceil(dateTotal * 0.8)) return "date";
    return "string";
  }

  function compareValues(a, b, type, dir) {
    var cmp = 0;
    if (type === "number") {
      var na = parseFloat(a) || 0;
      var nb = parseFloat(b) || 0;
      cmp = na - nb;
    } else if (type === "date") {
      var da = Date.parse(a) || 0;
      var db = Date.parse(b) || 0;
      cmp = da - db;
    } else {
      cmp = a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
    }
    return dir === "desc" ? -cmp : cmp;
  }

  function enhanceTable(table) {
    if (table.dataset.enhanced === "1") return;
    table.dataset.enhanced = "1";

    var wrap = table.closest(".table-wrap");
    var thead = table.tHead;
    var tbody = table.tBodies[0];
    if (!thead || !tbody) return;

    var headers = thead.rows[0].cells;
    var sortState = { col: -1, dir: "asc" };

    function clearSortClasses() {
      for (var h = 0; h < headers.length; h++) {
        headers[h].classList.remove("sort-asc", "sort-desc");
        headers[h].removeAttribute("aria-sort");
      }
    }

    function sortBy(colIndex) {
      var rows = Array.prototype.slice.call(tbody.rows);
      if (rows.length === 0) return;
      var type = inferColumnType(rows, colIndex);
      if (sortState.col === colIndex) {
        sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
      } else {
        sortState.col = colIndex;
        sortState.dir = "asc";
      }
      clearSortClasses();
      var th = headers[colIndex];
      th.classList.add(sortState.dir === "asc" ? "sort-asc" : "sort-desc");
      th.setAttribute("aria-sort", sortState.dir === "asc" ? "ascending" : "descending");

      rows.sort(function (ra, rb) {
        var va = colIndex < ra.cells.length ? cellSortValue(ra.cells[colIndex]) : "";
        var vb = colIndex < rb.cells.length ? cellSortValue(rb.cells[colIndex]) : "";
        return compareValues(va, vb, type, sortState.dir);
      });
      var frag = document.createDocumentFragment();
      for (var r = 0; r < rows.length; r++) frag.appendChild(rows[r]);
      tbody.appendChild(frag);
    }

    for (var i = 0; i < headers.length; i++) {
      (function (colIndex) {
        var th = headers[colIndex];
        if (th.hasAttribute("data-no-sort")) return;
        th.classList.add("sortable-th");
        th.setAttribute("tabindex", "0");
        th.setAttribute("role", "columnheader");
        th.addEventListener("click", function () {
          sortBy(colIndex);
        });
        th.addEventListener("keydown", function (ev) {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            sortBy(colIndex);
          }
        });
      })(i);
    }

    var fname = (table.getAttribute("data-tsv-filename") || "export").replace(/[^\w\-]+/g, "_");
    var exportBtn = wrap && wrap.querySelector("[data-tsv-export]");
    if (exportBtn) {
      exportBtn.addEventListener("click", function () {
        var lines = [];
        var hr = thead.rows[0];
        var headCells = [];
        for (var hc = 0; hc < hr.cells.length; hc++) {
          var hcell = hr.cells[hc];
          if (hcell.hasAttribute("data-tsv-skip")) continue;
          headCells.push((hcell.textContent || "").trim().replace(/\t/g, " "));
        }
        lines.push(headCells.join("\t"));
        for (var ri = 0; ri < tbody.rows.length; ri++) {
          var row = tbody.rows[ri];
          var parts = [];
          for (var ci = 0; ci < row.cells.length; ci++) {
            var dcell = row.cells[ci];
            if (dcell.hasAttribute("data-tsv-skip")) continue;
            parts.push(cellTsvValue(dcell));
          }
          lines.push(parts.join("\t"));
        }
        var blob = new Blob([lines.join("\n")], { type: "text/tab-separated-values;charset=utf-8" });
        var url = URL.createObjectURL(blob);
        var a = document.createElement("a");
        a.href = url;
        a.download = fname + ".tsv";
        a.rel = "noopener";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      });
    }
  }

  function initDataTables() {
    var tables = document.querySelectorAll("table.data-table");
    for (var t = 0; t < tables.length; t++) enhanceTable(tables[t]);
  }

  function initNavDrawer() {
    var toggle = document.getElementById("nav-menu-toggle");
    var drawer = document.getElementById("nav-drawer");
    if (!toggle || !drawer) return;
    function setOpen(open) {
      drawer.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    setOpen(false);
    toggle.addEventListener("click", function (ev) {
      ev.stopPropagation();
      setOpen(drawer.hidden);
    });
    document.addEventListener("click", function () {
      setOpen(false);
    });
    drawer.addEventListener("click", function (ev) {
      ev.stopPropagation();
    });
  }

  function initJobMetaToggle() {
    var btn = document.getElementById("job-meta-toggle");
    var wrap = document.getElementById("run-jobs-table-wrap");
    if (!btn || !wrap) return;
    if (btn.dataset.metaToggleBound === "1") return;
    btn.dataset.metaToggleBound = "1";
    btn.addEventListener("click", function () {
      var open = !wrap.classList.contains("job-meta-expanded");
      wrap.classList.toggle("job-meta-expanded", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.textContent = open ? "Hide row meta" : "Show row meta";
    });
  }

  function initFilterSchedulePage() {
    var form = document.getElementById("filter-save-form");
    var wrap = document.getElementById("schedule-slots");
    var payload = document.getElementById("schedule-payload");
    var preset = document.getElementById("even-spacing-preset");
    var addBtn = document.getElementById("add-schedule-slot");
    var addScheduleBtn = document.getElementById("schedule-add-btn");
    var editBtn = document.getElementById("schedule-edit-btn");
    var editor = document.getElementById("schedule-editor");
    var saveKindInput = document.getElementById("save-kind-input");
    var tpl = document.getElementById("schedule-slot-template");
    if (!form || !wrap || !payload || !tpl) return;

    var maxSlots = 5;
    if (wrap.getAttribute("data-max-slots")) {
      var parsed = parseInt(wrap.getAttribute("data-max-slots"), 10);
      if (!isNaN(parsed) && parsed > 0) maxSlots = parsed;
    } else if (form.getAttribute("data-max-slots")) {
      var pf = parseInt(form.getAttribute("data-max-slots"), 10);
      if (!isNaN(pf) && pf > 0) maxSlots = pf;
    }

    function pad2(n) {
      var x = parseInt(n, 10);
      if (isNaN(x)) return "00";
      return (x < 10 ? "0" : "") + x;
    }

    function parseHHMM(s) {
      var t = (s || "").trim();
      if (!t) return null;
      var parts = t.split(":");
      if (parts.length < 2) return null;
      var h = parseInt(parts[0], 10);
      var m = parseInt(parts[1], 10);
      if (isNaN(h) || isNaN(m) || h < 0 || h > 23 || m < 0 || m > 59) return null;
      return { h: h, m: m };
    }

    function fillHourSelect(sel) {
      sel.innerHTML = "";
      var optBlank = document.createElement("option");
      optBlank.value = "";
      optBlank.textContent = "Hour";
      sel.appendChild(optBlank);
      for (var th = 0; th < 24; th++) {
        var o = document.createElement("option");
        o.value = pad2(th);
        o.textContent = pad2(th);
        sel.appendChild(o);
      }
    }

    function fillMinuteSelect(sel) {
      sel.innerHTML = "";
      var optBlank = document.createElement("option");
      optBlank.value = "";
      optBlank.textContent = "Min";
      sel.appendChild(optBlank);
      for (var tm = 0; tm < 60; tm++) {
        var o = document.createElement("option");
        o.value = pad2(tm);
        o.textContent = pad2(tm);
        sel.appendChild(o);
      }
    }

    function populateRowTime(row, hhmm) {
      var hSel = row.querySelector(".schedule-hour-select");
      var mSel = row.querySelector(".schedule-minute-select");
      if (!hSel || !mSel) return;
      fillHourSelect(hSel);
      fillMinuteSelect(mSel);
      var parsed = parseHHMM(hhmm);
      if (parsed) {
        hSel.value = pad2(parsed.h);
        mSel.value = pad2(parsed.m);
      } else {
        hSel.value = "";
        mSel.value = "";
      }
    }

    function rowTimeValue(row) {
      var hSel = row.querySelector(".schedule-hour-select");
      var mSel = row.querySelector(".schedule-minute-select");
      if (!hSel || !mSel) return "";
      var hv = (hSel.value || "").trim();
      var mv = (mSel.value || "").trim();
      if (!hv || !mv) return "";
      return hv + ":" + mv;
    }

    function readPresets() {
      var el = document.getElementById("even-presets-data");
      if (!el || !el.textContent.trim()) return null;
      try {
        return JSON.parse(el.textContent);
      } catch (_) {
        return null;
      }
    }

    function sortedEqual(a, b) {
      if (a.length !== b.length) return false;
      var sa = a.slice().sort();
      var sb = b.slice().sort();
      for (var j = 0; j < sa.length; j++) {
        if (sa[j] !== sb[j]) return false;
      }
      return true;
    }

    function editorIsOpen() {
      return editor && !editor.hidden;
    }

    function openEditor() {
      if (editor) editor.hidden = false;
      updateAddTimeButton();
    }

    function updateAddTimeButton() {
      var n = wrap.querySelectorAll(".schedule-slot-row").length;
      var nonePreset = preset && preset.value === "__none__";
      if (addBtn) {
        addBtn.hidden = !editorIsOpen() || n >= maxSlots || nonePreset;
      }
    }

    function updatePresetFromTimes() {
      if (!preset) return;
      var vals = [];
      wrap.querySelectorAll(".schedule-slot-row").forEach(function (row) {
        var v = rowTimeValue(row);
        if (v) vals.push(v);
      });
      if (vals.length === 0) {
        var rowCount = wrap.querySelectorAll(".schedule-slot-row").length;
        preset.value = rowCount > 0 ? "__custom__" : "__none__";
        return;
      }
      var presets = readPresets();
      if (!presets) {
        preset.value = "__custom__";
        return;
      }
      var matched = "__custom__";
      for (var k in presets) {
        if (!Object.prototype.hasOwnProperty.call(presets, k)) continue;
        var arr = presets[k];
        if (!arr || arr.length !== vals.length) continue;
        if (sortedEqual(arr, vals)) {
          matched = k;
          break;
        }
      }
      preset.value = matched;
    }

    function syncPayload() {
      var vals = [];
      wrap.querySelectorAll(".schedule-slot-row").forEach(function (row) {
        var v = rowTimeValue(row);
        if (v) vals.push(v);
      });
      payload.value = JSON.stringify(vals);
    }

    function bindRow(row) {
      var rm = row.querySelector(".schedule-slot-remove");
      if (rm) {
        rm.addEventListener("click", function () {
          row.remove();
          syncPayload();
          updatePresetFromTimes();
          updateAddTimeButton();
        });
      }
      function onTimePartChange() {
        syncPayload();
        updatePresetFromTimes();
        updateAddTimeButton();
      }
      var hSel = row.querySelector(".schedule-hour-select");
      var mSel = row.querySelector(".schedule-minute-select");
      if (hSel) hSel.addEventListener("change", onTimePartChange);
      if (mSel) mSel.addEventListener("change", onTimePartChange);
    }

    function appendRowFromTemplate(hhmm, skipPresetUpdate) {
      if (wrap.querySelectorAll(".schedule-slot-row").length >= maxSlots) return;
      var frag = tpl.content.cloneNode(true);
      var row = frag.querySelector(".schedule-slot-row");
      if (!row) return;
      wrap.appendChild(row);
      populateRowTime(row, hhmm || "");
      bindRow(row);
      syncPayload();
      if (!skipPresetUpdate) updatePresetFromTimes();
      updateAddTimeButton();
    }

    wrap.querySelectorAll(".schedule-slot-row").forEach(function (row) {
      var initial = (row.getAttribute("data-initial-time") || "").trim();
      populateRowTime(row, initial);
      bindRow(row);
    });

    if (addBtn) {
      addBtn.addEventListener("click", function () {
        openEditor();
        appendRowFromTemplate("");
        updateAddTimeButton();
      });
    }

    if (addScheduleBtn) {
      addScheduleBtn.addEventListener("click", function () {
        if (wrap.querySelectorAll(".schedule-slot-row").length >= maxSlots) return;
        openEditor();
        if (wrap.querySelectorAll(".schedule-slot-row").length === 0) {
          appendRowFromTemplate("09:00");
        }
        updatePresetFromTimes();
        updateAddTimeButton();
      });
    }

    if (editBtn) {
      editBtn.addEventListener("click", function () {
        openEditor();
      });
    }

    if (preset) {
      preset.addEventListener("change", function () {
        var n = preset.value;
        if (n === "__none__") {
          wrap.innerHTML = "";
          syncPayload();
          updatePresetFromTimes();
          updateAddTimeButton();
          return;
        }
        openEditor();
        if (n === "__custom__") {
          if (wrap.querySelectorAll(".schedule-slot-row").length === 0) {
            appendRowFromTemplate("");
          }
          syncPayload();
          preset.value = "__custom__";
          updatePresetFromTimes();
          updateAddTimeButton();
          return;
        }
        if (!n) return;
        var presets = readPresets();
        if (!presets || !presets[n]) return;
        var arr = presets[n];
        wrap.innerHTML = "";
        for (var i = 0; i < arr.length; i++) {
          appendRowFromTemplate(arr[i], true);
        }
        preset.value = n;
        syncPayload();
        updatePresetFromTimes();
        updateAddTimeButton();
      });
    }

    form.addEventListener("submit", function (ev) {
      syncPayload();
      var sub = ev.submitter;
      var isScheduleSave = sub && sub.id === "filter-save-schedule-btn";
      if (saveKindInput) {
        saveKindInput.value = isScheduleSave ? "schedule" : "search";
      }
    });
    syncPayload();
    updatePresetFromTimes();
    updateAddTimeButton();
  }

  function initSystemPromptSettingsCard() {
    var shell = document.getElementById("system-prompt-shell");
    var form = document.getElementById("system-prompt-editor");
    var textarea = document.getElementById("system-prompt-text");
    var cancel = document.getElementById("system-prompt-cancel");
    if (!shell || !form || !textarea) return;
    if (String(shell.tagName || "").toLowerCase() !== "details") return;

    var snapText = "";

    function restoreFromSnapshot() {
      textarea.value = snapText;
    }

    shell.addEventListener("toggle", function () {
      if (shell.open) {
        snapText = textarea.value;
        requestAnimationFrame(function () {
          textarea.focus();
        });
      } else {
        restoreFromSnapshot();
      }
    });

    function closeEditor() {
      shell.open = false;
    }

    if (cancel) {
      cancel.addEventListener("click", function () {
        restoreFromSnapshot();
        closeEditor();
      });
    }

    form.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        restoreFromSnapshot();
        closeEditor();
      }
    });
  }

  function initIdealJobRequirementsCard() {
    var shell = document.getElementById("ideal-job-shell");
    var form = document.getElementById("ideal-job-editor");
    var textarea = document.getElementById("ideal-job-description");
    var thrInput = document.getElementById("ideal-job-notify-threshold");
    var emailInput = document.getElementById("ideal-job-notify-email");
    var cancel = document.getElementById("ideal-job-cancel");
    if (!shell || !form || !textarea) return;
    if (String(shell.tagName || "").toLowerCase() !== "details") return;

    var snapText = "";
    var snapThr = "";
    var snapEmail = "";

    function restoreFromSnapshot() {
      textarea.value = snapText;
      if (thrInput) thrInput.value = snapThr;
      if (emailInput) emailInput.value = snapEmail;
    }

    shell.addEventListener("toggle", function () {
      if (shell.open) {
        snapText = textarea.value;
        snapThr = thrInput ? thrInput.value : "";
        snapEmail = emailInput ? emailInput.value : "";
        requestAnimationFrame(function () {
          textarea.focus();
        });
      } else {
        restoreFromSnapshot();
      }
    });

    function closeEditor() {
      shell.open = false;
    }

    if (cancel) {
      cancel.addEventListener("click", function () {
        restoreFromSnapshot();
        closeEditor();
      });
    }

    form.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        restoreFromSnapshot();
        closeEditor();
      }
    });
  }

  function fetchLlmStatus() {
    return fetch("/api/llm/status", { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function postLlmPreferences(body) {
    return fetch("/api/llm/preferences", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) {
          var d = data && data.detail;
          throw new Error(typeof d === "string" ? d : r.statusText);
        }
        return data;
      });
    });
  }

  function fetchProviderModels(providerId, refresh) {
    var url = "/api/llm/" + encodeURIComponent(providerId) + "/models" + (refresh ? "?refresh=1" : "");
    return fetch(url, { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function fillModelSelect(select, models, currentValue, emptyLabel) {
    var ids = models.map(function (m) {
      return typeof m === "string" ? m : m.id;
    });
    select.innerHTML = "";
    var ph = document.createElement("option");
    ph.value = "";
    ph.textContent = ids.length ? "Select a model…" : emptyLabel;
    select.appendChild(ph);
    for (var i = 0; i < models.length; i++) {
      var item = models[i];
      var id = typeof item === "string" ? item : item.id;
      var label = typeof item === "string" ? item : item.display_label || item.id;
      if (typeof item !== "string" && item.is_free === true && label.indexOf(":free") < 0) {
        label = label + " (free)";
      }
      var o = document.createElement("option");
      o.value = id;
      o.textContent = label;
      select.appendChild(o);
    }
    if (currentValue && ids.indexOf(currentValue) >= 0) {
      select.value = currentValue;
    } else if (currentValue) {
      var extra = document.createElement("option");
      extra.value = currentValue;
      extra.textContent = currentValue + " (current)";
      select.appendChild(extra);
      select.value = currentValue;
    }
  }

  // ---------------------------------------------------------------------------
  // Reusable model-filter component
  //
  // attachModelFilter(container, options) renders "Free only" and/or "Vendor"
  // controls into ``container`` based on ``options.supportedFilters``. The host
  // calls ``setModels(detailedModelList, currentValue)`` whenever fresh data
  // arrives; the component re-runs ``options.fill(filteredModels, currentValue)``
  // on every change and persists user choices in ``localStorage`` per provider.
  //
  // Adding a new provider that supports the same filters needs no UI changes:
  // just declare ``supported_filters`` in the Python provider and call
  // ``attachModelFilter`` from the panel's init code.
  // ---------------------------------------------------------------------------

  var FILTER_STORAGE_PREFIX = "linkedin-automation:llm:filters:";

  function loadFilterPrefs(providerId) {
    try {
      var raw = localStorage.getItem(FILTER_STORAGE_PREFIX + providerId);
      if (!raw) return {};
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  function saveFilterPrefs(providerId, prefs) {
    try {
      localStorage.setItem(FILTER_STORAGE_PREFIX + providerId, JSON.stringify(prefs));
    } catch (_) {
      // Ignored: localStorage may be disabled.
    }
  }

  function attachModelFilter(container, options) {
    if (!container) return null;
    container.innerHTML = "";
    var providerId = options.providerId;
    var supported = options.supportedFilters || [];
    var prefs = loadFilterPrefs(providerId);

    var state = {
      models: [],
      current: "",
      supportedFilters: [],
      vendors: [],
      freeOnly: prefs.freeOnly !== false, // default ON for providers that surface a "free" tag; harmless when not supported.
      vendor: typeof prefs.vendor === "string" ? prefs.vendor : "",
    };

    // Free-only checkbox
    var freeWrap = null;
    var freeInput = null;
    if (supported.indexOf("free") >= 0) {
      freeWrap = document.createElement("label");
      freeWrap.className = "llm-model-filter-checkbox";
      freeInput = document.createElement("input");
      freeInput.type = "checkbox";
      freeInput.checked = !!state.freeOnly;
      freeWrap.appendChild(freeInput);
      var freeText = document.createElement("span");
      freeText.textContent = " Free models only";
      freeWrap.appendChild(freeText);
      container.appendChild(freeWrap);
    } else {
      state.freeOnly = false;
    }

    // Vendor select
    var vendorWrap = null;
    var vendorSelect = null;
    if (supported.indexOf("vendor") >= 0) {
      vendorWrap = document.createElement("label");
      vendorWrap.className = "llm-model-filter-vendor";
      var vendorText = document.createElement("span");
      vendorText.textContent = "Vendor: ";
      vendorWrap.appendChild(vendorText);
      vendorSelect = document.createElement("select");
      vendorWrap.appendChild(vendorSelect);
      container.appendChild(vendorWrap);
    } else {
      state.vendor = "";
    }

    function persist() {
      saveFilterPrefs(providerId, {
        freeOnly: !!state.freeOnly,
        vendor: state.vendor || "",
      });
    }

    function applyFilters() {
      var filtered = state.models.filter(function (m) {
        if (state.freeOnly && supported.indexOf("free") >= 0) {
          if (m.is_free !== true) return false;
        }
        if (state.vendor && supported.indexOf("vendor") >= 0) {
          if ((m.vendor || "") !== state.vendor) return false;
        }
        return true;
      });
      options.fill(filtered, state.current);
    }

    function rebuildVendorOptions() {
      if (!vendorSelect) return;
      var pool = state.models;
      if (state.freeOnly && supported.indexOf("free") >= 0) {
        pool = pool.filter(function (m) { return m.is_free === true; });
      }
      var seen = {};
      var vendors = [];
      pool.forEach(function (m) {
        var v = m.vendor || "";
        if (v && !seen[v]) {
          seen[v] = true;
          vendors.push(v);
        }
      });
      vendors.sort(function (a, b) { return a.localeCompare(b); });

      var prevValue = state.vendor;
      vendorSelect.innerHTML = "";
      var allOpt = document.createElement("option");
      allOpt.value = "";
      allOpt.textContent = "All vendors";
      vendorSelect.appendChild(allOpt);
      vendors.forEach(function (v) {
        var o = document.createElement("option");
        o.value = v;
        o.textContent = v;
        vendorSelect.appendChild(o);
      });
      if (prevValue && vendors.indexOf(prevValue) >= 0) {
        vendorSelect.value = prevValue;
      } else {
        vendorSelect.value = "";
        state.vendor = "";
      }
    }

    if (freeInput) {
      freeInput.addEventListener("change", function () {
        state.freeOnly = !!freeInput.checked;
        persist();
        rebuildVendorOptions();
        applyFilters();
      });
    }

    if (vendorSelect) {
      vendorSelect.addEventListener("change", function () {
        state.vendor = vendorSelect.value || "";
        persist();
        applyFilters();
      });
    }

    return {
      setModels: function (models, currentValue, supportedFilters) {
        state.models = Array.isArray(models) ? models.slice() : [];
        state.current = currentValue || "";
        if (Array.isArray(supportedFilters)) {
          state.supportedFilters = supportedFilters;
        }
        rebuildVendorOptions();
        applyFilters();
      },
      getState: function () {
        return {
          freeOnly: state.freeOnly,
          vendor: state.vendor,
        };
      },
    };
  }

  function initLlmProviderCard() {
    var card = document.getElementById("llm-provider-card");
    if (!card) return;
    var providerSelect = document.getElementById("llm-provider-select");
    var statusEl = document.getElementById("llm-provider-status");
    var errorEl = document.getElementById("llm-provider-error");
    var panels = card.querySelectorAll(".llm-provider-panel");
    if (!providerSelect || !statusEl || !errorEl) return;

    var lmsModelSel = document.getElementById("llm-lmstudio-model");
    var lmsRefreshBtn = document.getElementById("llm-lmstudio-refresh");
    var lmsSaveBtn = document.getElementById("llm-lmstudio-save");
    var lmsUnavailableEl = document.getElementById("llm-lmstudio-unavailable");
    var lmsFilterContainer = card.querySelector('.llm-model-filters[data-provider="lmstudio"]');

    var gemModelSel = document.getElementById("llm-gemini-model");
    var gemRefreshBtn = document.getElementById("llm-gemini-refresh");
    var gemSaveBtn = document.getElementById("llm-gemini-save");
    var gemModelsErrorEl = document.getElementById("llm-gemini-models-error");
    var gemFilterContainer = card.querySelector('.llm-model-filters[data-provider="gemini"]');

    var customBaseInput = document.getElementById("llm-custom-base-url");
    var customModelInput = document.getElementById("llm-custom-model");
    var customSaveBtn = document.getElementById("llm-custom-save");

    var lastStatus = null;
    var lmsFilter = null;
    var gemFilter = null;

    function setError(msg) {
      if (msg) {
        errorEl.textContent = msg;
        errorEl.hidden = false;
      } else {
        errorEl.textContent = "";
        errorEl.hidden = true;
      }
    }

    function setStatus(msg) {
      statusEl.textContent = msg || "";
    }

    function showPanel(activePid) {
      panels.forEach(function (p) {
        p.hidden = p.getAttribute("data-provider") !== activePid;
      });
    }

    function applyStatus(st) {
      lastStatus = st;
      providerSelect.value = st.active_provider;
      showPanel(st.active_provider);

      var providers = st.providers || {};
      var lms = providers.lmstudio || {};
      var gem = providers.gemini || {};
      var custom = providers.custom || {};

      if (lmsModelSel) {
        if (lms.cli_available === false) {
          if (lmsUnavailableEl) {
            lmsUnavailableEl.textContent =
              "LM Studio CLI (lms) was not found. Install LM Studio from lmstudio.ai and ensure lms is on your PATH, or set APP_LMS_CLI.";
            lmsUnavailableEl.hidden = false;
          }
          renderLmStudioModels([], lms.model || "", lms.supported_filters || [], "(LM Studio CLI not found)");
          if (lmsSaveBtn) lmsSaveBtn.disabled = true;
        } else {
          if (lmsUnavailableEl) lmsUnavailableEl.hidden = true;
          if (lmsSaveBtn) lmsSaveBtn.disabled = false;
          if (lms.list_error) {
            setError("LM Studio: " + lms.list_error);
          }
          renderLmStudioModels(
            lms.models_detailed || [],
            lms.model || "",
            lms.supported_filters || [],
            "(no models downloaded)"
          );
        }
      }

      if (gemModelSel) {
        if (gemModelsErrorEl) {
          if (gem.list_error) {
            gemModelsErrorEl.textContent = "Could not load Gemini models: " + gem.list_error;
            gemModelsErrorEl.hidden = false;
          } else {
            gemModelsErrorEl.hidden = true;
          }
        }
        renderGeminiModels(
          gem.models_detailed || [],
          gem.model || "",
          gem.supported_filters || [],
          gem.api_key_set ? "(no models loaded — refresh)" : "(set the Gemini API key first)"
        );
      }

      if (customBaseInput && document.activeElement !== customBaseInput) {
        customBaseInput.value = custom.base_url || "";
      }
      if (customModelInput && document.activeElement !== customModelInput) {
        customModelInput.value = custom.model || "";
      }

      var activeBlock = providers[st.active_provider] || {};
      var configured = !!st.configured;
      if (configured) {
        setStatus(
          "Active: " +
            (activeBlock.display_name || st.active_provider) +
            (activeBlock.model ? " · " + activeBlock.model : "")
        );
      } else {
        setStatus(
          "Active: " +
            (activeBlock.display_name || st.active_provider) +
            " — not fully configured yet."
        );
      }
    }

    function ensureLmStudioFilter(supportedFilters) {
      if (!lmsModelSel || !lmsFilterContainer) return null;
      if (!lmsFilter) {
        lmsFilter = attachModelFilter(lmsFilterContainer, {
          providerId: "lmstudio",
          supportedFilters: supportedFilters || [],
          fill: function (filteredModels, currentValue) {
            fillModelSelect(lmsModelSel, filteredModels, currentValue, "(no models downloaded)");
          },
        });
      }
      return lmsFilter;
    }

    function ensureGeminiFilter(supportedFilters) {
      if (!gemModelSel || !gemFilterContainer) return null;
      if (!gemFilter) {
        gemFilter = attachModelFilter(gemFilterContainer, {
          providerId: "gemini",
          supportedFilters: supportedFilters || [],
          fill: function (filteredModels, currentValue) {
            fillModelSelect(gemModelSel, filteredModels, currentValue, "(no models match the filters)");
          },
        });
      }
      return gemFilter;
    }

    function renderLmStudioModels(detailed, currentValue, supportedFilters, emptyLabel) {
      if (!lmsModelSel) return;
      if (lmsFilterContainer && supportedFilters && supportedFilters.length) {
        var f = ensureLmStudioFilter(supportedFilters);
        if (f) {
          if (!detailed.length) {
            fillModelSelect(lmsModelSel, [], currentValue || "", emptyLabel);
            return;
          }
          f.setModels(detailed, currentValue || "", supportedFilters);
          return;
        }
      }
      fillModelSelect(lmsModelSel, detailed, currentValue || "", emptyLabel);
    }

    function renderGeminiModels(detailed, currentValue, supportedFilters, emptyLabel) {
      if (!gemModelSel) return;
      if (gemFilterContainer && supportedFilters && supportedFilters.length) {
        var f = ensureGeminiFilter(supportedFilters);
        if (f) {
          if (!detailed.length) {
            fillModelSelect(gemModelSel, [], currentValue || "", emptyLabel);
            return;
          }
          f.setModels(detailed, currentValue || "", supportedFilters);
          return;
        }
      }
      fillModelSelect(gemModelSel, detailed, currentValue || "", emptyLabel);
    }

    function load() {
      setError("");
      fetchLlmStatus()
        .then(applyStatus)
        .catch(function (e) {
          setError(e.message || String(e));
        });
    }

    providerSelect.addEventListener("change", function () {
      showPanel(providerSelect.value);
    });

    if (lmsRefreshBtn) {
      lmsRefreshBtn.addEventListener("click", function () {
        lmsRefreshBtn.disabled = true;
        load();
        setTimeout(function () {
          lmsRefreshBtn.disabled = false;
        }, 400);
      });
    }

    if (lmsSaveBtn) {
      lmsSaveBtn.addEventListener("click", function () {
        var v = (lmsModelSel && lmsModelSel.value || "").trim();
        if (!v) {
          setError("Select an LM Studio model first.");
          return;
        }
        lmsSaveBtn.disabled = true;
        setError("");
        postLlmPreferences({ provider: "lmstudio", lmstudio: { model: v } })
          .then(load)
          .catch(function (err) {
            setError(err.message || String(err));
          })
          .finally(function () {
            lmsSaveBtn.disabled = false;
          });
      });
    }

    function refreshGeminiModels(force) {
      if (gemRefreshBtn) gemRefreshBtn.disabled = true;
      fetchProviderModels("gemini", force ? 1 : 0)
        .then(function (data) {
          var models = data.models || [];
          var current = (lastStatus && lastStatus.providers && lastStatus.providers.gemini && lastStatus.providers.gemini.model) || "";
          var supported = data.supported_filters || [];
          renderGeminiModels(models, current, supported, "(no models loaded — refresh)");
          if (gemModelsErrorEl) {
            if (data.list_error) {
              gemModelsErrorEl.textContent = "Could not load Gemini models: " + data.list_error;
              gemModelsErrorEl.hidden = false;
            } else {
              gemModelsErrorEl.hidden = true;
            }
          }
        })
        .catch(function (err) {
          setError(err.message || String(err));
        })
        .finally(function () {
          if (gemRefreshBtn) gemRefreshBtn.disabled = false;
        });
    }

    if (gemRefreshBtn) {
      gemRefreshBtn.addEventListener("click", function () {
        refreshGeminiModels(true);
      });
    }

    if (gemSaveBtn) {
      gemSaveBtn.addEventListener("click", function () {
        var v = (gemModelSel && gemModelSel.value || "").trim();
        if (!v) {
          setError("Pick or refresh a Gemini model first.");
          return;
        }
        gemSaveBtn.disabled = true;
        setError("");
        postLlmPreferences({ provider: "gemini", gemini: { model: v } })
          .then(load)
          .catch(function (err) {
            setError(err.message || String(err));
          })
          .finally(function () {
            gemSaveBtn.disabled = false;
          });
      });
    }

    if (customSaveBtn) {
      customSaveBtn.addEventListener("click", function () {
        var base = (customBaseInput && customBaseInput.value || "").trim();
        var model = (customModelInput && customModelInput.value || "").trim();
        if (!base || !model) {
          setError("Both base URL and model id are required.");
          return;
        }
        customSaveBtn.disabled = true;
        setError("");
        postLlmPreferences({
          provider: "custom",
          custom: { base_url: base, model: model },
        })
          .then(load)
          .catch(function (err) {
            setError(err.message || String(err));
          })
          .finally(function () {
            customSaveBtn.disabled = false;
          });
      });
    }

    load();
  }

  function rebindAfterHtmxSwap() {
    // The run detail page swaps the jobs table wrapper via htmx every 2s while
    // the run is fetching/scoring. After each swap the toolbar buttons and
    // table are fresh DOM nodes that need their listeners re-attached.
    initJobMetaToggle();
    initDataTables();
  }

  function boot() {
    initThemeToggle();
    initNavDrawer();
    initJobMetaToggle();
    initIdealJobRequirementsCard();
    initSystemPromptSettingsCard();
    initFilterSchedulePage();
    initDataTables();
    initLlmProviderCard();
    document.body.addEventListener("htmx:afterSettle", rebindAfterHtmxSwap);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
