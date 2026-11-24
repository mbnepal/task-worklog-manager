/**
 * Tasks & Work Log Manager — Google Apps Script Web App Backend
 *
 * Setup (one-time per Google Sheet):
 * 1. Open your Employee Monthly Reporting Google Sheet -> Extensions -> Apps Script.
 * 2. Paste this entire file into Code.gs (replacing any placeholder code) and Save.
 * 3. Click Deploy -> New deployment -> Select type: Web app.
 *    - Execute as: Me
 *    - Who has access: Anyone
 * 4. Authorize once when prompted and copy the Web App URL (ends with /exec).
 * 5. Paste the Web App URL into the Tasks & Work Log Manager Setup modal.
 */

const TWM_ACCESS_PIN = ""; // Optional: set a PIN to restrict access
const TWM_DEFAULT_TASKS_TAB = "Tasks";
const TWM_DEFAULT_WORKLOG_TAB = "Work log";
const TWM_DEFAULT_DATA_TAB = "Data";
const TWM_DEFAULT_PERF_TAB = "Perf Standard";
const TWM_TASKS_GID = 697912632;
const TWM_WORKLOG_GID = 1436624543;
const TWM_DATA_GID = 1258735725;
const TWM_PERF_GID = 1601402663;
const TWM_MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"
];
const TWM_TASKS_START_ROW = 9;
const TWM_WORKLOG_START_ROW = 10;

function twmCheckPin_(pin) {
  if (!TWM_ACCESS_PIN) return true;
  return String(pin || "") === String(TWM_ACCESS_PIN);
}

function twmJsonOut_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function twmParseSpreadsheetId_(linkOrId) {
  const s = String(linkOrId || "").trim();
  if (!s) return "";
  const m = s.match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  if (m) return m[1];
  if (/^[a-zA-Z0-9-_]{20,}$/.test(s)) return s;
  throw new Error("Invalid Google Sheet link or Spreadsheet ID.");
}

function twmGetSpreadsheet_(sheetUrlOrId) {
  const active = SpreadsheetApp.getActiveSpreadsheet();
  const id = twmParseSpreadsheetId_(sheetUrlOrId);
  if (!id || (active && id === active.getId())) {
    if (!active) throw new Error("This Apps Script is standalone (not inside a sheet). Paste your Google Sheet Link or ID in Setup.");
    return active;
  }
  return SpreadsheetApp.openById(id);
}

function twmFindSheet_(ss, preferredName, fallbackGid) {
  const target = String(preferredName || "").trim().toLowerCase();
  const sheets = ss.getSheets();
  for (let i = 0; i < sheets.length; i++) {
    if (sheets[i].getName().trim().toLowerCase() === target) {
      return sheets[i];
    }
  }
  for (let i = 0; i < sheets.length; i++) {
    if (sheets[i].getSheetId() === fallbackGid) {
      return sheets[i];
    }
  }
  return null;
}

function twmResolveSheets_(ss, params) {
  const tasksTab = String((params && params.tasks_tab) || TWM_DEFAULT_TASKS_TAB).trim() || TWM_DEFAULT_TASKS_TAB;
  const worklogTab = String((params && params.worklog_tab) || TWM_DEFAULT_WORKLOG_TAB).trim() || TWM_DEFAULT_WORKLOG_TAB;
  const dataTab = String((params && params.data_tab) || TWM_DEFAULT_DATA_TAB).trim() || TWM_DEFAULT_DATA_TAB;
  const perfTab = String((params && params.perf_tab) || TWM_DEFAULT_PERF_TAB).trim() || TWM_DEFAULT_PERF_TAB;

  const available = ss.getSheets().map(function (s) { return s.getName(); });
  const tasksSheet = twmFindSheet_(ss, tasksTab, TWM_TASKS_GID);
  if (!tasksSheet) {
    throw new Error("Could not find tab '" + tasksTab + "'. Available tabs: " + available.join(", "));
  }
  const worklogSheet = twmFindSheet_(ss, worklogTab, TWM_WORKLOG_GID);
  if (!worklogSheet) {
    throw new Error("Could not find tab '" + worklogTab + "'. Available tabs: " + available.join(", "));
  }
  const dataSheet = twmFindSheet_(ss, dataTab, TWM_DATA_GID);
  const perfSheet = twmFindSheet_(ss, perfTab, TWM_PERF_GID);

  return {
    tasksSheet: tasksSheet,
    worklogSheet: worklogSheet,
    dataSheet: dataSheet,
    perfSheet: perfSheet,
    availableTabs: available
  };
}

function twmCell_(row, idx) {
  if (!row || idx >= row.length || row[idx] === null || row[idx] === undefined) return "";
  return String(row[idx]).trim();
}

function twmTaskHasContent_(r) {
  for (let i = 1; i <= 12; i++) {
    if (twmCell_(r, i)) return true;
  }
  if (twmCell_(r, 14)) return true;
  for (let i = 16; i <= 20; i++) {
    if (twmCell_(r, i)) return true;
  }
  return false;
}

