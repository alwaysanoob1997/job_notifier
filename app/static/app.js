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

  function fetchLmstudioStatus() {
    return fetch("/api/lmstudio/status", { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function postLmstudioPreferred(modelId) {
    return fetch("/api/lmstudio/preferences", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferred_model_id: modelId }),
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

  function fillLmstudioModelSelect(select, models, preferredId) {
    select.innerHTML = "";
    var ph = document.createElement("option");
    ph.value = "";
    ph.textContent = models.length ? "Select a model…" : "(no models downloaded)";
    select.appendChild(ph);
    for (var i = 0; i < models.length; i++) {
      var o = document.createElement("option");
      o.value = models[i];
      o.textContent = models[i];
      select.appendChild(o);
    }
    if (preferredId && models.indexOf(preferredId) >= 0) {
      select.value = preferredId;
    }
  }

  function initLmStudioSetupWizard() {
    var dlg = document.getElementById("lmstudio-setup-dialog");
    var titleEl = document.getElementById("lmstudio-setup-title");
    var bodyEl = document.getElementById("lmstudio-setup-body");
    var actionsEl = document.getElementById("lmstudio-setup-actions");
    if (!dlg || !titleEl || !bodyEl || !actionsEl) return;

    function clear(el) {
      while (el.firstChild) el.removeChild(el.firstChild);
    }

    function addClose() {
      var closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "btn-secondary";
      closeBtn.textContent = "Close";
      closeBtn.addEventListener("click", function () {
        dlg.close();
      });
      actionsEl.appendChild(closeBtn);
    }

    function showPicker(models, preferredId, staleNote) {
      titleEl.textContent = "Choose a preferred model";
      clear(bodyEl);
      clear(actionsEl);
      if (staleNote) {
        var warn = document.createElement("p");
        warn.className = "error-inline";
        warn.textContent = staleNote;
        bodyEl.appendChild(warn);
      }
      var lbl = document.createElement("label");
      lbl.textContent = "Model";
      var sel = document.createElement("select");
      sel.id = "lmstudio-wizard-select";
      sel.setAttribute("aria-label", "Preferred LM Studio model");
      fillLmstudioModelSelect(sel, models, preferredId);
      lbl.appendChild(sel);
      bodyEl.appendChild(lbl);

      var saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "btn-primary";
      saveBtn.textContent = "Save";
      saveBtn.addEventListener("click", function () {
        var v = (sel.value || "").trim();
        if (!v) return;
        saveBtn.disabled = true;
        postLmstudioPreferred(v)
          .then(function () {
            dlg.close();
          })
          .catch(function (err) {
            alert(err.message || String(err));
          })
          .finally(function () {
            saveBtn.disabled = false;
          });
      });
      actionsEl.appendChild(saveBtn);
      addClose();
      dlg.showModal();
    }

    fetchLmstudioStatus()
      .then(function (st) {
        if (st.env_overrides_model) return;
        if (!st.cli_available) {
          titleEl.textContent = "Install LM Studio";
          clear(bodyEl);
          clear(actionsEl);
          var p1 = document.createElement("p");
          p1.textContent =
            "Job match scoring requires LM Studio. Install it, add the CLI to your PATH (or set LINKEDIN_LMS_CLI), then download a preferred model.";
          bodyEl.appendChild(p1);
          var pLink = document.createElement("p");
          var a = document.createElement("a");
          a.href = "https://lmstudio.ai/";
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.className = "btn-primary";
          a.textContent = "Open lmstudio.ai";
          pLink.appendChild(a);
          bodyEl.appendChild(pLink);
          var p2 = document.createElement("p");
          p2.className = "muted";
          p2.textContent =
            "In LM Studio, open Search / My Models and download a model (for example Gemma or Llama). Reload this page after installation.";
          bodyEl.appendChild(p2);
          addClose();
          dlg.showModal();
          return;
        }
        if (st.list_error) {
          titleEl.textContent = "LM Studio";
          clear(bodyEl);
          clear(actionsEl);
          var pe = document.createElement("p");
          pe.textContent = "Could not list downloaded models:";
          bodyEl.appendChild(pe);
          var pre = document.createElement("pre");
          pre.className = "lmstudio-cli-error";
          pre.textContent = st.list_error;
          bodyEl.appendChild(pre);
          var ph = document.createElement("p");
          ph.className = "muted";
          ph.textContent = "Fix your lms CLI or PATH, then reload.";
          bodyEl.appendChild(ph);
          addClose();
          dlg.showModal();
          return;
        }
        var models = st.models || [];
        if (models.length === 0) {
          titleEl.textContent = "Download a model";
          clear(bodyEl);
          clear(actionsEl);
          var p0 = document.createElement("p");
          p0.textContent =
            "LM Studio is installed, but no local models were found. Open LM Studio and download at least one model.";
          bodyEl.appendChild(p0);
          var pOpen = document.createElement("p");
          var a2 = document.createElement("a");
          a2.href = "https://lmstudio.ai/";
          a2.target = "_blank";
          a2.rel = "noopener noreferrer";
          a2.className = "btn-primary";
          a2.textContent = "Open lmstudio.ai";
          pOpen.appendChild(a2);
          bodyEl.appendChild(pOpen);
          addClose();
          dlg.showModal();
          return;
        }
        var pref = st.preferred_model_id || "";
        if (pref && models.indexOf(pref) >= 0) return;
        var note =
          pref && models.indexOf(pref) < 0
            ? "Your saved model is no longer in the downloaded list. Pick a new one."
            : "";
        showPicker(models, pref, note);
      })
      .catch(function () {
        /* ignore wizard errors on pages that don't need scoring */
      });
  }

  function initLmStudioSettingsPage() {
    var card = document.getElementById("lmstudio-settings-card");
    if (!card) return;
    var envEl = document.getElementById("lmstudio-settings-env");
    var errEl = document.getElementById("lmstudio-settings-error");
    var formWrap = document.getElementById("lmstudio-settings-form-wrap");
    var unavailableEl = document.getElementById("lmstudio-settings-unavailable");
    var select = document.getElementById("lmstudio-preferred-select");
    var refreshBtn = document.getElementById("lmstudio-refresh-models");
    var saveBtn = document.getElementById("lmstudio-save-preferred");
    if (!select || !refreshBtn || !saveBtn || !formWrap || !unavailableEl) return;

    function setError(msg) {
      if (!errEl) return;
      if (msg) {
        errEl.textContent = msg;
        errEl.hidden = false;
      } else {
        errEl.textContent = "";
        errEl.hidden = true;
      }
    }

    function applyStatus(st) {
      setError("");
      saveBtn.disabled = false;
      saveBtn.title = "";
      if (st.env_overrides_model) {
        if (envEl) {
          envEl.textContent =
            "LINKEDIN_LMSTUDIO_MODEL is set in the environment; the saved preference below is ignored until you unset it.";
          envEl.hidden = false;
        }
        formWrap.hidden = false;
        unavailableEl.hidden = true;
        saveBtn.disabled = true;
        saveBtn.title = "Unset LINKEDIN_LMSTUDIO_MODEL to save a preference to the config file.";
      } else {
        if (envEl) envEl.hidden = true;
      }

      if (!st.cli_available) {
        formWrap.hidden = true;
        unavailableEl.hidden = false;
        unavailableEl.textContent =
          "LM Studio CLI (lms) was not found. Install LM Studio from lmstudio.ai and ensure lms is on your PATH, or set LINKEDIN_LMS_CLI.";
        return;
      }
      unavailableEl.hidden = true;
      formWrap.hidden = false;

      if (st.list_error) {
        setError(st.list_error);
        fillLmstudioModelSelect(select, [], null);
        return;
      }
      var models = st.models || [];
      fillLmstudioModelSelect(select, models, st.preferred_model_id || "");
    }

    function load() {
      fetchLmstudioStatus()
        .then(applyStatus)
        .catch(function (e) {
          setError(e.message || String(e));
        });
    }

    refreshBtn.addEventListener("click", function () {
      refreshBtn.disabled = true;
      load();
      setTimeout(function () {
        refreshBtn.disabled = false;
      }, 400);
    });

    saveBtn.addEventListener("click", function () {
      var v = (select.value || "").trim();
      if (!v) {
        setError("Select a model first.");
        return;
      }
      saveBtn.disabled = true;
      setError("");
      postLmstudioPreferred(v)
        .then(function () {
          load();
        })
        .catch(function (err) {
          setError(err.message || String(err));
        })
        .finally(function () {
          saveBtn.disabled = false;
        });
    });

    load();
  }

  function boot() {
    initThemeToggle();
    initNavDrawer();
    initJobMetaToggle();
    initIdealJobRequirementsCard();
    initSystemPromptSettingsCard();
    initFilterSchedulePage();
    initDataTables();
    initLmStudioSetupWizard();
    initLmStudioSettingsPage();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
