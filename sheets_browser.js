/**
 * Browser-side Google Sheets adapter for static GitHub Pages hosting.
 * Primary mode: Google Apps Script Web App URL (zero Google Cloud Console / OAuth setup required).
 * Optional fallback mode: Google Identity Services (OAuth Web Client ID) + Sheets REST API v4.
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "twm_browser_config_v2";
  const DEFAULT_TABS = { tasks: "Tasks", worklog: "Work log", data: "Data" };
  const FALLBACK_GIDS = { tasks: 697912632, worklog: 1436624543, data: 1258735725 };
  const TASKS_START_ROW = 9;
  const WORKLOG_START_ROW = 10;
  const SCOPES = "https://www.googleapis.com/auth/spreadsheets";

  let tokenClient = null;
  let accessToken = "";
  let tokenExpiresAt = 0;

  function loadBrowserConfig() {
    const base = {
      auth_mode: "gas",
      gas_url: "",
      gas_pin: "",
      client_id: "",
      spreadsheet_id: "",
      sheet_url: "",
      tasks_tab: DEFAULT_TABS.tasks,
      worklog_tab: DEFAULT_TABS.worklog,
      data_tab: DEFAULT_TABS.data,
      saved_sheets: [],
    };
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          Object.assign(base, parsed);
        }
      }
    } catch (_) {}

    const qs = new URLSearchParams(window.location.search);
    const qGas = (qs.get("gas_url") || "").trim();
    const qClient = (qs.get("client_id") || "").trim();
    const qSheet = (qs.get("sheet") || "").trim();
    if (qGas) {
      base.auth_mode = "gas";
      base.gas_url = qGas;
    } else if (qClient && !base.gas_url) {
      base.auth_mode = "oauth";
      base.client_id = qClient;
    }
    if (qSheet) {
      base.sheet_url = qSheet;
      try {
        base.spreadsheet_id = parseSpreadsheetId(qSheet);
      } catch (_) {}
    }
    return base;
  }

  function saveBrowserConfig(cfg) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
  }

  function resetBrowserConfig() {
    localStorage.removeItem(STORAGE_KEY);
    accessToken = "";
    tokenExpiresAt = 0;
  }

  function upsertSavedSheet(cfg, entry) {
    const key = entry.spreadsheet_id || entry.gas_url;
    const saved = (cfg.saved_sheets || []).filter(s => (s.spreadsheet_id || s.gas_url) !== key);
    saved.unshift(entry);
    cfg.saved_sheets = saved.slice(0, 20);
  }

  function parseSpreadsheetId(linkOrId) {
    const s = String(linkOrId || "").trim();
    if (!s) throw new Error("Google Sheet link or Spreadsheet ID is required.");
    const m = s.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
    if (m) return m[1];
    if (/^[a-zA-Z0-9-_]{20,}$/.test(s)) return s;
    throw new Error("Invalid Google Sheet link or Spreadsheet ID. Paste the full Google Sheets URL or its ID.");
  }

  function validateGasUrl(url) {
    const u = String(url || "").trim();
    if (!u) throw new Error("Apps Script Web App URL is required.");
    if (!/^https:\/\/script\.google\.com\/macros\/s\/.+\/exec(\?.*)?$/i.test(u)) {
      throw new Error("Invalid Apps Script URL. It should start with https://script.google.com/macros/s/... and end with /exec.");
    }
    return u;
  }

  function quoteSheet(title) {
    return `'${String(title).replace(/'/g, "''")}'`;
  }

  function cell(row, idx) {
    return row && idx < row.length && row[idx] != null ? String(row[idx]).trim() : "";
  }

  function parseDateValue(raw) {
    const s = String(raw || "").trim();
    if (!s) return null;
    let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
    if (m) {
      const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
      if (!Number.isNaN(d.getTime())) return d;
    }
    m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/);
    if (m) {
      let yr = Number(m[3]);
      if (yr < 100) yr += 2000;
      const mo = Number(m[1]);
      const dy = Number(m[2]);
      const d = new Date(yr, mo - 1, dy);
      if (d.getFullYear() === yr && d.getMonth() === mo - 1 && d.getDate() === dy) return d;
    }
    throw new Error(`Invalid date '${s}'. Expected YYYY-MM-DD or MM/DD/YYYY.`);
  }

  function formatDateTasks(d) {
    if (!d) return "";
    return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`;
  }

  function formatDateWorklog(d) {
    if (!d) return "";
    return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${String(d.getFullYear()).slice(-2)}`;
  }

  function normalizeDateKey(raw) {
    const d = parseDateValue(raw);
    if (!d) return "";
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function todayDateOnly() {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }

  function parseNumber(raw, label, allowBlank = true) {
    const s = raw == null ? "" : String(raw).trim();
    if (!s) {
      if (allowBlank) return null;
      throw new Error(`${label} is required.`);
    }
    const n = Number(s);
    if (!Number.isFinite(n)) throw new Error(`${label} must be a valid number.`);
    return n;
  }

  function parsePct(raw) {
    if (raw == null || String(raw).trim() === "") return 0;
    const s = String(raw).trim().replace(/%$/, "").trim();
    const n = Number(s);
    if (!Number.isFinite(n) || n < 0 || n > 100) {
      throw new Error("Completion percentage must be between 0 and 100.");
    }
    return n;
  }

  function formatNumCell(val) {
    if (val === null || val === undefined) return "";
    return Number.isInteger(val) ? val : Math.round(val * 10000) / 10000;
  }

  function normalizeWorklogStatus(raw) {
    const v = String(raw || "").trim().toLowerCase();
    if (v === "complete" || v === "completed") return "Complete";
    if (v === "on-going" || v === "ongoing" || v === "in progress") return "On-going";
    throw new Error("Status must be 'On-going' or 'Complete'.");
  }

  // --- Apps Script Mode ---
  async function gasPost(gasUrl, bodyObj) {
    const url = validateGasUrl(gasUrl);
    let res;
    try {
      res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "text/plain" },
        body: JSON.stringify(bodyObj),
      });
    } catch (_) {
      throw new Error(
        "Could not connect to Apps Script or access that Google Sheet (blocked/permission denied). Ensure the Apps Script deployer's Google account has Editor access to this Google Sheet and the Web App is deployed with 'Who has access: Anyone'."
      );
    }
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (_) {
      throw new Error(
        "Unexpected response from Apps Script Web App. Ensure you deployed as Web app with 'Who has access: Anyone' and copied the /exec URL."
      );
    }
    if (!data || data.ok === false) {
      const err = new Error(data?.error || "Apps Script request failed.");
      if (data?.status === 409) err.status = 409;
      throw err;
    }
    return data;
  }

  function gasContext(cfg) {
    return {
      pin: cfg.gas_pin || "",
      sheet_url: cfg.sheet_url || cfg.spreadsheet_id || "",
      tasks_tab: cfg.tasks_tab || DEFAULT_TABS.tasks,
      worklog_tab: cfg.worklog_tab || DEFAULT_TABS.worklog,
      data_tab: cfg.data_tab || DEFAULT_TABS.data,
    };
  }

  // --- Optional Google OAuth Mode ---
  async function ensureAccessToken(clientId, forcePrompt = false) {
    const cid = String(clientId || "").trim();
    if (!cid) {
      throw new Error("Google OAuth Web Client ID is required for OAuth mode.");
    }
    if (!forcePrompt && accessToken && Date.now() < tokenExpiresAt - 30000) {
      return accessToken;
    }
    if (!global.google || !global.google.accounts || !global.google.accounts.oauth2) {
      throw new Error("Google Sign-In library failed to load. Check internet connection or ad-blocker and refresh.");
    }
    return new Promise((resolve, reject) => {
      tokenClient = global.google.accounts.oauth2.initTokenClient({
        client_id: cid,
        scope: SCOPES,
        callback: resp => {
          if (resp.error) {
            reject(new Error(`Google Sign-In failed: ${resp.error_description || resp.error}`));
            return;
          }
          accessToken = resp.access_token;
          tokenExpiresAt = Date.now() + (Number(resp.expires_in || 3600) * 1000);
          resolve(accessToken);
        },
        error_callback: err => {
          reject(new Error(`Google Sign-In popup error: ${err?.message || err?.type || "cancelled"}`));
        },
      });
      tokenClient.requestAccessToken({ prompt: forcePrompt ? "select_account" : "" });
    });
  }

  async function sheetsFetch(path, options = {}, cfgOverride = null) {
    const cfg = cfgOverride || loadBrowserConfig();
    const token = await ensureAccessToken(cfg.client_id, false);
    const url = `https://sheets.googleapis.com/v4/spreadsheets${path}`;
    const res = await fetch(url, {
      ...options,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const apiMsg = data?.error?.message || `Google Sheets API error (${res.status})`;
      if (res.status === 401) {
        accessToken = "";
        tokenExpiresAt = 0;
        throw new Error("Google sign-in session expired. Click Sign In / Switch Google Account in Setup.");
      }
      if (res.status === 403) {
        throw new Error("Permission denied (403) accessing this Google Sheet. Make sure the sheet is shared with Editor permission.");
      }
      if (res.status === 404) {
        throw new Error("Google Sheet not found (404). Please check the Sheet link or ID in Setup.");
      }
      throw new Error(apiMsg);
    }
    return data;
  }

  function findSheetEntry(sheetsList, preferredName, fallbackGid) {
    const target = String(preferredName || "").trim().toLowerCase();
    for (const s of sheetsList) {
      const p = s.properties || {};
      if (String(p.title || "").trim().toLowerCase() === target) {
        return {
          sheetId: p.sheetId,
          title: p.title || "",
          rowCount: p.gridProperties?.rowCount || 150,
          columnCount: p.gridProperties?.columnCount || 26,
        };
      }
    }
    for (const s of sheetsList) {
      const p = s.properties || {};
      if (p.sheetId === fallbackGid) {
        return {
          sheetId: p.sheetId,
          title: p.title || "",
          rowCount: p.gridProperties?.rowCount || 150,
          columnCount: p.gridProperties?.columnCount || 26,
        };
      }
    }
    return null;
  }

  async function getSheetMap(spreadsheetId, tasksTab, worklogTab, dataTab, cfg) {
    const meta = await sheetsFetch(`/${encodeURIComponent(spreadsheetId)}?includeGridData=false`, {}, cfg);
    const sheetsList = meta.sheets || [];
    const available = sheetsList.map(s => s.properties?.title || "");

    const tasks = findSheetEntry(sheetsList, tasksTab, FALLBACK_GIDS.tasks);
    if (!tasks) throw new Error(`Could not find tab '${tasksTab}'. Available tabs: ${available.join(", ")}`);
    const worklog = findSheetEntry(sheetsList, worklogTab, FALLBACK_GIDS.worklog);
    if (!worklog) throw new Error(`Could not find tab '${worklogTab}'. Available tabs: ${available.join(", ")}`);
    const data = findSheetEntry(sheetsList, dataTab, FALLBACK_GIDS.data);

    return {
      spreadsheet_id: spreadsheetId,
      spreadsheet_title: meta.properties?.title || "Employee Reporting",
      available_tabs: available,
      tasks,
      worklog,
      data,
    };
  }

  function taskHasUserContent(r) {
    for (let i = 1; i <= 12; i++) if (cell(r, i)) return true;
    if (cell(r, 14)) return true;
    for (let i = 16; i <= 20; i++) if (cell(r, i)) return true;
    return false;
  }

  function buildTaskRecord(rowNum, r) {
    return {
      row: rowNum,
      sn: cell(r, 0),
      date_of_entry: cell(r, 1),
      detail: cell(r, 2),
      other_person: cell(r, 3),
      other_department: cell(r, 4),
      priority: cell(r, 5),
      est_start_date: cell(r, 6),
      act_start_date: cell(r, 7),
      expected_deadline: cell(r, 8),
      completion_date: cell(r, 9),
      est_time: cell(r, 10),
      act_time: cell(r, 11),
      pct_completed: cell(r, 12),
      status_of_task: cell(r, 13),
      delay_reason: cell(r, 14),
      delay_days: cell(r, 15),
      expected_outcome: cell(r, 16),
      difficulty: cell(r, 17),
      remarks: cell(r, 18),
      supervisor_comments: cell(r, 19),
      clarification: cell(r, 20),
      check_employee: cell(r, 21),
      check_supervisor: cell(r, 22),
      last_updated_at: cell(r, 24),
    };
  }

  function buildWorklogRecord(rowNum, r) {
    return {
      row: rowNum,
      sn: cell(r, 0),
      date: cell(r, 1),
      task: cell(r, 2),
      project: cell(r, 3),
      time_minutes: cell(r, 4),
      status: cell(r, 5),
      comments: cell(r, 6),
    };
  }

  function extractDropdowns(dataRows, worklogs) {
    const staff = [];
    const departments = [];
    const priorities = [];
    const difficulties = [];
    const taskStatuses = [];

    dataRows.forEach((r, ri) => {
      const hrId = cell(r, 1);
      const name = cell(r, 2);
      const desig = cell(r, 4);
      if (ri >= 1 && name && (hrId || desig) && name.toLowerCase() !== "office assistant" && !staff.includes(name)) {
        staff.push(name);
      }
      const dept = cell(r, 9);
      if (ri >= 1 && dept && !departments.includes(dept)) departments.push(dept);
      const st = cell(r, 8);
      if (st && !taskStatuses.includes(st)) taskStatuses.push(st);
      const pr = cell(r, 13);
      if (pr && !priorities.includes(pr)) priorities.push(pr);
      const df = cell(r, 14);
      if (df && !difficulties.includes(df)) difficulties.push(df);
    });

    const projects = [...new Set(worklogs.map(w => w.project).filter(Boolean))].sort((a, b) =>
      a.toLowerCase().localeCompare(b.toLowerCase())
    );

    return {
      staff,
      departments: departments.length ? departments : ["Management", "Support", "Marketing", "Programming", "Admin", "CSP", "Kitchen"],
      priorities: priorities.length ? priorities : ["Low", "Medium", "High"],
      difficulties: difficulties.length ? difficulties : ["Very Difficult", "Difficult", "Moderate", "Easy", "Very Easy"],
      task_statuses: taskStatuses.length ? taskStatuses : ["On Schedule", "Behind Schedule", "Handover", "Abandoned"],
      worklog_statuses: ["On-going", "Complete"],
      projects,
    };
  }

  function buildSetupPayload(cfg, sheetUrl = "") {
    return {
      mode: "browser",
      auth_mode: cfg.auth_mode || "gas",
      gas_url: cfg.gas_url || "",
      gas_pin: cfg.gas_pin || "",
      client_id: cfg.client_id || "",
      spreadsheet_id: cfg.spreadsheet_id || "",
      sheet_url: cfg.sheet_url || "",
      url: sheetUrl,
      tasks_tab: cfg.tasks_tab || DEFAULT_TABS.tasks,
      worklog_tab: cfg.worklog_tab || DEFAULT_TABS.worklog,
      data_tab: cfg.data_tab || DEFAULT_TABS.data,
      sync_md_default: false,
      saved_sheets: cfg.saved_sheets || [],
      has_credentials: cfg.auth_mode === "oauth" ? Boolean(cfg.client_id) : Boolean(cfg.gas_url),
      has_token: cfg.auth_mode === "oauth" ? Boolean(accessToken && Date.now() < tokenExpiresAt - 30000) : Boolean(cfg.gas_url),
    };
  }

  function emptyNeedsSetupResponse(cfg) {
    return {
      needs_setup: true,
      spreadsheet: {
        id: "",
        title: "No Sheet Configured",
        url: "",
        tasks_sheet: cfg.tasks_tab || DEFAULT_TABS.tasks,
        tasks_max_row: 150,
        last_task_row: 8,
        worklog_sheet: cfg.worklog_tab || DEFAULT_TABS.worklog,
        worklog_max_row: 998,
        last_worklog_row: 9,
      },
      setup: buildSetupPayload(cfg),
      employee: {
        name: "Setup Required",
        department: "",
        id: "",
        check_employee_all: true,
        check_supervisor_all: true,
        status_message_1: "",
        status_message_2: "",
      },
      dropdowns: extractDropdowns([], []),
      tasks: [],
      worklogs: [],
    };
  }

  async function fetchBootstrapData(cfgOverride = null) {
    const cfg = cfgOverride || loadBrowserConfig();
    if (cfg.auth_mode !== "oauth") {
      if (!cfg.gas_url) return emptyNeedsSetupResponse(cfg);
      const data = await gasPost(cfg.gas_url, { action: "bootstrap", ...gasContext(cfg) });
      const mergedSetup = buildSetupPayload(cfg, data.spreadsheet?.url || "");
      Object.assign(mergedSetup, data.setup || {}, {
        mode: "browser",
        auth_mode: "gas",
        gas_url: cfg.gas_url,
        gas_pin: cfg.gas_pin || "",
        saved_sheets: cfg.saved_sheets || [],
        has_credentials: true,
        has_token: true,
      });
      data.setup = mergedSetup;
      return data;
    }

    const sid = String(cfg.spreadsheet_id || "").trim();
    if (!sid || !cfg.client_id) return emptyNeedsSetupResponse(cfg);

    const smap = await getSheetMap(sid, cfg.tasks_tab, cfg.worklog_tab, cfg.data_tab, cfg);
    const tTitle = quoteSheet(smap.tasks.title);
    const wTitle = quoteSheet(smap.worklog.title);
    const tMax = smap.tasks.rowCount;
    const wMax = smap.worklog.rowCount;

    const params = new URLSearchParams();
    params.append("ranges", `${tTitle}!A5:Z7`);
    params.append("ranges", `${tTitle}!A${TASKS_START_ROW}:Z${tMax}`);
    params.append("ranges", `${wTitle}!A${WORKLOG_START_ROW}:G${wMax}`);
    if (smap.data) params.append("ranges", `${quoteSheet(smap.data.title)}!A1:O45`);
    params.set("valueRenderOption", "FORMATTED_VALUE");

    const res = await sheetsFetch(`/${encodeURIComponent(sid)}/values:batchGet?${params.toString()}`, {}, cfg);
    const vrs = res.valueRanges || [];
    const metaRows = vrs[0]?.values || [];
    const tRows = vrs[1]?.values || [];
    const wRows = vrs[2]?.values || [];
    const dRows = vrs[3]?.values || [];

    const r5 = metaRows[0] || [];
    const r6 = metaRows[1] || [];
    const r7 = metaRows[2] || [];

    const tasks = [];
    let lastTaskRow = TASKS_START_ROW - 1;
    tRows.forEach((r, offset) => {
      const rn = TASKS_START_ROW + offset;
      if (taskHasUserContent(r)) {
        tasks.push(buildTaskRecord(rn, r));
        lastTaskRow = rn;
      }
    });

    const worklogs = [];
    let lastWlRow = WORKLOG_START_ROW - 1;
    wRows.forEach((r, offset) => {
      const rn = WORKLOG_START_ROW + offset;
      let hasAny = false;
      for (let i = 1; i <= 6; i++) if (cell(r, i)) { hasAny = true; break; }
      if (hasAny) {
        worklogs.push(buildWorklogRecord(rn, r));
        lastWlRow = rn;
      }
    });

    const tGid = smap.tasks.sheetId;
    const sheetUrl =
      tGid != null
        ? `https://docs.google.com/spreadsheets/d/${sid}/edit?gid=${tGid}#gid=${tGid}`
        : `https://docs.google.com/spreadsheets/d/${sid}/edit`;

    const setupInfo = buildSetupPayload(cfg, sheetUrl);
    setupInfo.tasks_tab = smap.tasks.title;
    setupInfo.worklog_tab = smap.worklog.title;
    setupInfo.data_tab = smap.data ? smap.data.title : cfg.data_tab;

    return {
      needs_setup: false,
      spreadsheet: {
        id: sid,
        title: smap.spreadsheet_title,
        url: sheetUrl,
        tasks_sheet: smap.tasks.title,
        tasks_max_row: tMax,
        last_task_row: lastTaskRow,
        worklog_sheet: smap.worklog.title,
        worklog_max_row: wMax,
        last_worklog_row: lastWlRow,
      },
      setup: setupInfo,
      employee: {
        name: cell(r5, 2),
        department: cell(r6, 2),
        id: cell(r7, 2),
        check_employee_all: cell(r7, 21) ? cell(r7, 21).toUpperCase() !== "FALSE" : true,
        check_supervisor_all: cell(r7, 22) ? cell(r7, 22).toUpperCase() !== "FALSE" : true,
        status_message_1: cell(r5, 3),
        status_message_2: cell(r6, 3),
      },
      dropdowns: extractDropdowns(dRows, worklogs),
      tasks,
      worklogs,
    };
  }

  function validateAndPrepareTask(payload) {
    const allowPartial = Boolean(payload.allow_partial);
    const entryD = parseDateValue(payload.date_of_entry) || todayDateOnly();
    const detail = String(payload.detail || "").trim();
    const otherPerson = String(payload.other_person || "").trim();
    const otherDept = String(payload.other_department || "").trim();
    const priority = String(payload.priority || "").trim();
    const estStartD = parseDateValue(payload.est_start_date);
    const actStartD = parseDateValue(payload.act_start_date);
    const deadlineD = parseDateValue(payload.expected_deadline);
    const completionD = parseDateValue(payload.completion_date);

    const estTime = parseNumber(payload.est_time, "Estimated Time (hours)", allowPartial);
    const actTime = parseNumber(payload.act_time, "Actual Time (hours)", true);
    const pct = parsePct(payload.pct_completed);
    const delayReason = String(payload.delay_reason || "").trim();
    const expectedOutcome = String(payload.expected_outcome || "").trim();
    const difficulty = String(payload.difficulty || "").trim();
    const remarks = String(payload.remarks || "").trim();
    const supervisorComments = String(payload.supervisor_comments || "").trim();
    const clarification = String(payload.clarification || "").trim();

    if (!detail) throw new Error("Task detail (with purpose) is required.");

    if (!allowPartial) {
      const missing = [];
      if (!priority) missing.push("Priority Scale");
      if (!difficulty) missing.push("Level of Task Difficulty");
      if (!expectedOutcome) missing.push("Expected Outcome");
      if (!estStartD) missing.push("Estimated Start Date");
      if (!deadlineD) missing.push("Expected Deadline");
      if (estTime === null || estTime < 0) missing.push("Estimated Time (>= 0)");
      if (missing.length) throw new Error(`Required for Tasks validation: ${missing.join(", ")}.`);

      if (completionD) {
        const errs = [];
        if (!actStartD) errs.push("Actual Start Date");
        if (actTime === null || actTime <= 0) errs.push("Actual Time (> 0)");
        if (Math.abs(pct - 100) > 1e-6) errs.push("% Completed must be 100%");
        if (errs.length) throw new Error(`When Completion Date is filled, Tasks sheet requires: ${errs.join(", ")}.`);
      }

      const todayD = todayDateOnly();
      const delayed = deadlineD && ((completionD && completionD > deadlineD) || (!completionD && todayD > deadlineD));
      if (delayed && !delayReason) {
        throw new Error("Delay Reason (Col O) is required because Completion Date (or Today) is past Expected Deadline.");
      }
    } else {
      if (estTime !== null && estTime < 0) throw new Error("Estimated Time cannot be negative.");
      if (actTime !== null && actTime < 0) throw new Error("Actual Time cannot be negative.");
    }

    const pctStr = String(payload.pct_completed ?? "").trim() !== "" ? `${pct.toFixed(2)}%` : "";

    return {
      B_to_M: [
        formatDateTasks(entryD),
        detail,
        otherPerson,
        otherDept,
        priority,
        formatDateTasks(estStartD),
        formatDateTasks(actStartD),
        formatDateTasks(deadlineD),
        formatDateTasks(completionD),
        formatNumCell(estTime),
        formatNumCell(actTime),
        pctStr,
      ],
      O: [[delayReason]],
      Q_to_U: [[expectedOutcome, difficulty, remarks, supervisorComments, clarification]],
    };
  }

  async function writeTaskRow(sid, sheetTitle, rowNum, prepared, cfg) {
    const qt = quoteSheet(sheetTitle);
    await sheetsFetch(
      `/${encodeURIComponent(sid)}/values:batchUpdate`,
      {
        method: "POST",
        body: JSON.stringify({
          valueInputOption: "USER_ENTERED",
          data: [
            { range: `${qt}!B${rowNum}:M${rowNum}`, values: [prepared.B_to_M] },
            { range: `${qt}!O${rowNum}`, values: prepared.O },
            { range: `${qt}!Q${rowNum}:U${rowNum}`, values: prepared.Q_to_U },
          ],
        }),
      },
      cfg
    );
  }

  function taskSnapshotMatches(existing, expected) {
    if (!expected) return true;
    return (
      (existing.detail || "") === (expected.detail || "") &&
      (existing.date_of_entry || "") === (expected.date_of_entry || "")
    );
  }

  function validateAndPrepareWorklog(payload) {
    const d = parseDateValue(payload.date) || todayDateOnly();
    const dateStr = formatDateWorklog(d);
    const task = String(payload.task || "").trim();
    const project = String(payload.project || "").trim();
    const mins = parseNumber(payload.time_minutes, "Time (minutes)", false);
    if (mins === null || mins < 0) throw new Error("Time (minutes) must be 0 or greater.");
    const status = normalizeWorklogStatus(payload.status || "Complete");
    const comments = String(payload.comments || "").trim();

    if (!task) throw new Error("Task description is required.");
    if (!project) throw new Error("Client / Project is required.");

    const timeCell = formatNumCell(mins);
    return {
      date: dateStr,
      task,
      project,
      time_minutes: String(timeCell),
      status,
      comments,
      row_values: [dateStr, task, project, timeCell, status, comments],
    };
  }

  function worklogSnapshotMatches(existing, expected) {
    if (!expected) return true;
    return (
      (existing.task || "") === (expected.task || "") &&
      (existing.project || "") === (expected.project || "") &&
      normalizeDateKey(existing.date || "") === normalizeDateKey(expected.date || "")
    );
  }

  async function probeSetup(payload) {
    const cfg = loadBrowserConfig();
    const authMode = payload.auth_mode || cfg.auth_mode || "gas";
    const tasksTab = String(payload.tasks_tab || DEFAULT_TABS.tasks).trim() || DEFAULT_TABS.tasks;
    const worklogTab = String(payload.worklog_tab || DEFAULT_TABS.worklog).trim() || DEFAULT_TABS.worklog;
    const dataTab = String(payload.data_tab || DEFAULT_TABS.data).trim() || DEFAULT_TABS.data;

    if (authMode !== "oauth") {
      const gasUrl = validateGasUrl(payload.gas_url);
      const pin = String(payload.gas_pin || "").trim();
      const sheetUrl = String(payload.sheet_url || "").trim();
      const info = await gasPost(gasUrl, {
        action: "setup_test",
        pin,
        sheet_url: sheetUrl,
        tasks_tab: tasksTab,
        worklog_tab: worklogTab,
        data_tab: dataTab,
      });
      return { ...info, auth_mode: "gas", gas_url: gasUrl, gas_pin: pin, sheet_url: sheetUrl };
    }

    const clientId = String(payload.client_id || cfg.client_id || "").trim();
    const sid = parseSpreadsheetId(payload.sheet_url || payload.spreadsheet_id || "");
    await ensureAccessToken(clientId, false);
    const probeCfg = { ...cfg, auth_mode: "oauth", client_id: clientId };
    const smap = await getSheetMap(sid, tasksTab, worklogTab, dataTab, probeCfg);
    const qt = quoteSheet(smap.tasks.title);
    const meta = await sheetsFetch(
      `/${encodeURIComponent(sid)}/values/${encodeURIComponent(`${qt}!A5:C7`)}?valueRenderOption=FORMATTED_VALUE`,
      {},
      probeCfg
    );
    const rows = meta.values || [];
    const tGid = smap.tasks.sheetId;
    const url =
      tGid != null
        ? `https://docs.google.com/spreadsheets/d/${sid}/edit?gid=${tGid}#gid=${tGid}`
        : `https://docs.google.com/spreadsheets/d/${sid}/edit`;

    return {
      ok: true,
      auth_mode: "oauth",
      client_id: clientId,
      spreadsheet_id: sid,
      title: smap.spreadsheet_title,
      employee_name: cell(rows[0] || [], 2),
      department: cell(rows[1] || [], 2),
      employee_id: cell(rows[2] || [], 2),
      tasks_tab: smap.tasks.title,
      worklog_tab: smap.worklog.title,
      data_tab: smap.data ? smap.data.title : "",
      available_tabs: smap.available_tabs,
      url,
    };
  }

  async function handleRequest(url, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const payload = options.body ? JSON.parse(options.body) : {};
    const cfg = loadBrowserConfig();

    if (url === "/api/bootstrap" && method === "GET") {
      return await fetchBootstrapData(cfg);
    }
    if (url === "/api/setup" && method === "GET") {
      return buildSetupPayload(cfg);
    }
    if (url === "/api/setup/test" && method === "POST") {
      return await probeSetup(payload);
    }
    if (url === "/api/setup/reset" && method === "POST") {
      resetBrowserConfig();
      return await fetchBootstrapData(loadBrowserConfig());
    }
    if (url === "/api/setup" && method === "POST") {
      const info = await probeSetup(payload);
      cfg.auth_mode = info.auth_mode;
      if (info.auth_mode === "gas") {
        cfg.gas_url = info.gas_url;
        cfg.gas_pin = info.gas_pin || "";
        cfg.sheet_url = info.sheet_url || "";
        cfg.spreadsheet_id = info.spreadsheet_id || "";
      } else {
        cfg.client_id = info.client_id;
        cfg.spreadsheet_id = info.spreadsheet_id;
        cfg.sheet_url = info.url || "";
      }
      cfg.tasks_tab = info.tasks_tab;
      cfg.worklog_tab = info.worklog_tab;
      cfg.data_tab = info.data_tab || DEFAULT_TABS.data;
      upsertSavedSheet(cfg, {
        auth_mode: cfg.auth_mode,
        gas_url: cfg.gas_url || "",
        gas_pin: cfg.gas_pin || "",
        client_id: cfg.client_id || "",
        spreadsheet_id: info.spreadsheet_id || "",
        sheet_url: cfg.sheet_url || "",
        title: info.title || "Google Sheet",
        employee_name: info.employee_name || "",
        tasks_tab: info.tasks_tab,
        worklog_tab: info.worklog_tab,
        data_tab: cfg.data_tab,
        url: info.url || "",
      });
      saveBrowserConfig(cfg);
      return await fetchBootstrapData(cfg);
    }
    if (url === "/api/setup/remove" && method === "POST") {
      const sid = String(payload.spreadsheet_id || "").trim();
      if (!sid) throw new Error("spreadsheet_id is required.");
      if (sid === cfg.spreadsheet_id) throw new Error("Cannot remove the currently active sheet.");
      cfg.saved_sheets = (cfg.saved_sheets || []).filter(s => (s.spreadsheet_id || s.gas_url) !== sid);
      saveBrowserConfig(cfg);
      return { ok: true, saved_sheets: cfg.saved_sheets };
    }
    if (url === "/api/setup/reauth" && method === "POST") {
      const cid = String(payload.client_id || cfg.client_id || "").trim();
      await ensureAccessToken(cid, true);
      cfg.auth_mode = "oauth";
      cfg.client_id = cid;
      saveBrowserConfig(cfg);
      return await fetchBootstrapData(cfg);
    }

    // --- Apps Script CRUD routing ---
    if (cfg.auth_mode !== "oauth") {
      if (!cfg.gas_url) throw new Error("No Apps Script Web App URL configured. Open Setup first.");
      const baseCtx = gasContext(cfg);

      if (url === "/api/tasks" && method === "POST") {
        validateAndPrepareTask(payload);
        const res = await gasPost(cfg.gas_url, { ...payload, ...baseCtx, action: "create_task" });
        res.setup = Object.assign(buildSetupPayload(cfg, res.spreadsheet?.url || ""), res.setup || {}, {
          mode: "browser", auth_mode: "gas", gas_url: cfg.gas_url, gas_pin: cfg.gas_pin || "", saved_sheets: cfg.saved_sheets || [],
        });
        return res;
      }
      let mTask = url.match(/^\/api\/tasks\/(\d+)$/);
      if (mTask && method === "PUT") {
        validateAndPrepareTask(payload);
        const res = await gasPost(cfg.gas_url, { ...payload, ...baseCtx, action: "update_task", row: Number(mTask[1]) });
        res.setup = Object.assign(buildSetupPayload(cfg, res.spreadsheet?.url || ""), res.setup || {}, {
          mode: "browser", auth_mode: "gas", gas_url: cfg.gas_url, gas_pin: cfg.gas_pin || "", saved_sheets: cfg.saved_sheets || [],
        });
        return res;
      }
      if (mTask && method === "DELETE") {
        const res = await gasPost(cfg.gas_url, { ...payload, ...baseCtx, action: "delete_task", row: Number(mTask[1]) });
        res.setup = Object.assign(buildSetupPayload(cfg, res.spreadsheet?.url || ""), res.setup || {}, {
          mode: "browser", auth_mode: "gas", gas_url: cfg.gas_url, gas_pin: cfg.gas_pin || "", saved_sheets: cfg.saved_sheets || [],
        });
        return res;
      }
      if (url === "/api/worklogs" && method === "POST") {
        validateAndPrepareWorklog(payload);
        const res = await gasPost(cfg.gas_url, { ...payload, ...baseCtx, action: "create_worklog" });
        res.setup = Object.assign(buildSetupPayload(cfg, res.spreadsheet?.url || ""), res.setup || {}, {
          mode: "browser", auth_mode: "gas", gas_url: cfg.gas_url, gas_pin: cfg.gas_pin || "", saved_sheets: cfg.saved_sheets || [],
        });
        return res;
      }
      let mWl = url.match(/^\/api\/worklogs\/(\d+)$/);
      if (mWl && method === "PUT") {
        validateAndPrepareWorklog(payload);
        const res = await gasPost(cfg.gas_url, { ...payload, ...baseCtx, action: "update_worklog", row: Number(mWl[1]) });
        res.setup = Object.assign(buildSetupPayload(cfg, res.spreadsheet?.url || ""), res.setup || {}, {
          mode: "browser", auth_mode: "gas", gas_url: cfg.gas_url, gas_pin: cfg.gas_pin || "", saved_sheets: cfg.saved_sheets || [],
        });
        return res;
      }
      if (mWl && method === "DELETE") {
        const res = await gasPost(cfg.gas_url, { ...payload, ...baseCtx, action: "delete_worklog", row: Number(mWl[1]) });
        res.setup = Object.assign(buildSetupPayload(cfg, res.spreadsheet?.url || ""), res.setup || {}, {
          mode: "browser", auth_mode: "gas", gas_url: cfg.gas_url, gas_pin: cfg.gas_pin || "", saved_sheets: cfg.saved_sheets || [],
        });
        return res;
      }
      throw new Error(`Unsupported API request: ${method} ${url}`);
    }

    // --- OAuth REST API v4 CRUD routing ---
    if (url === "/api/tasks" && method === "POST") {
      const prepared = validateAndPrepareTask(payload);
      const smap = await getSheetMap(cfg.spreadsheet_id, cfg.tasks_tab, cfg.worklog_tab, cfg.data_tab, cfg);
      const qt = quoteSheet(smap.tasks.title);
      const tMax = smap.tasks.rowCount;
      const res = await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qt}!A${TASKS_START_ROW}:U${tMax}`)}?valueRenderOption=FORMATTED_VALUE`,
        {},
        cfg
      );
      const rows = res.values || [];
      let lastUsed = TASKS_START_ROW - 1;
      rows.forEach((r, offset) => { if (taskHasUserContent(r)) lastUsed = TASKS_START_ROW + offset; });
      const targetRow = lastUsed + 1;
      if (targetRow > tMax) throw new Error(`Tasks sheet has reached its template limit of row ${tMax}.`);
      await writeTaskRow(cfg.spreadsheet_id, smap.tasks.title, targetRow, prepared, cfg);
      const data = await fetchBootstrapData(cfg);
      data.affected_row = targetRow;
      return data;
    }

    let mTask = url.match(/^\/api\/tasks\/(\d+)$/);
    if (mTask && method === "PUT") {
      const rowNum = Number(mTask[1]);
      const prepared = validateAndPrepareTask(payload);
      const smap = await getSheetMap(cfg.spreadsheet_id, cfg.tasks_tab, cfg.worklog_tab, cfg.data_tab, cfg);
      const tMax = smap.tasks.rowCount;
      if (rowNum < TASKS_START_ROW || rowNum > tMax) throw new Error(`Task row must be between ${TASKS_START_ROW} and ${tMax}.`);
      const qt = quoteSheet(smap.tasks.title);
      const currRes = await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qt}!A${rowNum}:Z${rowNum}`)}?valueRenderOption=FORMATTED_VALUE`,
        {},
        cfg
      );
      const currRec = buildTaskRecord(rowNum, (currRes.values || [])[0] || []);
      if (!taskSnapshotMatches(currRec, payload.expected_snapshot)) {
        const err = new Error("This task row changed in Google Sheets since you loaded it. Refreshing latest data.");
        err.status = 409;
        throw err;
      }
      await writeTaskRow(cfg.spreadsheet_id, smap.tasks.title, rowNum, prepared, cfg);
      const data = await fetchBootstrapData(cfg);
      data.affected_row = rowNum;
      return data;
    }

    if (mTask && method === "DELETE") {
      const rowNum = Number(mTask[1]);
      const smap = await getSheetMap(cfg.spreadsheet_id, cfg.tasks_tab, cfg.worklog_tab, cfg.data_tab, cfg);
      const tMax = smap.tasks.rowCount;
      if (rowNum < TASKS_START_ROW || rowNum > tMax) throw new Error(`Task row must be between ${TASKS_START_ROW} and ${tMax}.`);
      const qt = quoteSheet(smap.tasks.title);
      const res = await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qt}!A${TASKS_START_ROW}:U${tMax}`)}?valueRenderOption=FORMATTED_VALUE`,
        {},
        cfg
      );
      const rows = res.values || [];
      const currRow = rows[rowNum - TASKS_START_ROW] || [];
      if (!taskHasUserContent(currRow)) throw new Error(`Task row ${rowNum} is already empty.`);
      if (!taskSnapshotMatches(buildTaskRecord(rowNum, currRow), payload.expected_snapshot)) {
        const err = new Error("This task row changed in Google Sheets since you loaded it. Refreshing latest data.");
        err.status = 409;
        throw err;
      }
      let lastUsed = rowNum;
      rows.forEach((r, offset) => { if (taskHasUserContent(r)) lastUsed = Math.max(lastUsed, TASKS_START_ROW + offset); });
      const updates = [];
      if (rowNum < lastUsed) {
        const shiftBM = [], shiftO = [], shiftQU = [];
        for (let rn = rowNum + 1; rn <= lastUsed; rn++) {
          const r = rows[rn - TASKS_START_ROW] || [];
          const rowBM = []; for (let c = 1; c <= 12; c++) rowBM.push(cell(r, c)); shiftBM.push(rowBM);
          shiftO.push([cell(r, 14)]);
          const rowQU = []; for (let c = 16; c <= 20; c++) rowQU.push(cell(r, c)); shiftQU.push(rowQU);
        }
        updates.push(
          { range: `${qt}!B${rowNum}:M${lastUsed - 1}`, values: shiftBM },
          { range: `${qt}!O${rowNum}:O${lastUsed - 1}`, values: shiftO },
          { range: `${qt}!Q${rowNum}:U${lastUsed - 1}`, values: shiftQU }
        );
      }
      updates.push(
        { range: `${qt}!B${lastUsed}:M${lastUsed}`, values: [Array(12).fill("")] },
        { range: `${qt}!O${lastUsed}`, values: [[""]] },
        { range: `${qt}!Q${lastUsed}:U${lastUsed}`, values: [Array(5).fill("")] }
      );
      await sheetsFetch(`/${encodeURIComponent(cfg.spreadsheet_id)}/values:batchUpdate`, {
        method: "POST", body: JSON.stringify({ valueInputOption: "USER_ENTERED", data: updates }),
      }, cfg);
      const data = await fetchBootstrapData(cfg);
      data.deleted_row = rowNum;
      return data;
    }

    if (url === "/api/worklogs" && method === "POST") {
      const prepared = validateAndPrepareWorklog(payload);
      const smap = await getSheetMap(cfg.spreadsheet_id, cfg.tasks_tab, cfg.worklog_tab, cfg.data_tab, cfg);
      const qw = quoteSheet(smap.worklog.title);
      const wMax = smap.worklog.rowCount;
      const res = await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qw}!A${WORKLOG_START_ROW}:G${wMax}`)}?valueRenderOption=FORMATTED_VALUE`,
        {},
        cfg
      );
      const rows = res.values || [];
      let lastUsed = WORKLOG_START_ROW - 1;
      rows.forEach((r, offset) => {
        for (let i = 1; i <= 6; i++) if (cell(r, i)) { lastUsed = WORKLOG_START_ROW + offset; break; }
      });
      const targetRow = lastUsed + 1;
      if (targetRow > wMax) {
        await sheetsFetch(`/${encodeURIComponent(cfg.spreadsheet_id)}:batchUpdate`, {
          method: "POST",
          body: JSON.stringify({ requests: [{ appendDimension: { sheetId: smap.worklog.sheetId, dimension: "ROWS", length: 100 } }] }),
        }, cfg);
      }
      await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qw}!B${targetRow}:G${targetRow}`)}?valueInputOption=USER_ENTERED`,
        { method: "PUT", body: JSON.stringify({ values: [prepared.row_values] }) },
        cfg
      );
      const data = await fetchBootstrapData(cfg);
      data.affected_row = targetRow;
      return data;
    }

    let mWl = url.match(/^\/api\/worklogs\/(\d+)$/);
    if (mWl && method === "PUT") {
      const rowNum = Number(mWl[1]);
      const prepared = validateAndPrepareWorklog(payload);
      const smap = await getSheetMap(cfg.spreadsheet_id, cfg.tasks_tab, cfg.worklog_tab, cfg.data_tab, cfg);
      const wMax = smap.worklog.rowCount;
      if (rowNum < WORKLOG_START_ROW || rowNum > wMax) throw new Error(`Work log row must be between ${WORKLOG_START_ROW} and ${wMax}.`);
      const qw = quoteSheet(smap.worklog.title);
      const currRes = await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qw}!A${rowNum}:G${rowNum}`)}?valueRenderOption=FORMATTED_VALUE`,
        {},
        cfg
      );
      const currRec = buildWorklogRecord(rowNum, (currRes.values || [])[0] || []);
      if (!worklogSnapshotMatches(currRec, payload.expected_snapshot)) {
        const err = new Error("This work log row changed in Google Sheets since you loaded it. Refreshing latest data.");
        err.status = 409;
        throw err;
      }
      await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qw}!B${rowNum}:G${rowNum}`)}?valueInputOption=USER_ENTERED`,
        { method: "PUT", body: JSON.stringify({ values: [prepared.row_values] }) },
        cfg
      );
      const data = await fetchBootstrapData(cfg);
      data.affected_row = rowNum;
      return data;
    }

    if (mWl && method === "DELETE") {
      const rowNum = Number(mWl[1]);
      const smap = await getSheetMap(cfg.spreadsheet_id, cfg.tasks_tab, cfg.worklog_tab, cfg.data_tab, cfg);
      const wMax = smap.worklog.rowCount;
      if (rowNum < WORKLOG_START_ROW || rowNum > wMax) throw new Error(`Work log row must be between ${WORKLOG_START_ROW} and ${wMax}.`);
      const qw = quoteSheet(smap.worklog.title);
      const res = await sheetsFetch(
        `/${encodeURIComponent(cfg.spreadsheet_id)}/values/${encodeURIComponent(`${qw}!A${WORKLOG_START_ROW}:G${wMax}`)}?valueRenderOption=FORMATTED_VALUE`,
        {},
        cfg
      );
      const rows = res.values || [];
      const currRow = rows[rowNum - WORKLOG_START_ROW] || [];
      let rowNonEmpty = false;
      for (let i = 1; i <= 6; i++) if (cell(currRow, i)) rowNonEmpty = true;
      if (!rowNonEmpty) throw new Error(`Work log row ${rowNum} is already empty.`);
      const currRec = buildWorklogRecord(rowNum, currRow);
      if (!worklogSnapshotMatches(currRec, payload.expected_snapshot)) {
        const err = new Error("This work log row changed in Google Sheets since you loaded it. Refreshing latest data.");
        err.status = 409;
        throw err;
      }
      let lastUsed = rowNum;
      rows.forEach((r, offset) => {
        for (let i = 1; i <= 6; i++) if (cell(r, i)) { lastUsed = Math.max(lastUsed, WORKLOG_START_ROW + offset); break; }
      });
      const updates = [];
      if (rowNum < lastUsed) {
        const shiftRows = [];
        for (let rn = rowNum + 1; rn <= lastUsed; rn++) {
          const r = rows[rn - WORKLOG_START_ROW] || [];
          const outR = []; for (let c = 1; c <= 6; c++) outR.push(cell(r, c)); shiftRows.push(outR);
        }
        updates.push({ range: `${qw}!B${rowNum}:G${lastUsed - 1}`, values: shiftRows });
      }
      updates.push({ range: `${qw}!B${lastUsed}:G${lastUsed}`, values: [Array(6).fill("")] });
      await sheetsFetch(`/${encodeURIComponent(cfg.spreadsheet_id)}/values:batchUpdate`, {
        method: "POST", body: JSON.stringify({ valueInputOption: "USER_ENTERED", data: updates }),
      }, cfg);
      const data = await fetchBootstrapData(cfg);
      data.deleted_row = rowNum;
      return data;
    }

    throw new Error(`Unsupported API request: ${method} ${url}`);
  }

  function exportWorklogMarkdown(worklogs) {
    let md = "# Work Log History\n\nThis file keeps a local record of work tasks logged to Google Sheets.\n\n";
    let currentDate = "";
    worklogs.forEach(w => {
      const dateStr = String(w.date || "").trim() || currentDate || "Unknown Date";
      if (dateStr !== currentDate) {
        currentDate = dateStr;
        md += `## ${currentDate}\n`;
      }
      const inline = s => String(s || "").replace(/\s*\r?\n+\s*/g, " ").trim();
      md += `- **Project**: ${inline(w.project)}\n`;
      md += `  **Task**: ${inline(w.task)}\n`;
      md += `  **Time**: ${inline(w.time_minutes)} minutes\n`;
      md += `  **Status**: ${inline(w.status)}\n`;
      md += `  **Comments**: ${inline(w.comments)}\n\n`;
    });
    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "Work_Log_History.md";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 100);
  }

  global.SheetsBrowserAdapter = {
    loadBrowserConfig,
    handleRequest,
    exportWorklogMarkdown,
  };
})(window);