function twmBuildTaskRecord_(rowNum, r) {
  return {
    row: rowNum,
    sn: twmCell_(r, 0),
    date_of_entry: twmCell_(r, 1),
    detail: twmCell_(r, 2),
    other_person: twmCell_(r, 3),
    other_department: twmCell_(r, 4),
    priority: twmCell_(r, 5),
    est_start_date: twmCell_(r, 6),
    act_start_date: twmCell_(r, 7),
    expected_deadline: twmCell_(r, 8),
    completion_date: twmCell_(r, 9),
    est_time: twmCell_(r, 10),
    act_time: twmCell_(r, 11),
    pct_completed: twmCell_(r, 12),
    status_of_task: twmCell_(r, 13),
    delay_reason: twmCell_(r, 14),
    delay_days: twmCell_(r, 15),
    expected_outcome: twmCell_(r, 16),
    difficulty: twmCell_(r, 17),
    remarks: twmCell_(r, 18),
    supervisor_comments: twmCell_(r, 19),
    clarification: twmCell_(r, 20),
    check_employee: twmCell_(r, 21),
    check_supervisor: twmCell_(r, 22),
    last_updated_at: twmCell_(r, 24)
  };
}

function twmBuildWorklogRecord_(rowNum, r) {
  return {
    row: rowNum,
    sn: twmCell_(r, 0),
    date: twmCell_(r, 1),
    task: twmCell_(r, 2),
    project: twmCell_(r, 3),
    time_minutes: twmCell_(r, 4),
    status: twmCell_(r, 5),
    comments: twmCell_(r, 6)
  };
}

function twmExtractDropdowns_(dataRows, worklogs) {
  const staff = [];
  const departments = [];
  const priorities = [];
  const difficulties = [];
  const taskStatuses = [];

  for (let ri = 0; ri < dataRows.length; ri++) {
    const r = dataRows[ri];
    const hrId = twmCell_(r, 1);
    const name = twmCell_(r, 2);
    const desig = twmCell_(r, 4);
    if (ri >= 1 && name && (hrId || desig) && name.toLowerCase() !== "office assistant" && staff.indexOf(name) === -1) {
      staff.push(name);
    }
    const dept = twmCell_(r, 9);
    if (ri >= 1 && dept && departments.indexOf(dept) === -1) departments.push(dept);
    const st = twmCell_(r, 8);
    if (st && taskStatuses.indexOf(st) === -1) taskStatuses.push(st);
    const pr = twmCell_(r, 13);
    if (pr && priorities.indexOf(pr) === -1) priorities.push(pr);
    const df = twmCell_(r, 14);
    if (df && difficulties.indexOf(df) === -1) difficulties.push(df);
  }

  const projMap = {};
  for (let i = 0; i < worklogs.length; i++) {
    if (worklogs[i].project) projMap[worklogs[i].project] = true;
  }
  const projects = Object.keys(projMap).sort(function (a, b) {
    return a.toLowerCase().localeCompare(b.toLowerCase());
  });

  return {
    staff: staff,
    departments: departments.length ? departments : ["Management", "Support", "Marketing", "Programming", "Admin", "CSP", "Kitchen"],
    priorities: priorities.length ? priorities : ["Low", "Medium", "High"],
    difficulties: difficulties.length ? difficulties : ["Very Difficult", "Difficult", "Moderate", "Easy", "Very Easy"],
    task_statuses: taskStatuses.length ? taskStatuses : ["On Schedule", "Behind Schedule", "Handover", "Abandoned"],
    worklog_statuses: ["On-going", "Complete"],
    projects: projects
  };
}

function twmParseDate_(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    if (!isNaN(d.getTime())) return d;
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
  throw new Error("Invalid date '" + s + "'. Expected YYYY-MM-DD or MM/DD/YYYY.");
}

function twmPad2_(n) {
  return (n < 10 ? "0" : "") + String(n);
}

function twmFormatDateTasks_(d) {
  if (!d) return "";
  return twmPad2_(d.getMonth() + 1) + "/" + twmPad2_(d.getDate()) + "/" + d.getFullYear();
}

function twmFormatDateWorklog_(d) {
  if (!d) return "";
  return twmPad2_(d.getMonth() + 1) + "/" + twmPad2_(d.getDate()) + "/" + String(d.getFullYear()).slice(-2);
}

function twmNormalizeDateKey_(raw) {
  const d = twmParseDate_(raw);
  if (!d) return "";
  return d.getFullYear() + "-" + twmPad2_(d.getMonth() + 1) + "-" + twmPad2_(d.getDate());
}

function twmTodayDateOnly_() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function twmParseNumber_(raw, label, allowBlank) {
  const s = raw === null || raw === undefined ? "" : String(raw).trim();
  if (!s) {
    if (allowBlank) return null;
    throw new Error(label + " is required.");
  }
  const n = Number(s);
  if (!isFinite(n)) throw new Error(label + " must be a valid number.");
  return n;
}

function twmParsePct_(raw) {
  if (raw === null || raw === undefined || String(raw).trim() === "") return 0;
  const s = String(raw).trim().replace(/%$/, "").trim();
  const n = Number(s);
  if (!isFinite(n) || n < 0 || n > 100) {
    throw new Error("Completion percentage must be between 0 and 100.");
  }
  return n;
}

function twmFormatNum_(val) {
  if (val === null || val === undefined) return "";
  return val % 1 === 0 ? val : Math.round(val * 10000) / 10000;
}

function twmNormalizeWorklogStatus_(raw) {
  const v = String(raw || "").trim().toLowerCase();
  if (v === "complete" || v === "completed") return "Complete";
  if (v === "on-going" || v === "ongoing" || v === "in progress") return "On-going";
  throw new Error("Status must be 'On-going' or 'Complete'.");
}

