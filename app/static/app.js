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
    var addScheduleBtn = document.getElementById("add-schedule-btn");
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

    function updateScheduleActionButtons() {
      var n = wrap.querySelectorAll(".schedule-slot-row").length;
      var nonePreset = preset && preset.value === "__none__";
      if (addScheduleBtn) {
        addScheduleBtn.hidden = editorIsOpen() || n > 0;
      }
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
          updateScheduleActionButtons();
        });
      }
      function onTimePartChange() {
        syncPayload();
        updatePresetFromTimes();
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
    }

    wrap.querySelectorAll(".schedule-slot-row").forEach(function (row) {
      var initial = (row.getAttribute("data-initial-time") || "").trim();
      populateRowTime(row, initial);
      bindRow(row);
    });

    if (addBtn) {
      addBtn.addEventListener("click", function () {
        if (editor) editor.hidden = false;
        appendRowFromTemplate("");
        updateScheduleActionButtons();
      });
    }

    if (addScheduleBtn) {
      addScheduleBtn.addEventListener("click", function () {
        if (wrap.querySelectorAll(".schedule-slot-row").length >= maxSlots) return;
        if (editor) editor.hidden = false;
        if (wrap.querySelectorAll(".schedule-slot-row").length === 0) {
          appendRowFromTemplate("09:00");
        }
        updateScheduleActionButtons();
      });
    }

    if (preset) {
      preset.addEventListener("change", function () {
        var n = preset.value;
        if (n === "__none__") {
          wrap.innerHTML = "";
          syncPayload();
          updatePresetFromTimes();
          updateScheduleActionButtons();
          return;
        }
        if (editor) editor.hidden = false;
        if (n === "__custom__") {
          if (wrap.querySelectorAll(".schedule-slot-row").length === 0) {
            appendRowFromTemplate("");
          }
          syncPayload();
          preset.value = "__custom__";
          updatePresetFromTimes();
          updateScheduleActionButtons();
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
        updateScheduleActionButtons();
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
    updateScheduleActionButtons();
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

  function boot() {
    initThemeToggle();
    initNavDrawer();
    initJobMetaToggle();
    initIdealJobRequirementsCard();
    initFilterSchedulePage();
    initDataTables();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
