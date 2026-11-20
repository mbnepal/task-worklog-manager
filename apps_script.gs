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
const TWM_TASKS_GID = 697912632;
const TWM_WORKLOG_GID = 1436624543;
const TWM_DATA_GID = 1258735725;
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
    if (!active) throw new Error("No active spreadsheet found. Paste a Google Sheet link or bind this script to your sheet.");
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

  return {
    tasksSheet: tasksSheet,
    worklogSheet: worklogSheet,
    dataSheet: dataSheet,
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

function twmBuildBootstrap_(params) {
  const ss = twmGetSpreadsheet_(params && (params.sheet_url || params.spreadsheet_id));
  const resolved = twmResolveSheets_(ss, params);
  const tSheet = resolved.tasksSheet;
  const wSheet = resolved.worklogSheet;
  const dSheet = resolved.dataSheet;

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
    worklogs: worklogs
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

    return twmJsonOut_({ ok: false, error: "Unknown action: " + action });
  } catch (err) {
    return twmJsonOut_({ ok: false, error: err && err.message ? err.message : String(err) });
  } finally {
    try {
      lock.releaseLock();
    } catch (_) {}
  }
}