function twmNormalizePerfMonth_(yearRaw, monthRaw) {
  const yStr = String(yearRaw || "").trim();
  const mStr = String(monthRaw || "").trim();
  if (!/^\d{4}$/.test(yStr)) return null;
  let mIdx = -1;
  for (let i = 0; i < TWM_MONTH_NAMES.length; i++) {
    if (TWM_MONTH_NAMES[i].toLowerCase() === mStr.toLowerCase()) { mIdx = i; break; }
  }
  if (mIdx < 0 && /^\d{1,2}$/.test(mStr)) {
    const n = Number(mStr);
    if (n >= 1 && n <= 12) mIdx = n - 1;
  }
  if (mIdx < 0) return null;
  const monthNum = mIdx + 1;
  const canonicalMonth = TWM_MONTH_NAMES[mIdx];
  const key = yStr + "-" + twmPad2_(monthNum);
  return {
    year: yStr,
    month: canonicalMonth,
    month_num: monthNum,
    key: key,
    label: canonicalMonth + " " + yStr,
    sort_val: Number(yStr) * 100 + monthNum
  };
}

function twmExtractPerfYears_(dataRows, monthsList) {
  const seen = {};
  const curYear = new Date().getFullYear();
  seen[String(curYear - 1)] = true;
  seen[String(curYear)] = true;
  seen[String(curYear + 1)] = true;
  for (let i = 0; i < dataRows.length; i++) {
    const y = twmCell_(dataRows[i], 12);
    if (/^\d{4}$/.test(y)) seen[y] = true;
  }
  for (let i = 0; i < monthsList.length; i++) {
    if (/^\d{4}$/.test(monthsList[i].year)) seen[monthsList[i].year] = true;
  }
  return Object.keys(seen).sort(function (a, b) { return Number(b) - Number(a); });
}

function twmReadPerfStandard_(pSheet, dataRows) {
  if (!pSheet) {
    const now = new Date();
    return {
      has_tab: false,
      title: TWM_DEFAULT_PERF_TAB,
      max_row: 0,
      last_row: 2,
      years: twmExtractPerfYears_(dataRows || [], []),
      month_names: TWM_MONTH_NAMES.slice(),
      latest_month_key: "",
      next_year: String(now.getFullYear()),
      next_month: TWM_MONTH_NAMES[now.getMonth()],
      months: [],
      items: []
    };
  }
  const pMax = pSheet.getMaxRows();
  const pCols = Math.min(13, pSheet.getMaxColumns());
  const pRows = pMax > 0 ? pSheet.getRange(1, 1, pMax, pCols).getDisplayValues() : [];
  let headerIdx = -1;
  for (let i = 0; i < Math.min(5, pRows.length); i++) {
    if (twmCell_(pRows[i], 0).toLowerCase() === "year" && twmCell_(pRows[i], 1).toLowerCase() === "month") {
      headerIdx = i;
      break;
    }
  }
  const startOffset = headerIdx >= 0 ? headerIdx + 1 : 2;
  let lastRow = startOffset;
  for (let i = startOffset; i < pRows.length; i++) {
    for (let c = 0; c <= 11; c++) {
      if (twmCell_(pRows[i], c)) { lastRow = i + 1; break; }
    }
  }

  const hValidations = {};
  if (lastRow >= 3) {
    const dvs = pSheet.getRange(3, 8, lastRow - 2, 1).getDataValidations();
    for (let i = 0; i < dvs.length; i++) {
      const dv = dvs[i][0];
      if (dv && dv.getCriteriaType() === SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST) {
        const vals = (dv.getCriteriaValues()[0] || []).map(function (v) { return String(v).trim(); }).filter(Boolean);
        if (vals.length) hValidations[3 + i] = vals;
      }
    }
  }

  const items = [];
  const monthMap = {};
  let activeCriteria = "";
  let prevMkKey = "";
  for (let i = startOffset; i < pRows.length; i++) {
    const r = pRows[i];
    const rowNum = i + 1;
    const mk = twmNormalizePerfMonth_(twmCell_(r, 0), twmCell_(r, 1));
    if (!mk) continue;
    if (mk.key !== prevMkKey) { activeCriteria = ""; prevMkKey = mk.key; }
    const rawCrit = twmCell_(r, 2);
    if (rawCrit) activeCriteria = rawCrit;
    const weight = twmCell_(r, 5);
    const score = twmCell_(r, 8);
    const supScore = twmCell_(r, 10);
    items.push({
      row: rowNum,
      year: mk.year,
      month: mk.month,
      month_num: mk.month_num,
      month_key: mk.key,
      month_label: mk.label,
      criteria: rawCrit,
      criteria_group: activeCriteria,
      description: twmCell_(r, 3),
      source: twmCell_(r, 4),
      weight: weight,
      target: twmCell_(r, 6),
      achievement: twmCell_(r, 7),
      score: score,
      remarks: twmCell_(r, 9),
      supervisor_score: supScore,
      supervisor_remarks: twmCell_(r, 11),
      sf: twmCell_(r, 12),
      h_options: hValidations[rowNum] || []
    });
    if (!monthMap[mk.key]) {
      monthMap[mk.key] = {
        key: mk.key, year: mk.year, month: mk.month, month_num: mk.month_num,
        label: mk.label, sort_val: mk.sort_val, start_row: rowNum, end_row: rowNum,
        row_count: 0, total_weight: 0, self_score: 0, supervisor_score: 0, net_score: 0, is_latest: false
      };
    }
    const m = monthMap[mk.key];
    m.start_row = Math.min(m.start_row, rowNum);
    m.end_row = Math.max(m.end_row, rowNum);
    m.row_count += 1;
    m.total_weight += Number(weight) || 0;
    m.self_score += Number(score) || 0;
    m.supervisor_score += Number(supScore) || 0;
  }
  const months = Object.keys(monthMap).map(function (k) { return monthMap[k]; }).sort(function (a, b) {
    return b.sort_val - a.sort_val;
  });
  for (let i = 0; i < months.length; i++) {
    months[i].total_weight = Math.round(months[i].total_weight * 100) / 100;
    months[i].self_score = Math.round(months[i].self_score * 100) / 100;
    months[i].supervisor_score = Math.round(months[i].supervisor_score * 100) / 100;
    months[i].net_score = Math.round((months[i].self_score + months[i].supervisor_score) * 100) / 100;
  }
  if (months.length) months[0].is_latest = true;
  const latest = months[0] || null;
  const now = new Date();
  let nextYear = String(now.getFullYear());
  let nextMonth = TWM_MONTH_NAMES[now.getMonth()];
  if (latest) {
    if (latest.month_num === 12) { nextYear = String(Number(latest.year) + 1); nextMonth = TWM_MONTH_NAMES[0]; }
    else { nextYear = latest.year; nextMonth = TWM_MONTH_NAMES[latest.month_num]; }
  }
  return {
    has_tab: true,
    title: pSheet.getName(),
    max_row: pMax,
    last_row: lastRow,
    years: twmExtractPerfYears_(dataRows || [], months),
    month_names: TWM_MONTH_NAMES.slice(),
    latest_month_key: latest ? latest.key : "",
    next_year: nextYear,
    next_month: nextMonth,
    months: months,
    items: items
  };
}

function twmValidatePerfAch_(rawVal, hOptions, desc) {
  const s = rawVal === null || rawVal === undefined ? "" : String(rawVal).trim();
  if (s === "") return "";
  if (hOptions && hOptions.length > 0 && hOptions.indexOf(s) === -1) {
    throw new Error("Achievement for '" + (desc || "row") + "' must be one of: " + hOptions.join(", ") + ".");
  }
  const n = Number(s);
  if (!isFinite(n) || n < 0) {
    throw new Error("Achievement for '" + (desc || "row") + "' must be a valid non-negative number.");
  }
  return twmFormatNum_(n);
}

function twmBuildBootstrap_(params) {
  const ss = twmGetSpreadsheet_(params && (params.sheet_url || params.spreadsheet_id));
  const resolved = twmResolveSheets_(ss, params);
  const tSheet = resolved.tasksSheet;
  const wSheet = resolved.worklogSheet;
  const dSheet = resolved.dataSheet;
  const pSheet = resolved.perfSheet;

  const tMaxRow = tSheet.getMaxRows();
  const tCols = Math.min(26, tSheet.getMaxColumns());
  const wMaxRow = wSheet.getMaxRows();

  const metaRows = tSheet.getRange(5, 1, 3, tCols).getDisplayValues();
  const tRows = tMaxRow >= TWM_TASKS_START_ROW
    ? tSheet.getRange(TWM_TASKS_START_ROW, 1, tMaxRow - TWM_TASKS_START_ROW + 1, tCols).getDisplayValues()
    : [];
  const wRows = wMaxRow >= TWM_WORKLOG_START_ROW
    ? wSheet.getRange(TWM_WORKLOG_START_ROW, 1, wMaxRow - TWM_WORKLOG_START_ROW + 1, 7).getDisplayValues()
    : [];

  let dRows = [];
  if (dSheet) {
    const dRowsCount = Math.min(45, dSheet.getMaxRows());
    const dColsCount = Math.min(15, dSheet.getMaxColumns());
    if (dRowsCount > 0 && dColsCount > 0) {
      dRows = dSheet.getRange(1, 1, dRowsCount, dColsCount).getDisplayValues();
    }
  }

  const r5 = metaRows[0] || [];
  const r6 = metaRows[1] || [];
  const r7 = metaRows[2] || [];

  const tasks = [];
  let lastTaskRow = TWM_TASKS_START_ROW - 1;
  for (let i = 0; i < tRows.length; i++) {
    const rn = TWM_TASKS_START_ROW + i;
    if (twmTaskHasContent_(tRows[i])) {
      tasks.push(twmBuildTaskRecord_(rn, tRows[i]));
      lastTaskRow = rn;
    }
  }

  const worklogs = [];
  let lastWlRow = TWM_WORKLOG_START_ROW - 1;
  for (let i = 0; i < wRows.length; i++) {
    const rn = TWM_WORKLOG_START_ROW + i;
    let hasAny = false;
    for (let c = 1; c <= 6; c++) {
      if (twmCell_(wRows[i], c)) {
        hasAny = true;
        break;
      }
    }
    if (hasAny) {
      worklogs.push(twmBuildWorklogRecord_(rn, wRows[i]));
      lastWlRow = rn;
    }
  }

  const sid = ss.getId();
  const tGid = tSheet.getSheetId();
  const sheetUrl = "https://docs.google.com/spreadsheets/d/" + sid + "/edit?gid=" + tGid + "#gid=" + tGid;

  return {
    ok: true,
    needs_setup: false,
    spreadsheet: {
      id: sid,
      title: ss.getName(),
      url: sheetUrl,
      tasks_sheet: tSheet.getName(),
      tasks_max_row: tMaxRow,
      last_task_row: lastTaskRow,
      worklog_sheet: wSheet.getName(),
      worklog_max_row: wMaxRow,
      last_worklog_row: lastWlRow
    },
    setup: {
      mode: "gas",
      spreadsheet_id: sid,
      url: sheetUrl,
      tasks_tab: tSheet.getName(),
      worklog_tab: wSheet.getName(),
      data_tab: dSheet ? dSheet.getName() : TWM_DEFAULT_DATA_TAB,
      perf_tab: pSheet ? pSheet.getName() : TWM_DEFAULT_PERF_TAB,
      sync_md_default: false
    },
    employee: {
      name: twmCell_(r5, 2),
      department: twmCell_(r6, 2),
      id: twmCell_(r7, 2),
      check_employee_all: twmCell_(r7, 21) ? twmCell_(r7, 21).toUpperCase() !== "FALSE" : true,
      check_supervisor_all: twmCell_(r7, 22) ? twmCell_(r7, 22).toUpperCase() !== "FALSE" : true,
      status_message_1: twmCell_(r5, 3),
      status_message_2: twmCell_(r6, 3)
    },
    dropdowns: twmExtractDropdowns_(dRows, worklogs),
    tasks: tasks,
    worklogs: worklogs,
    perf: twmReadPerfStandard_(pSheet, dRows)
  };
}

function twmProbeSetup_(params) {
  const ss = twmGetSpreadsheet_(params && (params.sheet_url || params.spreadsheet_id));
  const resolved = twmResolveSheets_(ss, params);
  const tSheet = resolved.tasksSheet;
  const metaRows = tSheet.getRange(5, 1, 3, 3).getDisplayValues();
  const empName = twmCell_(metaRows[0] || [], 2);
  const empDept = twmCell_(metaRows[1] || [], 2);
  const empId = twmCell_(metaRows[2] || [], 2);
  const sid = ss.getId();
  const tGid = tSheet.getSheetId();

  return {
    ok: true,
    spreadsheet_id: sid,
    title: ss.getName(),
    employee_name: empName,
    department: empDept,
    employee_id: empId,
    tasks_tab: tSheet.getName(),
    worklog_tab: resolved.worklogSheet.getName(),
    data_tab: resolved.dataSheet ? resolved.dataSheet.getName() : "",
    perf_tab: resolved.perfSheet ? resolved.perfSheet.getName() : "",
    available_tabs: resolved.availableTabs,
    url: "https://docs.google.com/spreadsheets/d/" + sid + "/edit?gid=" + tGid + "#gid=" + tGid
  };
}

function twmValidateTask_(payload) {
  const allowPartial = Boolean(payload.allow_partial);
  const entryD = twmParseDate_(payload.date_of_entry) || twmTodayDateOnly_();
  const detail = String(payload.detail || "").trim();
  const otherPerson = String(payload.other_person || "").trim();
  const otherDept = String(payload.other_department || "").trim();
  const priority = String(payload.priority || "").trim();
  const estStartD = twmParseDate_(payload.est_start_date);
  const actStartD = twmParseDate_(payload.act_start_date);
  const deadlineD = twmParseDate_(payload.expected_deadline);
  const completionD = twmParseDate_(payload.completion_date);

  const estTime = twmParseNumber_(payload.est_time, "Estimated Time (hours)", allowPartial);
  const actTime = twmParseNumber_(payload.act_time, "Actual Time (hours)", true);
  const pct = twmParsePct_(payload.pct_completed);
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
    if (missing.length) throw new Error("Required for Tasks validation: " + missing.join(", ") + ".");

    if (completionD) {
      const errs = [];
      if (!actStartD) errs.push("Actual Start Date");
      if (actTime === null || actTime <= 0) errs.push("Actual Time (> 0)");
      if (Math.abs(pct - 100) > 1e-6) errs.push("% Completed must be 100%");
      if (errs.length) throw new Error("When Completion Date is filled, Tasks sheet requires: " + errs.join(", ") + ".");
    }

    const todayD = twmTodayDateOnly_();
    const delayed = deadlineD && ((completionD && completionD > deadlineD) || (!completionD && todayD > deadlineD));
    if (delayed && !delayReason) {
      throw new Error("Delay Reason (Col O) is required because Completion Date (or Today) is past Expected Deadline.");
    }
  } else {
    if (estTime !== null && estTime < 0) throw new Error("Estimated Time cannot be negative.");
    if (actTime !== null && actTime < 0) throw new Error("Actual Time cannot be negative.");
  }

  const pctStr = String(payload.pct_completed !== undefined && payload.pct_completed !== null ? payload.pct_completed : "").trim() !== ""
    ? pct.toFixed(2) + "%"
    : "";

  return {
    B_to_M: [
      twmFormatDateTasks_(entryD),
      detail,
      otherPerson,
      otherDept,
      priority,
      twmFormatDateTasks_(estStartD),
      twmFormatDateTasks_(actStartD),
      twmFormatDateTasks_(deadlineD),
      twmFormatDateTasks_(completionD),
      twmFormatNum_(estTime),
      twmFormatNum_(actTime),
      pctStr
    ],
    O: delayReason,
    Q_to_U: [expectedOutcome, difficulty, remarks, supervisorComments, clarification]
  };
}

function twmWriteTaskRow_(tSheet, rowNum, prep) {
  tSheet.getRange(rowNum, 2, 1, 12).setValues([prep.B_to_M]);
  tSheet.getRange(rowNum, 15).setValue(prep.O);
  tSheet.getRange(rowNum, 17, 1, 5).setValues([prep.Q_to_U]);
}

function twmTaskSnapshotMatches_(existing, expected) {
  if (!expected) return true;
  return (
    (existing.detail || "") === (expected.detail || "") &&
    (existing.date_of_entry || "") === (expected.date_of_entry || "")
  );
}

function twmValidateWorklog_(payload) {
  const d = twmParseDate_(payload.date) || twmTodayDateOnly_();
  const dateStr = twmFormatDateWorklog_(d);
  const task = String(payload.task || "").trim();
  const project = String(payload.project || "").trim();
  const mins = twmParseNumber_(payload.time_minutes, "Time (minutes)", false);
  if (mins === null || mins < 0) throw new Error("Time (minutes) must be 0 or greater.");
  const status = twmNormalizeWorklogStatus_(payload.status || "Complete");
  const comments = String(payload.comments || "").trim();

  if (!task) throw new Error("Task description is required.");
  if (!project) throw new Error("Client / Project is required.");

  return [dateStr, task, project, twmFormatNum_(mins), status, comments];
}

function twmWorklogSnapshotMatches_(existing, expected) {
  if (!expected) return true;
  return (
    (existing.task || "") === (expected.task || "") &&
    (existing.project || "") === (expected.project || "") &&
    twmNormalizeDateKey_(existing.date || "") === twmNormalizeDateKey_(expected.date || "")
  );
}

function doGet(e) {
  try {
    const params = (e && e.parameter) || {};
    if (!twmCheckPin_(params.pin)) {
      return twmJsonOut_({ ok: false, error: "Invalid PIN" });
    }
    const action = params.action || "bootstrap";
    if (action === "setup_test") {
      return twmJsonOut_(twmProbeSetup_(params));
    }
    return twmJsonOut_(twmBuildBootstrap_(params));
  } catch (err) {
    return twmJsonOut_({ ok: false, error: err && err.message ? err.message : String(err) });
  }
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    const payload = JSON.parse((e && e.postData && e.postData.contents) || "{}");
    if (!twmCheckPin_(payload.pin)) {
      return twmJsonOut_({ ok: false, error: "Invalid PIN" });
    }

    const action = payload.action || "bootstrap";
    if (action === "bootstrap") {
      return twmJsonOut_(twmBuildBootstrap_(payload));
    }
    if (action === "setup_test") {
      return twmJsonOut_(twmProbeSetup_(payload));
    }

    lock.waitLock(15000);
    const ss = twmGetSpreadsheet_(payload.sheet_url || payload.spreadsheet_id);
    const resolved = twmResolveSheets_(ss, payload);
    const tSheet = resolved.tasksSheet;
    const wSheet = resolved.worklogSheet;

    if (action === "create_task") {
      const prep = twmValidateTask_(payload);
      const tMax = tSheet.getMaxRows();
      const rows = tSheet.getRange(TWM_TASKS_START_ROW, 1, tMax - TWM_TASKS_START_ROW + 1, 21).getDisplayValues();
      let lastUsed = TWM_TASKS_START_ROW - 1;
      for (let i = 0; i < rows.length; i++) {
        if (twmTaskHasContent_(rows[i])) lastUsed = TWM_TASKS_START_ROW + i;
      }
      const targetRow = lastUsed + 1;
      if (targetRow > tMax) {
        return twmJsonOut_({ ok: false, error: "Tasks sheet has reached its template limit of row " + tMax + "." });
      }
      twmWriteTaskRow_(tSheet, targetRow, prep);
      SpreadsheetApp.flush();
      const boot = twmBuildBootstrap_(payload);
      boot.affected_row = targetRow;
      return twmJsonOut_(boot);
    }

    if (action === "update_task") {
      const rowNum = Number(payload.row);
      const tMax = tSheet.getMaxRows();
      if (!rowNum || rowNum < TWM_TASKS_START_ROW || rowNum > tMax) {
        return twmJsonOut_({ ok: false, error: "Task row must be between " + TWM_TASKS_START_ROW + " and " + tMax + "." });
      }
      const prep = twmValidateTask_(payload);
      const currRow = tSheet.getRange(rowNum, 1, 1, 26).getDisplayValues()[0] || [];
      if (!twmTaskSnapshotMatches_(twmBuildTaskRecord_(rowNum, currRow), payload.expected_snapshot)) {
        return twmJsonOut_({ ok: false, status: 409, error: "This task row changed in Google Sheets since you loaded it. Refreshing latest data." });
      }
      twmWriteTaskRow_(tSheet, rowNum, prep);
      SpreadsheetApp.flush();
      const boot = twmBuildBootstrap_(payload);
      boot.affected_row = rowNum;
      return twmJsonOut_(boot);
    }

    if (action === "delete_task") {
      const rowNum = Number(payload.row);
      const tMax = tSheet.getMaxRows();
      if (!rowNum || rowNum < TWM_TASKS_START_ROW || rowNum > tMax) {
        return twmJsonOut_({ ok: false, error: "Task row must be between " + TWM_TASKS_START_ROW + " and " + tMax + "." });
      }
      const rows = tSheet.getRange(TWM_TASKS_START_ROW, 1, tMax - TWM_TASKS_START_ROW + 1, 21).getDisplayValues();
      const currRow = rows[rowNum - TWM_TASKS_START_ROW] || [];
      if (!twmTaskHasContent_(currRow)) {
        return twmJsonOut_({ ok: false, error: "Task row " + rowNum + " is already empty." });
      }
      if (!twmTaskSnapshotMatches_(twmBuildTaskRecord_(rowNum, currRow), payload.expected_snapshot)) {
        return twmJsonOut_({ ok: false, status: 409, error: "This task row changed in Google Sheets since you loaded it. Refreshing latest data." });
      }
      let lastUsed = rowNum;
      for (let i = 0; i < rows.length; i++) {
        if (twmTaskHasContent_(rows[i])) lastUsed = Math.max(lastUsed, TWM_TASKS_START_ROW + i);
      }
      if (rowNum < lastUsed) {
        const shiftBM = [];
        const shiftO = [];
        const shiftQU = [];
        for (let rn = rowNum + 1; rn <= lastUsed; rn++) {
          const r = rows[rn - TWM_TASKS_START_ROW] || [];
          const bm = [];
          for (let c = 1; c <= 12; c++) bm.push(twmCell_(r, c));
          shiftBM.push(bm);
          shiftO.push([twmCell_(r, 14)]);
          const qu = [];
          for (let c = 16; c <= 20; c++) qu.push(twmCell_(r, c));
          shiftQU.push(qu);
        }
        const count = lastUsed - rowNum;
        tSheet.getRange(rowNum, 2, count, 12).setValues(shiftBM);
        tSheet.getRange(rowNum, 15, count, 1).setValues(shiftO);
        tSheet.getRange(rowNum, 17, count, 5).setValues(shiftQU);
      }
      tSheet.getRange(lastUsed, 2, 1, 12).clearContent();
      tSheet.getRange(lastUsed, 15, 1, 1).clearContent();
      tSheet.getRange(lastUsed, 17, 1, 5).clearContent();
      SpreadsheetApp.flush();
      const boot = twmBuildBootstrap_(payload);
      boot.deleted_row = rowNum;
      return twmJsonOut_(boot);
    }

    if (action === "create_worklog") {
      const rowValues = twmValidateWorklog_(payload);
      const wMax = wSheet.getMaxRows();
      const rows = wSheet.getRange(TWM_WORKLOG_START_ROW, 1, wMax - TWM_WORKLOG_START_ROW + 1, 7).getDisplayValues();
      let lastUsed = TWM_WORKLOG_START_ROW - 1;
      for (let i = 0; i < rows.length; i++) {
        for (let c = 1; c <= 6; c++) {
          if (twmCell_(rows[i], c)) {
            lastUsed = TWM_WORKLOG_START_ROW + i;
            break;
          }
        }
      }
      const targetRow = lastUsed + 1;
      if (targetRow > wMax) {
        wSheet.insertRowsAfter(wMax, 100);
      }
      wSheet.getRange(targetRow, 2, 1, 6).setValues([rowValues]);
      SpreadsheetApp.flush();
      const boot = twmBuildBootstrap_(payload);
      boot.affected_row = targetRow;
      return twmJsonOut_(boot);
    }

    if (action === "update_worklog") {
      const rowNum = Number(payload.row);
      const wMax = wSheet.getMaxRows();
      if (!rowNum || rowNum < TWM_WORKLOG_START_ROW || rowNum > wMax) {
        return twmJsonOut_({ ok: false, error: "Work log row must be between " + TWM_WORKLOG_START_ROW + " and " + wMax + "." });
      }
      const rowValues = twmValidateWorklog_(payload);
      const currRow = wSheet.getRange(rowNum, 1, 1, 7).getDisplayValues()[0] || [];
      if (!twmWorklogSnapshotMatches_(twmBuildWorklogRecord_(rowNum, currRow), payload.expected_snapshot)) {
        return twmJsonOut_({ ok: false, status: 409, error: "This work log row changed in Google Sheets since you loaded it. Refreshing latest data." });
      }
      wSheet.getRange(rowNum, 2, 1, 6).setValues([rowValues]);
      SpreadsheetApp.flush();
      const boot = twmBuildBootstrap_(payload);
      boot.affected_row = rowNum;
      return twmJsonOut_(boot);
    }

    if (action === "delete_worklog") {
      const rowNum = Number(payload.row);
      const wMax = wSheet.getMaxRows();
      if (!rowNum || rowNum < TWM_WORKLOG_START_ROW || rowNum > wMax) {
        return twmJsonOut_({ ok: false, error: "Work log row must be between " + TWM_WORKLOG_START_ROW + " and " + wMax + "." });
      }
      const rows = wSheet.getRange(TWM_WORKLOG_START_ROW, 1, wMax - TWM_WORKLOG_START_ROW + 1, 7).getDisplayValues();
      const currRow = rows[rowNum - TWM_WORKLOG_START_ROW] || [];
      let nonEmpty = false;
      for (let c = 1; c <= 6; c++) {
        if (twmCell_(currRow, c)) nonEmpty = true;
      }
      if (!nonEmpty) {
        return twmJsonOut_({ ok: false, error: "Work log row " + rowNum + " is already empty." });
      }
      if (!twmWorklogSnapshotMatches_(twmBuildWorklogRecord_(rowNum, currRow), payload.expected_snapshot)) {
        return twmJsonOut_({ ok: false, status: 409, error: "This work log row changed in Google Sheets since you loaded it. Refreshing latest data." });
      }
      let lastUsed = rowNum;
      for (let i = 0; i < rows.length; i++) {
        for (let c = 1; c <= 6; c++) {
          if (twmCell_(rows[i], c)) {
            lastUsed = Math.max(lastUsed, TWM_WORKLOG_START_ROW + i);
            break;
          }
        }
      }
      if (rowNum < lastUsed) {
        const shiftRows = [];
        for (let rn = rowNum + 1; rn <= lastUsed; rn++) {
          const r = rows[rn - TWM_WORKLOG_START_ROW] || [];
          const outR = [];
          for (let c = 1; c <= 6; c++) outR.push(twmCell_(r, c));
          shiftRows.push(outR);
        }
        wSheet.getRange(rowNum, 2, lastUsed - rowNum, 6).setValues(shiftRows);
      }
      wSheet.getRange(lastUsed, 2, 1, 6).clearContent();
      SpreadsheetApp.flush();
      const boot = twmBuildBootstrap_(payload);
      boot.deleted_row = rowNum;
      return twmJsonOut_(boot);
    }

    if (action === "create_perf_month") {
      const pSheet = resolved.perfSheet;
      if (!pSheet) throw new Error("Perf Standard tab was not found in this Google Sheet.");
      const targetMk = twmNormalizePerfMonth_(payload.year, payload.month);
      if (!targetMk) throw new Error("Valid 4-digit Year and Month name are required.");
      const srcMk = twmNormalizePerfMonth_(payload.source_year, payload.source_month);
      if (!srcMk) throw new Error("Source Year and Month to copy from are required.");

      const perfState = twmReadPerfStandard_(pSheet, []);
      for (let i = 0; i < perfState.months.length; i++) {
        if (perfState.months[i].key === targetMk.key) {
          return twmJsonOut_({ ok: false, status: 409, error: "Perf Standard entry for " + targetMk.label + " already exists." });
        }
      }
      const srcRows = perfState.items.filter(function (it) { return it.month_key === srcMk.key; })
        .sort(function (a, b) { return a.row - b.row; });
      if (!srcRows.length) throw new Error("No rows found for source month " + srcMk.label + ".");

      const dstStart = perfState.last_row + 1;
      const dstEnd = dstStart + srcRows.length - 1;
      const pMax = pSheet.getMaxRows();
      if (dstEnd > pMax) pSheet.insertRowsAfter(pMax, Math.max(100, dstEnd - pMax));

      for (let i = 0; i < srcRows.length; i++) {
        pSheet.getRange(srcRows[i].row, 1, 1, 13).copyTo(
          pSheet.getRange(dstStart + i, 1, 1, 13),
          SpreadsheetApp.CopyPasteType.PASTE_NORMAL,
          false
        );
      }

      const providedAch = Array.isArray(payload.achievements) ? payload.achievements : null;
      const yearNum = Number(targetMk.year);
      const abVals = [];
      const hVals = [];
      const jlVals = [];
      for (let i = 0; i < srcRows.length; i++) {
        abVals.push([isFinite(yearNum) ? yearNum : targetMk.year, targetMk.month]);
        const rawAch = providedAch && i < providedAch.length ? providedAch[i] : "";
        hVals.push([twmValidatePerfAch_(rawAch, srcRows[i].h_options, srcRows[i].description)]);
        jlVals.push(["", "", ""]);
      }
      pSheet.getRange(dstStart, 1, srcRows.length, 2).setValues(abVals);
      pSheet.getRange(dstStart, 8, srcRows.length, 1).setValues(hVals);
      pSheet.getRange(dstStart, 10, srcRows.length, 3).setValues(jlVals);
      SpreadsheetApp.flush();
      const boot = twmBuildBootstrap_(payload);
      boot.created_month_key = targetMk.key;
      return twmJsonOut_(boot);
    }

    if (action === "update_perf_achievements") {
      const pSheet = resolved.perfSheet;
      if (!pSheet) throw new Error("Perf Standard tab was not found in this Google Sheet.");
      const targetMk = twmNormalizePerfMonth_(payload.year, payload.month);
      if (!targetMk) throw new Error("Valid Year and Month are required.");
      const updatesList = Array.isArray(payload.updates) ? payload.updates : [];
      if (!updatesList.length) throw new Error("At least one Achievement update is required.");

      const perfState = twmReadPerfStandard_(pSheet, []);
      const monthObj = perfState.months.filter(function (m) { return m.key === targetMk.key; })[0];
      if (!monthObj) throw new Error("Perf Standard month " + targetMk.label + " was not found.");
      if (monthObj.key !== perfState.latest_month_key) {
        const latestObj = perfState.months[0];
        throw new Error("Past months are view-only. Only the latest month (" + (latestObj ? latestObj.label : perfState.latest_month_key) + ") can edit Achievement.");
      }

      const byRow = {};
      perfState.items.filter(function (it) { return it.month_key === monthObj.key; }).forEach(function (it) {
        byRow[it.row] = it;
      });
      for (let i = 0; i < updatesList.length; i++) {
        const u = updatesList[i];
        const rn = Number(u.row);
        const rec = byRow[rn];
        if (!rec) {
          return twmJsonOut_({ ok: false, status: 409, error: "Row " + rn + " does not belong to " + monthObj.label + "." });
        }
        if (u.expected_description !== undefined && rec.description !== String(u.expected_description).trim()) {
          return twmJsonOut_({ ok: false, status: 409, error: "Perf Standard row " + rn + " changed externally." });
        }
        const cleaned = twmValidatePerfAch_(u.achievement, rec.h_options, rec.description);
        pSheet.getRange(rn, 8).setValue(cleaned);
      }
      SpreadsheetApp.flush();
      return twmJsonOut_(twmBuildBootstrap_(payload));
    }

    return twmJsonOut_({ ok: false, error: "Unknown action: " + action });
  } catch (err) {
    return twmJsonOut_({ ok: false, error: err && err.message ? err.message : String(err) });
  } finally {
    try {
      lock.releaseLock();
    } catch (_) {}
  }
}
