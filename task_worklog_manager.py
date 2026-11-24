"""
Tasks & Work Log Manager — Local Web CRUD App for Google Sheets

Features:
- Full Create, Edit, and Safe Delete on both `Tasks` and `Work log` tabs.
- Multi-sheet Setup modal so any team member can connect their own Google Sheet link.
- Never overwrites sheet-wide ARRAYFORMULA columns in `Tasks` (A, N, P, V, W) or `Work log` (A).
- Safe row deletion shifts editable columns up and clears the vacated row without deleteDimension,
  keeping row-9 / row-10 ARRAYFORMULAs, V7/W7 checks, Report VLOOKUPs, and validations intact.
- Optimistic concurrency snapshot check on Update/Delete to avoid modifying stale row indices.
- Optional incremental sync to local `Work_Log_History.md`.
"""

import argparse
import glob
import json
import os
import re
import sys
import threading
import urllib.parse
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_DIR = os.path.join(BASE_DIR, "config")
DEFAULT_PORT = int(os.environ.get("TASK_MANAGER_PORT", "8766"))

DEFAULT_TASKS_TAB = "Tasks"
DEFAULT_WORKLOG_TAB = "Work log"
DEFAULT_DATA_TAB = "Data"
DEFAULT_PERF_TAB = "Perf Standard"
TASKS_GID = 697912632
WORKLOG_GID = 1436624543
DATA_GID = 1258735725
PERF_GID = 1601402663
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TOKEN_FILE = os.environ.get("TASK_MANAGER_TOKEN", os.path.join(DEFAULT_CONFIG_DIR, "token.json"))
CONFIG_FILE = os.environ.get("TASK_MANAGER_CONFIG", os.path.join(DEFAULT_CONFIG_DIR, "task_worklog_config.json"))
WORK_LOG_MD_FILE = os.environ.get("TASK_MANAGER_WORKLOG_MD", os.path.join(BASE_DIR, "Work_Log_History.md"))
UI_HTML_FILE = os.path.join(BASE_DIR, "index.html")
BROWSER_JS_FILE = os.path.join(BASE_DIR, "sheets_browser.js")

TASKS_START_ROW = 9
WORKLOG_START_ROW = 10

sheets_lock = threading.Lock()
md_lock = threading.Lock()
config_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "BAD_REQUEST"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def resolve_credentials_file() -> str:
    env_path = os.environ.get("TASK_MANAGER_CREDENTIALS", "").strip()
    if env_path:
        return env_path if os.path.isfile(env_path) else ""
    preferred = os.path.join(DEFAULT_CONFIG_DIR, "client_secret.json")
    if os.path.isfile(preferred):
        return preferred
    for folder in (DEFAULT_CONFIG_DIR, BASE_DIR):
        matches = sorted(glob.glob(os.path.join(folder, "client_secret*.json")))
        if matches:
            return matches[0]
    return ""


def parse_spreadsheet_id(link_or_id: str) -> str:
    s = (link_or_id or "").strip()
    if not s:
        raise ApiError("Google Sheet link or Spreadsheet ID is required.")
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]{20,}", s):
        return s
    raise ApiError("Invalid Google Sheet link or Spreadsheet ID. Paste the full Google Sheets URL or its ID.")


def normalize_role_mode(val) -> str:
    return "supervisor" if str(val or "").strip().lower() == "supervisor" else "employee"


def require_employee_mode(cfg: dict, action: str = "modify records"):
    if normalize_role_mode(cfg.get("role_mode")) == "supervisor":
        raise ApiError(
            f"Supervision mode only allows adding Supervisor comments; cannot {action}.",
            status=403,
            code="SUPERVISOR_READ_ONLY",
        )


def require_supervisor_mode(cfg: dict, field_label: str = "Supervisor comments"):
    if normalize_role_mode(cfg.get("role_mode")) != "supervisor":
        raise ApiError(
            f"Enable Supervision mode in Setup to edit {field_label}.",
            status=403,
            code="SUPERVISOR_ONLY",
        )


def default_config() -> dict:
    return {
        "role_mode": "employee",
        "spreadsheet_id": "",
        "tasks_tab": DEFAULT_TASKS_TAB,
        "worklog_tab": DEFAULT_WORKLOG_TAB,
        "data_tab": DEFAULT_DATA_TAB,
        "perf_tab": DEFAULT_PERF_TAB,
        "sync_md_default": False,
        "saved_sheets": [],
    }


def load_config() -> dict:
    with config_lock:
        cfg = default_config()
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    cfg["role_mode"] = normalize_role_mode(raw.get("role_mode"))
                    cfg["spreadsheet_id"] = (raw.get("spreadsheet_id") or "").strip()
                    cfg["tasks_tab"] = (raw.get("tasks_tab") or DEFAULT_TASKS_TAB).strip()
                    cfg["worklog_tab"] = (raw.get("worklog_tab") or DEFAULT_WORKLOG_TAB).strip()
                    cfg["data_tab"] = (raw.get("data_tab") or DEFAULT_DATA_TAB).strip()
                    cfg["perf_tab"] = (raw.get("perf_tab") or DEFAULT_PERF_TAB).strip()
                    cfg["sync_md_default"] = bool(raw.get("sync_md_default", False))
                    if isinstance(raw.get("saved_sheets"), list):
                        cfg["saved_sheets"] = raw["saved_sheets"]
            except Exception:
                pass
        return cfg


def save_config(cfg: dict):
    with config_lock:
        parent = os.path.dirname(CONFIG_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)


def upsert_saved_sheet(cfg: dict, entry: dict):
    saved = [s for s in cfg.get("saved_sheets", []) if s.get("spreadsheet_id") != entry["spreadsheet_id"]]
    saved.insert(0, entry)
    cfg["saved_sheets"] = saved[:20]


def get_credentials(force_reauth: bool = False) -> Credentials:
    creds = None
    if not force_reauth and os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if force_reauth or not creds or not creds.valid:
        if not force_reauth and creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = None
        if force_reauth or not creds:
            cred_file = resolve_credentials_file()
            if not cred_file:
                raise ApiError(
                    "Google OAuth credentials file not found. Save your Desktop App OAuth JSON as config/client_secret.json (see README.md).",
                    status=400,
                    code="MISSING_CREDENTIALS",
                )
            flow = InstalledAppFlow.from_client_secrets_file(cred_file, SCOPES)
            kwargs = {"prompt": "select_account"} if force_reauth else {}
            creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=120, **kwargs)
            if not creds:
                raise ApiError("Google sign-in timed out or was cancelled.", status=400)
        token_dir = os.path.dirname(TOKEN_FILE)
        if token_dir:
            os.makedirs(token_dir, exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())
    return creds


def get_sheets_service():
    creds = get_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def quote_sheet(title: str) -> str:
    escaped = title.replace("'", "''")
    return f"'{escaped}'"


def parse_date_value(raw: str) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ApiError(f"Invalid date '{s}'. Expected YYYY-MM-DD or MM/DD/YYYY.")


def format_date_tasks(raw: str) -> str:
    d = parse_date_value(raw)
    return d.strftime("%m/%d/%Y") if d else ""


def format_date_worklog(raw: str) -> str:
    d = parse_date_value(raw)
    return d.strftime("%m/%d/%y") if d else ""


def normalize_date_key(raw: str) -> str:
    d = parse_date_value(raw)
    return d.isoformat() if d else ""


def normalize_worklog_status(raw: str) -> str:
    val = (raw or "").strip().lower()
    if val in ("complete", "completed"):
        return "Complete"
    if val in ("on-going", "ongoing", "in progress"):
        return "On-going"
    raise ApiError("Status must be 'On-going' or 'Complete'.")


def parse_pct(raw) -> float:
    if raw is None or str(raw).strip() == "":
        return 0.0
    s = str(raw).strip().rstrip("%").strip()
    try:
        val = float(s)
    except ValueError as exc:
        raise ApiError(f"Invalid completion percentage '{raw}'.") from exc
    if val < 0 or val > 100:
        raise ApiError("Completion percentage must be between 0 and 100.")
    return val


def parse_number(raw, label: str, allow_blank: bool = True) -> float | None:
    s = "" if raw is None else str(raw).strip()
    if not s:
        if allow_blank:
            return None
        raise ApiError(f"{label} is required.")
    try:
        return float(s)
    except ValueError as exc:
        raise ApiError(f"{label} must be a valid number.") from exc


def format_num_cell(val: float | None) -> str | int | float:
    if val is None:
        return ""
    if val.is_integer():
        return int(val)
    return round(val, 4)


def cell(row: list, idx: int) -> str:
    if idx < len(row) and row[idx] is not None:
        return str(row[idx]).strip()
    return ""


def _find_sheet_entry(sheets_list: list[dict], preferred_name: str, fallback_gid: int) -> dict | None:
    target = (preferred_name or "").strip().lower()
    for s in sheets_list:
        props = s.get("properties", {})
        if props.get("title", "").strip().lower() == target:
            return {
                "sheetId": props.get("sheetId"),
                "title": props.get("title", ""),
                "rowCount": props.get("gridProperties", {}).get("rowCount", 150),
                "columnCount": props.get("gridProperties", {}).get("columnCount", 26),
            }
    for s in sheets_list:
        props = s.get("properties", {})
        if props.get("sheetId") == fallback_gid:
            return {
                "sheetId": props.get("sheetId"),
                "title": props.get("title", ""),
                "rowCount": props.get("gridProperties", {}).get("rowCount", 150),
                "columnCount": props.get("gridProperties", {}).get("columnCount", 26),
            }
    return None


def get_sheet_map(
    service,
    spreadsheet_id: str,
    tasks_tab: str = DEFAULT_TASKS_TAB,
    worklog_tab: str = DEFAULT_WORKLOG_TAB,
    data_tab: str = DEFAULT_DATA_TAB,
    perf_tab: str = DEFAULT_PERF_TAB,
) -> dict:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, includeGridData=False).execute()
    sheets_list = meta.get("sheets", [])
    available = [s.get("properties", {}).get("title", "") for s in sheets_list]

    tasks_entry = _find_sheet_entry(sheets_list, tasks_tab, TASKS_GID)
    if not tasks_entry:
        raise ApiError(
            f"Could not find tab '{tasks_tab}' in this spreadsheet. Available tabs: {', '.join(available)}",
            status=400,
            code="MISSING_TAB",
        )

    worklog_entry = _find_sheet_entry(sheets_list, worklog_tab, WORKLOG_GID)
    if not worklog_entry:
        raise ApiError(
            f"Could not find tab '{worklog_tab}' in this spreadsheet. Available tabs: {', '.join(available)}",
            status=400,
            code="MISSING_TAB",
        )

    data_entry = _find_sheet_entry(sheets_list, data_tab, DATA_GID)
    perf_entry = _find_sheet_entry(sheets_list, perf_tab, PERF_GID)

    return {
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_title": meta.get("properties", {}).get("title", "Employee Reporting"),
        "available_tabs": available,
        "tasks": tasks_entry,
        "worklog": worklog_entry,
        "data": data_entry,
        "perf": perf_entry,
    }


def extract_dropdowns(data_rows: list[list], worklog_rows: list[dict]) -> dict:
    staff = []
    departments = []
    priorities = []
    difficulties = []
    task_statuses = []

    for ri, r in enumerate(data_rows):
        hr_id = cell(r, 1)
        name = cell(r, 2)
        desig = cell(r, 4)
        if ri >= 1 and name and (hr_id or desig) and name.lower() != "office assistant":
            if name not in staff:
                staff.append(name)

        dept = cell(r, 9)
        if ri >= 1 and dept and dept not in departments:
            departments.append(dept)

        status = cell(r, 8)
        if status and status not in task_statuses:
            task_statuses.append(status)

        prio = cell(r, 13)
        if prio and prio not in priorities:
            priorities.append(prio)

        diff = cell(r, 14)
        if diff and diff not in difficulties:
            difficulties.append(diff)

    projects = sorted({w["project"] for w in worklog_rows if w["project"]}, key=lambda x: x.lower())

    return {
        "staff": staff,
        "departments": departments or ["Management", "Support", "Marketing", "Programming", "Admin", "CSP", "Kitchen"],
        "priorities": priorities or ["Low", "Medium", "High"],
        "difficulties": difficulties or ["Very Difficult", "Difficult", "Moderate", "Easy", "Very Easy"],
        "task_statuses": task_statuses or ["On Schedule", "Behind Schedule", "Handover", "Abandoned"],
        "worklog_statuses": ["On-going", "Complete"],
        "projects": projects,
    }


def build_task_record(row_num: int, r: list) -> dict:
    return {
        "row": row_num,
        "sn": cell(r, 0),
        "date_of_entry": cell(r, 1),
        "detail": cell(r, 2),
        "other_person": cell(r, 3),
        "other_department": cell(r, 4),
        "priority": cell(r, 5),
        "est_start_date": cell(r, 6),
        "act_start_date": cell(r, 7),
        "expected_deadline": cell(r, 8),
        "completion_date": cell(r, 9),
        "est_time": cell(r, 10),
        "act_time": cell(r, 11),
        "pct_completed": cell(r, 12),
        "status_of_task": cell(r, 13),
        "delay_reason": cell(r, 14),
        "delay_days": cell(r, 15),
        "expected_outcome": cell(r, 16),
        "difficulty": cell(r, 17),
        "remarks": cell(r, 18),
        "supervisor_comments": cell(r, 19),
        "clarification": cell(r, 20),
        "check_employee": cell(r, 21),
        "check_supervisor": cell(r, 22),
        "last_updated_at": cell(r, 24),
    }


def task_has_user_content(r: list) -> bool:
    editable_indices = list(range(1, 13)) + [14] + list(range(16, 21))
    return any(bool(cell(r, i)) for i in editable_indices)


def build_worklog_record(row_num: int, r: list) -> dict:
    return {
        "row": row_num,
        "sn": cell(r, 0),
        "date": cell(r, 1),
        "task": cell(r, 2),
        "project": cell(r, 3),
        "time_minutes": cell(r, 4),
        "status": cell(r, 5),
        "comments": cell(r, 6),
    }


def normalize_perf_month(year_raw, month_raw) -> dict | None:
    y_str = str(year_raw or "").strip()
    m_str = str(month_raw or "").strip()
    if not re.fullmatch(r"\d{4}", y_str):
        return None
    m_idx = next((i for i, m in enumerate(MONTH_NAMES) if m.lower() == m_str.lower()), -1)
    if m_idx < 0 and re.fullmatch(r"\d{1,2}", m_str):
        n = int(m_str)
        if 1 <= n <= 12:
            m_idx = n - 1
    if m_idx < 0:
        return None
    month_num = m_idx + 1
    canonical = MONTH_NAMES[m_idx]
    return {
        "year": y_str,
        "month": canonical,
        "month_num": month_num,
        "key": f"{y_str}-{month_num:02d}",
        "label": f"{canonical} {y_str}",
        "sort_val": int(y_str) * 100 + month_num,
    }


def extract_perf_years(data_rows: list[list], months_list: list[dict]) -> list[str]:
    cur_y = date.today().year
    yrs = {str(cur_y - 1), str(cur_y), str(cur_y + 1)}
    for r in data_rows or []:
        y = cell(r, 12)
        if re.fullmatch(r"\d{4}", y):
            yrs.add(y)
    for m in months_list or []:
        if re.fullmatch(r"\d{4}", m.get("year", "")):
            yrs.add(m["year"])
    return sorted(yrs, key=int, reverse=True)


def parse_perf_standard_data(
    p_rows: list[list],
    data_rows: list[list] | None = None,
    h_validations: dict[int, list[str]] | None = None,
    perf_entry: dict | None = None,
) -> dict:
    h_validations = h_validations or {}
    data_rows = data_rows or []
    if not perf_entry:
        today_d = date.today()
        return {
            "has_tab": False,
            "title": DEFAULT_PERF_TAB,
            "max_row": 0,
            "last_row": 2,
            "years": extract_perf_years(data_rows, []),
            "month_names": list(MONTH_NAMES),
            "latest_month_key": "",
            "next_year": str(today_d.year),
            "next_month": MONTH_NAMES[today_d.month - 1],
            "months": [],
            "items": [],
        }

    header_idx = -1
    for i in range(min(5, len(p_rows))):
        if cell(p_rows[i], 0).lower() == "year" and cell(p_rows[i], 1).lower() == "month":
            header_idx = i
            break
    start_offset = header_idx + 1 if header_idx >= 0 else 2
    last_row = start_offset
    items = []
    month_map: dict[str, dict] = {}
    active_criteria = ""
    prev_mk_key = ""

    for i in range(start_offset, len(p_rows)):
        r = p_rows[i] or []
        row_num = i + 1
        if not any(bool(cell(r, c)) for c in range(12)):
            continue
        last_row = row_num

        mk = normalize_perf_month(cell(r, 0), cell(r, 1))
        if not mk:
            continue
        if mk["key"] != prev_mk_key:
            active_criteria = ""
            prev_mk_key = mk["key"]
        raw_crit = cell(r, 2)
        if raw_crit:
            active_criteria = raw_crit

        weight = cell(r, 5)
        score = cell(r, 8)
        sup_score = cell(r, 10)
        items.append({
            "row": row_num,
            "year": mk["year"],
            "month": mk["month"],
            "month_num": mk["month_num"],
            "month_key": mk["key"],
            "month_label": mk["label"],
            "criteria": raw_crit,
            "criteria_group": active_criteria,
            "description": cell(r, 3),
            "source": cell(r, 4),
            "weight": weight,
            "target": cell(r, 6),
            "achievement": cell(r, 7),
            "score": score,
            "remarks": cell(r, 9),
            "supervisor_score": sup_score,
            "supervisor_remarks": cell(r, 11),
            "sf": cell(r, 12),
            "h_options": h_validations.get(row_num, []),
        })

        if mk["key"] not in month_map:
            month_map[mk["key"]] = {
                "key": mk["key"],
                "year": mk["year"],
                "month": mk["month"],
                "month_num": mk["month_num"],
                "label": mk["label"],
                "sort_val": mk["sort_val"],
                "start_row": row_num,
                "end_row": row_num,
                "row_count": 0,
                "total_weight": 0.0,
                "self_score": 0.0,
                "supervisor_score": 0.0,
                "net_score": 0.0,
                "is_latest": False,
            }
        m = month_map[mk["key"]]
        m["start_row"] = min(m["start_row"], row_num)
        m["end_row"] = max(m["end_row"], row_num)
        m["row_count"] += 1
        try:
            m["total_weight"] += float(weight) if weight else 0.0
        except ValueError:
            pass
        try:
            m["self_score"] += float(score) if score else 0.0
        except ValueError:
            pass
        try:
            m["supervisor_score"] += float(sup_score) if sup_score else 0.0
        except ValueError:
            pass

    months = sorted(month_map.values(), key=lambda m: m["sort_val"], reverse=True)
    for m in months:
        m["total_weight"] = round(m["total_weight"], 2)
        m["self_score"] = round(m["self_score"], 2)
        m["supervisor_score"] = round(m["supervisor_score"], 2)
        m["net_score"] = round(m["self_score"] + m["supervisor_score"], 2)
    if months:
        months[0]["is_latest"] = True

    latest = months[0] if months else None
    today_d = date.today()
    next_year = str(today_d.year)
    next_month = MONTH_NAMES[today_d.month - 1]
    if latest:
        if latest["month_num"] == 12:
            next_year = str(int(latest["year"]) + 1)
            next_month = MONTH_NAMES[0]
        else:
            next_year = latest["year"]
            next_month = MONTH_NAMES[latest["month_num"]]

    return {
        "has_tab": True,
        "title": perf_entry["title"],
        "max_row": perf_entry["rowCount"],
        "last_row": last_row,
        "years": extract_perf_years(data_rows, months),
        "month_names": list(MONTH_NAMES),
        "latest_month_key": latest["key"] if latest else "",
        "next_year": next_year,
        "next_month": next_month,
        "months": months,
        "items": items,
    }


def validate_perf_achievement_val(raw_val, h_options: list[str] | None = None, desc: str = ""):
    s = "" if raw_val is None else str(raw_val).strip()
    if not s:
        return ""
    if h_options and s not in h_options:
        raise ApiError(f"Achievement for '{desc or 'row'}' must be one of: {', '.join(h_options)}.")
    try:
        n = float(s)
    except ValueError as exc:
        raise ApiError(f"Achievement for '{desc or 'row'}' must be a valid non-negative number.") from exc
    if n < 0:
        raise ApiError(f"Achievement for '{desc or 'row'}' must be a valid non-negative number.")
    return format_num_cell(n)


def fetch_perf_h_validations(svc, sid: str, perf_entry: dict | None, p_rows: list[list]) -> dict[int, list[str]]:
    if not perf_entry or not p_rows:
        return {}
    last_row = 2
    for i, r in enumerate(p_rows):
        if any(bool(cell(r, c)) for c in range(12)):
            last_row = i + 1
    if last_row < 3:
        return {}
    try:
        qp = quote_sheet(perf_entry["title"])
        grid = (
            svc.spreadsheets()
            .get(
                spreadsheetId=sid,
                ranges=[f"{qp}!H3:H{last_row}"],
                fields="sheets.data.rowData.values.dataValidation",
            )
            .execute()
        )
        row_data = grid.get("sheets", [{}])[0].get("data", [{}])[0].get("rowData", [])
        out: dict[int, list[str]] = {}
        for idx, rd in enumerate(row_data):
            dv = (rd.get("values") or [{}])[0].get("dataValidation") or {}
            cond = dv.get("condition") or {}
            if cond.get("type") == "ONE_OF_LIST":
                vals = [str(v.get("userEnteredValue", "")).strip() for v in cond.get("values", []) if str(v.get("userEnteredValue", "")).strip()]
                if vals:
                    out[3 + idx] = vals
        return out
    except Exception:
        return {}


def build_setup_status(cfg: dict, sheet_url: str = "") -> dict:
    return {
        "mode": "local",
        "role_mode": normalize_role_mode(cfg.get("role_mode")),
        "spreadsheet_id": cfg.get("spreadsheet_id", ""),
        "url": sheet_url,
        "tasks_tab": cfg.get("tasks_tab", DEFAULT_TASKS_TAB),
        "worklog_tab": cfg.get("worklog_tab", DEFAULT_WORKLOG_TAB),
        "data_tab": cfg.get("data_tab", DEFAULT_DATA_TAB),
        "perf_tab": cfg.get("perf_tab", DEFAULT_PERF_TAB),
        "sync_md_default": bool(cfg.get("sync_md_default", False)),
        "saved_sheets": cfg.get("saved_sheets", []),
        "has_credentials": bool(resolve_credentials_file()),
        "has_token": os.path.isfile(TOKEN_FILE),
    }


def fetch_bootstrap_data(service=None, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    spreadsheet_id = cfg.get("spreadsheet_id", "").strip()
    if not spreadsheet_id:
        return {
            "needs_setup": True,
            "spreadsheet": {
                "id": "",
                "title": "No Sheet Configured",
                "url": "",
                "tasks_sheet": cfg.get("tasks_tab", DEFAULT_TASKS_TAB),
                "tasks_max_row": 150,
                "last_task_row": 8,
                "worklog_sheet": cfg.get("worklog_tab", DEFAULT_WORKLOG_TAB),
                "worklog_max_row": 998,
                "last_worklog_row": 9,
            },
            "setup": build_setup_status(cfg),
            "employee": {
                "name": "Setup Required",
                "department": "",
                "id": "",
                "check_employee_all": True,
                "check_supervisor_all": True,
                "status_message_1": "",
                "status_message_2": "",
            },
            "dropdowns": extract_dropdowns([], []),
            "tasks": [],
            "worklogs": [],
            "perf": parse_perf_standard_data([], [], {}, None),
        }

    tasks_tab = cfg.get("tasks_tab", DEFAULT_TASKS_TAB)
    worklog_tab = cfg.get("worklog_tab", DEFAULT_WORKLOG_TAB)
    data_tab = cfg.get("data_tab", DEFAULT_DATA_TAB)
    perf_tab = cfg.get("perf_tab", DEFAULT_PERF_TAB)

    def _run(svc):
        smap = get_sheet_map(svc, spreadsheet_id, tasks_tab, worklog_tab, data_tab, perf_tab)
        t_title = quote_sheet(smap["tasks"]["title"])
        w_title = quote_sheet(smap["worklog"]["title"])
        t_max_row = smap["tasks"]["rowCount"]
        w_max_row = smap["worklog"]["rowCount"]

        ranges = [
            f"{t_title}!A5:Z7",
            f"{t_title}!A{TASKS_START_ROW}:Z{t_max_row}",
            f"{w_title}!A{WORKLOG_START_ROW}:G{w_max_row}",
        ]
        if smap["data"]:
            d_title = quote_sheet(smap["data"]["title"])
            ranges.append(f"{d_title}!A1:O45")
        if smap.get("perf"):
            p_title = quote_sheet(smap["perf"]["title"])
            ranges.append(f"{p_title}!A1:M{smap['perf']['rowCount']}")

        res = (
            svc.spreadsheets()
            .values()
            .batchGet(spreadsheetId=spreadsheet_id, ranges=ranges, valueRenderOption="FORMATTED_VALUE")
            .execute()
        )
        vrs = res.get("valueRanges", [])
        vr_idx = 0
        meta_rows = vrs[vr_idx].get("values", []) if vr_idx < len(vrs) else []
        vr_idx += 1
        t_rows = vrs[vr_idx].get("values", []) if vr_idx < len(vrs) else []
        vr_idx += 1
        w_rows = vrs[vr_idx].get("values", []) if vr_idx < len(vrs) else []
        vr_idx += 1
        d_rows = []
        if smap["data"]:
            d_rows = vrs[vr_idx].get("values", []) if vr_idx < len(vrs) else []
            vr_idx += 1
        p_rows = []
        if smap.get("perf"):
            p_rows = vrs[vr_idx].get("values", []) if vr_idx < len(vrs) else []
            vr_idx += 1

        r5 = meta_rows[0] if len(meta_rows) > 0 else []
        r6 = meta_rows[1] if len(meta_rows) > 1 else []
        r7 = meta_rows[2] if len(meta_rows) > 2 else []

        tasks = []
        last_task_row = TASKS_START_ROW - 1
        for offset, r in enumerate(t_rows):
            row_num = TASKS_START_ROW + offset
            if task_has_user_content(r):
                tasks.append(build_task_record(row_num, r))
                last_task_row = row_num

        worklogs = []
        last_worklog_row = WORKLOG_START_ROW - 1
        for offset, r in enumerate(w_rows):
            row_num = WORKLOG_START_ROW + offset
            if any(bool(cell(r, i)) for i in range(1, 7)):
                worklogs.append(build_worklog_record(row_num, r))
                last_worklog_row = row_num

        dropdowns = extract_dropdowns(d_rows, worklogs)
        t_gid = smap["tasks"].get("sheetId")
        sheet_url = (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit?gid={t_gid}#gid={t_gid}"
            if t_gid is not None
            else f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
        )

        setup_info = build_setup_status(cfg, sheet_url=sheet_url)
        setup_info["tasks_tab"] = smap["tasks"]["title"]
        setup_info["worklog_tab"] = smap["worklog"]["title"]
        setup_info["data_tab"] = smap["data"]["title"] if smap["data"] else data_tab
        setup_info["perf_tab"] = smap["perf"]["title"] if smap.get("perf") else perf_tab

        h_vals = fetch_perf_h_validations(svc, spreadsheet_id, smap.get("perf"), p_rows)
        perf_data = parse_perf_standard_data(p_rows, d_rows, h_vals, smap.get("perf"))

        return {
            "needs_setup": False,
            "spreadsheet": {
                "id": spreadsheet_id,
                "title": smap["spreadsheet_title"],
                "url": sheet_url,
                "tasks_sheet": smap["tasks"]["title"],
                "tasks_max_row": t_max_row,
                "last_task_row": last_task_row,
                "worklog_sheet": smap["worklog"]["title"],
                "worklog_max_row": w_max_row,
                "last_worklog_row": last_worklog_row,
            },
            "setup": setup_info,
            "employee": {
                "name": cell(r5, 2),
                "department": cell(r6, 2),
                "id": cell(r7, 2),
                "check_employee_all": cell(r7, 21).upper() != "FALSE" if cell(r7, 21) else True,
                "check_supervisor_all": cell(r7, 22).upper() != "FALSE" if cell(r7, 22) else True,
                "status_message_1": cell(r5, 3),
                "status_message_2": cell(r6, 3),
            },
            "dropdowns": dropdowns,
            "tasks": tasks,
            "worklogs": worklogs,
            "perf": perf_data,
        }

    if service is not None:
        return _run(service)
    with sheets_lock:
        svc = get_sheets_service()
        return _run(svc)


def validate_and_prepare_task(payload: dict) -> dict:
    allow_partial = bool(payload.get("allow_partial", False))
    date_of_entry = format_date_tasks(payload.get("date_of_entry") or date.today().strftime("%Y-%m-%d"))
    detail = (payload.get("detail") or "").strip()
    other_person = (payload.get("other_person") or "").strip()
    other_department = (payload.get("other_department") or "").strip()
    priority = (payload.get("priority") or "").strip()
    est_start_raw = payload.get("est_start_date") or ""
    act_start_raw = payload.get("act_start_date") or ""
    deadline_raw = payload.get("expected_deadline") or ""
    completion_raw = payload.get("completion_date") or ""

    est_start_d = parse_date_value(est_start_raw)
    act_start_d = parse_date_value(act_start_raw)
    deadline_d = parse_date_value(deadline_raw)
    completion_d = parse_date_value(completion_raw)

    est_time = parse_number(payload.get("est_time"), "Estimated Time (hours)", allow_blank=allow_partial)
    act_time = parse_number(payload.get("act_time"), "Actual Time (hours)", allow_blank=True)
    pct = parse_pct(payload.get("pct_completed"))
    delay_reason = (payload.get("delay_reason") or "").strip()
    expected_outcome = (payload.get("expected_outcome") or "").strip()
    difficulty = (payload.get("difficulty") or "").strip()
    remarks = (payload.get("remarks") or "").strip()
    supervisor_comments = (payload.get("supervisor_comments") or "").strip()
    clarification = (payload.get("clarification") or "").strip()

    if not detail:
        raise ApiError("Task detail (with purpose) is required.")

    if not allow_partial:
        missing = []
        if not priority:
            missing.append("Priority Scale")
        if not difficulty:
            missing.append("Level of Task Difficulty")
        if not expected_outcome:
            missing.append("Expected Outcome")
        if est_start_d is None:
            missing.append("Estimated Start Date")
        if deadline_d is None:
            missing.append("Expected Deadline")
        if est_time is None or est_time < 0:
            missing.append("Estimated Time (>= 0)")
        if missing:
            raise ApiError("Required for Tasks validation: " + ", ".join(missing) + ".")

        if completion_d is not None:
            comp_errs = []
            if act_start_d is None:
                comp_errs.append("Actual Start Date")
            if act_time is None or act_time <= 0:
                comp_errs.append("Actual Time (> 0)")
            if abs(pct - 100.0) > 1e-6:
                comp_errs.append("% Completed must be 100%")
            if comp_errs:
                raise ApiError(
                    "When Completion Date is filled, Tasks sheet requires: " + ", ".join(comp_errs) + "."
                )

        is_delayed = False
        if deadline_d is not None:
            if completion_d is not None and completion_d > deadline_d:
                is_delayed = True
            elif completion_d is None and date.today() > deadline_d:
                is_delayed = True
        if is_delayed and not delay_reason:
            raise ApiError(
                "Delay Reason (Col O) is required because Completion Date (or Today) is past Expected Deadline."
            )
    else:
        if est_time is not None and est_time < 0:
            raise ApiError("Estimated Time cannot be negative.")
        if act_time is not None and act_time < 0:
            raise ApiError("Actual Time cannot be negative.")

    pct_str = f"{pct:.2f}%" if str(payload.get("pct_completed", "")).strip() != "" else ""

    return {
        "B_to_M": [
            date_of_entry,
            detail,
            other_person,
            other_department,
            priority,
            est_start_d.strftime("%m/%d/%Y") if est_start_d else "",
            act_start_d.strftime("%m/%d/%Y") if act_start_d else "",
            deadline_d.strftime("%m/%d/%Y") if deadline_d else "",
            completion_d.strftime("%m/%d/%Y") if completion_d else "",
            format_num_cell(est_time),
            format_num_cell(act_time),
            pct_str,
        ],
        "O": [[delay_reason]],
        "Q_to_U": [[
            expected_outcome,
            difficulty,
            remarks,
            supervisor_comments,
            clarification,
        ]],
    }


def write_task_row(service, spreadsheet_id: str, sheet_title: str, row_num: int, prepared: dict):
    qt = quote_sheet(sheet_title)
    body = {
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": f"{qt}!B{row_num}:M{row_num}", "values": [prepared["B_to_M"]]},
            {"range": f"{qt}!O{row_num}", "values": prepared["O"]},
            {"range": f"{qt}!Q{row_num}:U{row_num}", "values": prepared["Q_to_U"]},
        ],
    }
    service.spreadsheets().values().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()


def task_snapshot_matches(existing: dict, expected: dict | None) -> bool:
    if not expected:
        return True
    return (
        existing.get("detail", "") == (expected.get("detail") or "")
        and existing.get("date_of_entry", "") == (expected.get("date_of_entry") or "")
    )


def _active_smap(svc, cfg: dict) -> tuple[str, dict]:
    sid = cfg.get("spreadsheet_id", "").strip()
    if not sid:
        raise ApiError("No Google Sheet is configured yet. Click Setup to connect your sheet.", status=400, code="NO_SHEET")
    smap = get_sheet_map(
        svc,
        sid,
        cfg.get("tasks_tab", DEFAULT_TASKS_TAB),
        cfg.get("worklog_tab", DEFAULT_WORKLOG_TAB),
        cfg.get("data_tab", DEFAULT_DATA_TAB),
        cfg.get("perf_tab", DEFAULT_PERF_TAB),
    )
    return sid, smap


def create_task(payload: dict) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "add new tasks")
    prepared = validate_and_prepare_task(payload)
    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        t_title = smap["tasks"]["title"]
        t_max = smap["tasks"]["rowCount"]
        qt = quote_sheet(t_title)

        rows = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qt}!A{TASKS_START_ROW}:U{t_max}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )
        last_used = TASKS_START_ROW - 1
        for offset, r in enumerate(rows):
            if task_has_user_content(r):
                last_used = TASKS_START_ROW + offset

        target_row = last_used + 1
        if target_row > t_max:
            raise ApiError(
                f"Tasks sheet has reached its template limit of row {t_max} (referenced by V7/W7). Delete unused rows or expand sheet formulas first.",
                status=400,
            )

        write_task_row(svc, sid, t_title, target_row, prepared)
        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["affected_row"] = target_row
        return data


def update_task(row_num: int, payload: dict) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "edit employee task fields")
    prepared = validate_and_prepare_task(payload)
    expected = payload.get("expected_snapshot")
    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        t_title = smap["tasks"]["title"]
        t_max = smap["tasks"]["rowCount"]
        if row_num < TASKS_START_ROW or row_num > t_max:
            raise ApiError(f"Task row must be between {TASKS_START_ROW} and {t_max}.")

        qt = quote_sheet(t_title)
        curr_vals = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qt}!A{row_num}:Z{row_num}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )
        curr_rec = build_task_record(row_num, curr_vals[0] if curr_vals else [])
        if not task_snapshot_matches(curr_rec, expected):
            raise ApiError(
                "This task row changed in Google Sheets since you loaded it. Refreshing latest data.",
                status=409,
                code="STALE_ROW",
            )

        write_task_row(svc, sid, t_title, row_num, prepared)
        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["affected_row"] = row_num
        return data


def update_task_supervisor_comment(row_num: int, payload: dict) -> dict:
    cfg = load_config()
    require_supervisor_mode(cfg, "Supervisor Comments (Col T)")
    supervisor_comments = (payload.get("supervisor_comments") or "").strip()
    expected = payload.get("expected_snapshot")
    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        t_title = smap["tasks"]["title"]
        t_max = smap["tasks"]["rowCount"]
        if row_num < TASKS_START_ROW or row_num > t_max:
            raise ApiError(f"Task row must be between {TASKS_START_ROW} and {t_max}.")

        qt = quote_sheet(t_title)
        curr_vals = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qt}!A{row_num}:Z{row_num}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )
        curr_row = curr_vals[0] if curr_vals else []
        if not task_has_user_content(curr_row):
            raise ApiError(f"Task row {row_num} is empty.", status=404)
        curr_rec = build_task_record(row_num, curr_row)
        if not task_snapshot_matches(curr_rec, expected):
            raise ApiError(
                "This task row changed in Google Sheets since you loaded it. Refreshing latest data.",
                status=409,
                code="STALE_ROW",
            )

        # Strictly write ONLY Column T (Supervisor Comments)
        svc.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{qt}!T{row_num}",
            valueInputOption="USER_ENTERED",
            body={"values": [[supervisor_comments]]},
        ).execute()
        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["affected_row"] = row_num
        return data


def delete_task(row_num: int, expected: dict | None = None) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "delete tasks")
    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        t_title = smap["tasks"]["title"]
        t_max = smap["tasks"]["rowCount"]
        if row_num < TASKS_START_ROW or row_num > t_max:
            raise ApiError(f"Task row must be between {TASKS_START_ROW} and {t_max}.")

        qt = quote_sheet(t_title)
        rows = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qt}!A{TASKS_START_ROW}:U{t_max}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )

        idx = row_num - TASKS_START_ROW
        curr_row = rows[idx] if idx < len(rows) else []
        if not task_has_user_content(curr_row):
            raise ApiError(f"Task row {row_num} is already empty.", status=404)
        if not task_snapshot_matches(build_task_record(row_num, curr_row), expected):
            raise ApiError(
                "This task row changed in Google Sheets since you loaded it. Refreshing latest data.",
                status=409,
                code="STALE_ROW",
            )

        last_used = row_num
        for offset, r in enumerate(rows):
            rn = TASKS_START_ROW + offset
            if task_has_user_content(r):
                last_used = max(last_used, rn)

        updates = []
        if row_num < last_used:
            shift_b_m = []
            shift_o = []
            shift_q_u = []
            for rn in range(row_num + 1, last_used + 1):
                r = rows[rn - TASKS_START_ROW] if (rn - TASKS_START_ROW) < len(rows) else []
                shift_b_m.append([cell(r, c) for c in range(1, 13)])
                shift_o.append([cell(r, 14)])
                shift_q_u.append([cell(r, c) for c in range(16, 21)])
            updates.extend([
                {"range": f"{qt}!B{row_num}:M{last_used - 1}", "values": shift_b_m},
                {"range": f"{qt}!O{row_num}:O{last_used - 1}", "values": shift_o},
                {"range": f"{qt}!Q{row_num}:U{last_used - 1}", "values": shift_q_u},
            ])

        updates.extend([
            {"range": f"{qt}!B{last_used}:M{last_used}", "values": [[""] * 12]},
            {"range": f"{qt}!O{last_used}", "values": [[""]]},
            {"range": f"{qt}!Q{last_used}:U{last_used}", "values": [[""] * 5]},
        ])

        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()

        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["deleted_row"] = row_num
        return data


def validate_and_prepare_worklog(payload: dict) -> dict:
    date_raw = (payload.get("date") or "").strip() or date.today().strftime("%Y-%m-%d")
    date_str = format_date_worklog(date_raw)
    task = (payload.get("task") or "").strip()
    project = (payload.get("project") or "").strip()
    time_mins = parse_number(payload.get("time_minutes"), "Time (minutes)", allow_blank=False)
    if time_mins is None or time_mins < 0:
        raise ApiError("Time (minutes) must be 0 or greater.")
    status = normalize_worklog_status(payload.get("status") or "Complete")
    comments = (payload.get("comments") or "").strip()

    if not task:
        raise ApiError("Task description is required.")
    if not project:
        raise ApiError("Client / Project is required.")

    time_cell = format_num_cell(time_mins)
    return {
        "date": date_str,
        "task": task,
        "project": project,
        "time_minutes": str(time_cell),
        "status": status,
        "comments": comments,
        "row_values": [date_str, task, project, time_cell, status, comments],
    }


def worklog_snapshot_matches(existing: dict, expected: dict | None) -> bool:
    if not expected:
        return True
    return (
        existing.get("task", "") == (expected.get("task") or "")
        and existing.get("project", "") == (expected.get("project") or "")
        and normalize_date_key(existing.get("date", "")) == normalize_date_key(expected.get("date") or "")
    )


# --- Optional Incremental Work_Log_History.md Sync ---

ENTRY_RE = re.compile(
    r"^- \*\*Project\*\*:\s*(?P<project>.*)\n"
    r"  \*\*Task\*\*:\s*(?P<task>.*)\n"
    r"  \*\*Time\*\*:\s*(?P<time>.*?) minutes\n"
    r"  \*\*Status\*\*:\s*(?P<status>.*)\n"
    r"  \*\*Comments\*\*:\s*(?P<comments>.*)$"
)


def _md_inline(val: str) -> str:
    return re.sub(r"\s*\r?\n+\s*", " ", (val or "").strip())


def format_md_entry(entry: dict) -> str:
    return (
        f"- **Project**: {_md_inline(entry['project'])}\n"
        f"  **Task**: {_md_inline(entry['task'])}\n"
        f"  **Time**: {_md_inline(str(entry['time_minutes']))} minutes\n"
        f"  **Status**: {_md_inline(entry['status'])}\n"
        f"  **Comments**: {_md_inline(entry['comments'])}"
    )


def parse_md_sections(text: str) -> tuple[str, list[dict]]:
    normalized = text.replace("\r\n", "\n")
    parts = re.split(r"(?m)^## (.+)$", normalized)
    preamble = parts[0].rstrip("\n")
    sections = []
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        raw_blocks = [b.strip("\n") for b in re.split(r"\n{2,}", body.strip("\n")) if b.strip()]
        entries = []
        for blk in raw_blocks:
            m = ENTRY_RE.match(blk)
            if m:
                entries.append({
                    "project": m.group("project").strip(),
                    "task": m.group("task").strip(),
                    "time_minutes": m.group("time").strip(),
                    "status": m.group("status").strip(),
                    "comments": m.group("comments").strip(),
                    "raw": blk,
                })
            else:
                entries.append({"raw": blk})
        sections.append({"date": heading, "entries": entries})
    return preamble, sections


def write_md_sections(preamble: str, sections: list[dict]):
    out = [preamble.rstrip()]
    for sec in sections:
        if not sec["entries"]:
            continue
        out.append("")
        out.append(f"## {sec['date']}")
        for idx, e in enumerate(sec["entries"]):
            if "task" in e:
                out.append(format_md_entry(e))
            else:
                out.append(e["raw"])
            if idx < len(sec["entries"]) - 1:
                out.append("")
    content = "\n".join(out).rstrip() + "\n"
    with open(WORK_LOG_MD_FILE, "w", encoding="utf-8") as f:
        f.write(content)


def find_md_entry_index(sections: list[dict], target: dict) -> tuple[int, int] | None:
    target_d = normalize_date_key(target.get("date", ""))
    target_t = _md_inline(target.get("task", ""))
    target_p = _md_inline(target.get("project", ""))
    for si, sec in enumerate(sections):
        sec_d = ""
        try:
            sec_d = normalize_date_key(sec["date"])
        except ApiError:
            sec_d = ""
        if target_d and sec_d and sec_d != target_d:
            continue
        for ei, e in enumerate(sec["entries"]):
            if "task" in e and _md_inline(e["task"]) == target_t and _md_inline(e["project"]) == target_p:
                return si, ei
    return None


def _append_entry_to_sections(sections: list[dict], new_entry: dict):
    new_d = normalize_date_key(new_entry["date"])
    for sec in reversed(sections):
        try:
            if normalize_date_key(sec["date"]) == new_d:
                sec["entries"].append(new_entry)
                return
        except ApiError:
            continue
    sections.append({"date": new_entry["date"], "entries": [new_entry]})


def sync_worklog_md(action: str, new_entry: dict | None = None, old_entry: dict | None = None):
    with md_lock:
        if not os.path.exists(WORK_LOG_MD_FILE):
            if action == "create" and new_entry:
                preamble = "# Work Log History\n\nThis file keeps a local record of work tasks logged to Google Sheets."
                write_md_sections(preamble, [{"date": new_entry["date"], "entries": [new_entry]}])
            return

        with open(WORK_LOG_MD_FILE, "r", encoding="utf-8") as f:
            text = f.read()
        preamble, sections = parse_md_sections(text)

        if action == "create" and new_entry:
            _append_entry_to_sections(sections, new_entry)
            write_md_sections(preamble, sections)

        elif action == "edit" and new_entry and old_entry:
            loc = find_md_entry_index(sections, old_entry)
            if loc is not None:
                si, ei = loc
                if normalize_date_key(old_entry.get("date", "")) == normalize_date_key(new_entry["date"]):
                    sections[si]["entries"][ei] = new_entry
                else:
                    sections[si]["entries"].pop(ei)
                    _append_entry_to_sections(sections, new_entry)
            else:
                _append_entry_to_sections(sections, new_entry)
            write_md_sections(preamble, sections)

        elif action == "delete" and old_entry:
            loc = find_md_entry_index(sections, old_entry)
            if loc is not None:
                si, ei = loc
                sections[si]["entries"].pop(ei)
                write_md_sections(preamble, sections)


def create_worklog(payload: dict) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "add work log entries")
    prepared = validate_and_prepare_worklog(payload)
    sync_md = payload.get("sync_md", bool(cfg.get("sync_md_default", False)))
    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        w_title = smap["worklog"]["title"]
        w_max = smap["worklog"]["rowCount"]
        qw = quote_sheet(w_title)

        rows = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qw}!A{WORKLOG_START_ROW}:G{w_max}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )
        last_used = WORKLOG_START_ROW - 1
        for offset, r in enumerate(rows):
            if any(bool(cell(r, i)) for i in range(1, 7)):
                last_used = WORKLOG_START_ROW + offset

        target_row = last_used + 1
        if target_row > w_max:
            sheet_id = smap["worklog"]["sheetId"]
            svc.spreadsheets().batchUpdate(
                spreadsheetId=sid,
                body={
                    "requests": [{
                        "appendDimension": {
                            "sheetId": sheet_id,
                            "dimension": "ROWS",
                            "length": 100,
                        }
                    }]
                },
            ).execute()

        svc.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{qw}!B{target_row}:G{target_row}",
            valueInputOption="USER_ENTERED",
            body={"values": [prepared["row_values"]]},
        ).execute()

        if sync_md:
            sync_worklog_md("create", new_entry=prepared)

        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["affected_row"] = target_row
        return data


def update_worklog(row_num: int, payload: dict) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "edit work log entries")
    prepared = validate_and_prepare_worklog(payload)
    expected = payload.get("expected_snapshot")
    sync_md = payload.get("sync_md", bool(cfg.get("sync_md_default", False)))
    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        w_title = smap["worklog"]["title"]
        w_max = smap["worklog"]["rowCount"]
        if row_num < WORKLOG_START_ROW or row_num > w_max:
            raise ApiError(f"Work log row must be between {WORKLOG_START_ROW} and {w_max}.")

        qw = quote_sheet(w_title)
        curr_vals = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qw}!A{row_num}:G{row_num}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )
        curr_rec = build_worklog_record(row_num, curr_vals[0] if curr_vals else [])
        if not worklog_snapshot_matches(curr_rec, expected):
            raise ApiError(
                "This work log row changed in Google Sheets since you loaded it. Refreshing latest data.",
                status=409,
                code="STALE_ROW",
            )

        svc.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"{qw}!B{row_num}:G{row_num}",
            valueInputOption="USER_ENTERED",
            body={"values": [prepared["row_values"]]},
        ).execute()

        if sync_md:
            sync_worklog_md("edit", new_entry=prepared, old_entry=curr_rec)

        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["affected_row"] = row_num
        return data


def delete_worklog(row_num: int, expected: dict | None = None, sync_md: bool | None = None) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "delete work log entries")
    if sync_md is None:
        sync_md = bool(cfg.get("sync_md_default", False))
    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        w_title = smap["worklog"]["title"]
        w_max = smap["worklog"]["rowCount"]
        if row_num < WORKLOG_START_ROW or row_num > w_max:
            raise ApiError(f"Work log row must be between {WORKLOG_START_ROW} and {w_max}.")

        qw = quote_sheet(w_title)
        rows = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qw}!A{WORKLOG_START_ROW}:G{w_max}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )
        idx = row_num - WORKLOG_START_ROW
        curr_row = rows[idx] if idx < len(rows) else []
        if not any(bool(cell(curr_row, i)) for i in range(1, 7)):
            raise ApiError(f"Work log row {row_num} is already empty.", status=404)

        curr_rec = build_worklog_record(row_num, curr_row)
        if not worklog_snapshot_matches(curr_rec, expected):
            raise ApiError(
                "This work log row changed in Google Sheets since you loaded it. Refreshing latest data.",
                status=409,
                code="STALE_ROW",
            )

        last_used = row_num
        for offset, r in enumerate(rows):
            rn = WORKLOG_START_ROW + offset
            if any(bool(cell(r, i)) for i in range(1, 7)):
                last_used = max(last_used, rn)

        updates = []
        if row_num < last_used:
            shift_rows = []
            for rn in range(row_num + 1, last_used + 1):
                r = rows[rn - WORKLOG_START_ROW] if (rn - WORKLOG_START_ROW) < len(rows) else []
                shift_rows.append([cell(r, c) for c in range(1, 7)])
            updates.append({"range": f"{qw}!B{row_num}:G{last_used - 1}", "values": shift_rows})

        updates.append({"range": f"{qw}!B{last_used}:G{last_used}", "values": [[""] * 6]})

        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()

        if sync_md:
            sync_worklog_md("delete", old_entry=curr_rec)

        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["deleted_row"] = row_num
        return data


def create_perf_month(payload: dict) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "create a new Perf Standard month")
    target_mk = normalize_perf_month(payload.get("year"), payload.get("month"))
    if not target_mk:
        raise ApiError("Valid 4-digit Year and Month name are required.")
    src_mk = normalize_perf_month(payload.get("source_year"), payload.get("source_month"))
    if not src_mk:
        raise ApiError("Source Year and Month to copy from are required.")

    with sheets_lock:
        svc = get_sheets_service()
        sid, smap = _active_smap(svc, cfg)
        perf_entry = smap.get("perf")
        if not perf_entry:
            raise ApiError(f"Tab '{cfg.get('perf_tab', DEFAULT_PERF_TAB)}' was not found in this Google Sheet.")
        qp = quote_sheet(perf_entry["title"])
        p_max = perf_entry["rowCount"]
        p_rows = (
            svc.spreadsheets()
            .values()
            .get(spreadsheetId=sid, range=f"{qp}!A1:M{p_max}", valueRenderOption="FORMATTED_VALUE")
            .execute()
            .get("values", [])
        )
        h_vals = fetch_perf_h_validations(svc, sid, perf_entry, p_rows)
        perf_state = parse_perf_standard_data(p_rows, [], h_vals, perf_entry)

        if any(m["key"] == target_mk["key"] for m in perf_state["months"]):
            raise ApiError(
                f"Perf Standard entry for {target_mk['label']} already exists. View it from the Month selector.",
                status=409,
                code="DUPLICATE_MONTH",
            )
        src_rows = sorted(
            [it for it in perf_state["items"] if it["month_key"] == src_mk["key"]],
            key=lambda it: it["row"],
        )
        if not src_rows:
            raise ApiError(f"No rows found for source month {src_mk['label']}.")

        dst_start = perf_state["last_row"] + 1
        dst_end = dst_start + len(src_rows) - 1
        batch_reqs = []
        if dst_end > p_max:
            batch_reqs.append({
                "appendDimension": {
                    "sheetId": perf_entry["sheetId"],
                    "dimension": "ROWS",
                    "length": max(100, dst_end - p_max),
                }
            })

        run_start = src_rows[0]["row"]
        run_prev = src_rows[0]["row"]
        run_dst_start = dst_start
        for idx in range(1, len(src_rows) + 1):
            cur_row = src_rows[idx]["row"] if idx < len(src_rows) else None
            if cur_row == run_prev + 1:
                run_prev = cur_row
                continue
            run_len = run_prev - run_start + 1
            batch_reqs.append({
                "copyPaste": {
                    "source": {
                        "sheetId": perf_entry["sheetId"],
                        "startRowIndex": run_start - 1,
                        "endRowIndex": run_prev,
                        "startColumnIndex": 0,
                        "endColumnIndex": 13,
                    },
                    "destination": {
                        "sheetId": perf_entry["sheetId"],
                        "startRowIndex": run_dst_start - 1,
                        "endRowIndex": run_dst_start - 1 + run_len,
                        "startColumnIndex": 0,
                        "endColumnIndex": 13,
                    },
                    "pasteType": "PASTE_NORMAL",
                }
            })
            if cur_row is not None:
                run_dst_start += run_len
                run_start = cur_row
                run_prev = cur_row

        svc.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": batch_reqs}).execute()

        provided_ach = payload.get("achievements") if isinstance(payload.get("achievements"), list) else None
        year_val = int(target_mk["year"])
        ab_vals = []
        h_vals_out = []
        jl_vals = []
        for i, src in enumerate(src_rows):
            ab_vals.append([year_val, target_mk["month"]])
            raw_ach = provided_ach[i] if provided_ach and i < len(provided_ach) else ""
            h_vals_out.append([validate_perf_achievement_val(raw_ach, src["h_options"], src["description"])])
            jl_vals.append(["", "", ""])

        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {"range": f"{qp}!A{dst_start}:B{dst_end}", "values": ab_vals},
                    {"range": f"{qp}!H{dst_start}:H{dst_end}", "values": h_vals_out},
                    {"range": f"{qp}!J{dst_start}:L{dst_end}", "values": jl_vals},
                ],
            },
        ).execute()

        data = fetch_bootstrap_data(svc, cfg=cfg)
        data["created_month_key"] = target_mk["key"]
        return data


def _load_perf_write_context(svc, cfg: dict, year, month) -> tuple[str, str, dict, dict, dict[int, dict]]:
    target_mk = normalize_perf_month(year, month)
    if not target_mk:
        raise ApiError("Valid Year and Month are required.")
    sid, smap = _active_smap(svc, cfg)
    perf_entry = smap.get("perf")
    if not perf_entry:
        raise ApiError(f"Tab '{cfg.get('perf_tab', DEFAULT_PERF_TAB)}' was not found in this Google Sheet.")
    qp = quote_sheet(perf_entry["title"])
    p_max = perf_entry["rowCount"]
    p_rows = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sid, range=f"{qp}!A1:M{p_max}", valueRenderOption="FORMATTED_VALUE")
        .execute()
        .get("values", [])
    )
    h_vals = fetch_perf_h_validations(svc, sid, perf_entry, p_rows)
    perf_state = parse_perf_standard_data(p_rows, [], h_vals, perf_entry)
    month_obj = next((m for m in perf_state["months"] if m["key"] == target_mk["key"]), None)
    if not month_obj:
        raise ApiError(f"Perf Standard month {target_mk['label']} was not found.")
    by_row = {it["row"]: it for it in perf_state["items"] if it["month_key"] == month_obj["key"]}
    return sid, qp, perf_state, month_obj, by_row


def _resolve_perf_update_row(by_row: dict[int, dict], month_obj: dict, update_item: dict) -> tuple[int, dict]:
    rn = int(update_item.get("row", 0))
    rec = by_row.get(rn)
    if not rec:
        raise ApiError(f"Row {rn} does not belong to {month_obj['label']}. Refreshing latest data.", status=409, code="STALE_ROW")
    if "expected_description" in update_item and rec["description"] != str(update_item["expected_description"] or "").strip():
        raise ApiError(f"Perf Standard row {rn} changed externally. Refreshing latest data.", status=409, code="STALE_ROW")
    return rn, rec


def update_perf_achievements(payload: dict) -> dict:
    cfg = load_config()
    require_employee_mode(cfg, "edit Achievement values (Col H)")
    updates_list = payload.get("updates")
    if not isinstance(updates_list, list) or not updates_list:
        raise ApiError("At least one Achievement update is required.")

    with sheets_lock:
        svc = get_sheets_service()
        sid, qp, perf_state, month_obj, by_row = _load_perf_write_context(svc, cfg, payload.get("year"), payload.get("month"))
        if month_obj["key"] != perf_state["latest_month_key"]:
            latest_label = perf_state["months"][0]["label"] if perf_state["months"] else perf_state["latest_month_key"]
            raise ApiError(f"Past months are view-only. Only the latest month ({latest_label}) can edit Achievement.", status=403)

        data_updates = []
        for u in updates_list:
            rn, rec = _resolve_perf_update_row(by_row, month_obj, u)
            cleaned = validate_perf_achievement_val(u.get("achievement"), rec["h_options"], rec["description"])
            data_updates.append({"range": f"{qp}!H{rn}", "values": [[cleaned]]})

        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "USER_ENTERED", "data": data_updates},
        ).execute()
        return fetch_bootstrap_data(svc, cfg=cfg)


def update_perf_supervisor_remarks(payload: dict) -> dict:
    cfg = load_config()
    require_supervisor_mode(cfg, "Supervisor Remarks (Col L)")
    updates_list = payload.get("updates")
    if not isinstance(updates_list, list) or not updates_list:
        raise ApiError("At least one Supervisor Remark update is required.")

    with sheets_lock:
        svc = get_sheets_service()
        sid, qp, _perf_state, month_obj, by_row = _load_perf_write_context(svc, cfg, payload.get("year"), payload.get("month"))
        data_updates = []
        for u in updates_list:
            rn, _rec = _resolve_perf_update_row(by_row, month_obj, u)
            remark_val = str(u.get("supervisor_remarks") or "").strip()
            # Strictly write ONLY Column L (Supervisor Remarks)
            data_updates.append({"range": f"{qp}!L{rn}", "values": [[remark_val]]})

        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "USER_ENTERED", "data": data_updates},
        ).execute()
        return fetch_bootstrap_data(svc, cfg=cfg)


def _probe_sheet_setup(svc, payload: dict) -> tuple[dict, dict]:
    sid = parse_spreadsheet_id(payload.get("sheet_url") or payload.get("spreadsheet_id") or "")
    tasks_tab = (payload.get("tasks_tab") or DEFAULT_TASKS_TAB).strip() or DEFAULT_TASKS_TAB
    worklog_tab = (payload.get("worklog_tab") or DEFAULT_WORKLOG_TAB).strip() or DEFAULT_WORKLOG_TAB
    data_tab = (payload.get("data_tab") or DEFAULT_DATA_TAB).strip() or DEFAULT_DATA_TAB
    perf_tab = (payload.get("perf_tab") or DEFAULT_PERF_TAB).strip() or DEFAULT_PERF_TAB

    smap = get_sheet_map(svc, sid, tasks_tab, worklog_tab, data_tab, perf_tab)
    t_title = quote_sheet(smap["tasks"]["title"])
    meta_rows = (
        svc.spreadsheets()
        .values()
        .get(spreadsheetId=sid, range=f"{t_title}!A5:C7", valueRenderOption="FORMATTED_VALUE")
        .execute()
        .get("values", [])
    )
    emp_name = cell(meta_rows[0], 2) if len(meta_rows) > 0 else ""
    emp_dept = cell(meta_rows[1], 2) if len(meta_rows) > 1 else ""
    emp_id = cell(meta_rows[2], 2) if len(meta_rows) > 2 else ""

    t_gid = smap["tasks"].get("sheetId")
    url = (
        f"https://docs.google.com/spreadsheets/d/{sid}/edit?gid={t_gid}#gid={t_gid}"
        if t_gid is not None
        else f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    )
    info = {
        "ok": True,
        "spreadsheet_id": sid,
        "title": smap["spreadsheet_title"],
        "employee_name": emp_name,
        "department": emp_dept,
        "employee_id": emp_id,
        "tasks_tab": smap["tasks"]["title"],
        "worklog_tab": smap["worklog"]["title"],
        "data_tab": smap["data"]["title"] if smap["data"] else "",
        "perf_tab": smap["perf"]["title"] if smap.get("perf") else "",
        "available_tabs": smap["available_tabs"],
        "url": url,
    }
    return info, smap


def test_setup_connection(payload: dict) -> dict:
    with sheets_lock:
        svc = get_sheets_service()
        info, _ = _probe_sheet_setup(svc, payload)
        return info


def apply_setup(payload: dict) -> dict:
    with sheets_lock:
        svc = get_sheets_service()
        info, _ = _probe_sheet_setup(svc, payload)
        cfg = load_config()
        if "role_mode" in payload:
            cfg["role_mode"] = normalize_role_mode(payload.get("role_mode"))
        else:
            cfg["role_mode"] = normalize_role_mode(cfg.get("role_mode"))
        cfg["spreadsheet_id"] = info["spreadsheet_id"]
        cfg["tasks_tab"] = info["tasks_tab"]
        cfg["worklog_tab"] = info["worklog_tab"]
        cfg["data_tab"] = info["data_tab"] or DEFAULT_DATA_TAB
        cfg["perf_tab"] = info.get("perf_tab") or DEFAULT_PERF_TAB
        if "sync_md_default" in payload:
            cfg["sync_md_default"] = bool(payload["sync_md_default"])
        upsert_saved_sheet(
            cfg,
            {
                "role_mode": cfg["role_mode"],
                "spreadsheet_id": info["spreadsheet_id"],
                "title": info["title"],
                "employee_name": info["employee_name"],
                "tasks_tab": info["tasks_tab"],
                "worklog_tab": info["worklog_tab"],
                "data_tab": cfg["data_tab"],
                "perf_tab": cfg["perf_tab"],
                "url": info["url"],
            },
        )
        save_config(cfg)
        return fetch_bootstrap_data(svc, cfg=cfg)


def remove_saved_sheet(payload: dict) -> dict:
    sid = (payload.get("spreadsheet_id") or "").strip()
    if not sid:
        raise ApiError("spreadsheet_id is required.")
    cfg = load_config()
    if sid == cfg.get("spreadsheet_id"):
        raise ApiError("Cannot remove the currently active sheet.")
    cfg["saved_sheets"] = [s for s in cfg.get("saved_sheets", []) if s.get("spreadsheet_id") != sid]
    save_config(cfg)
    return {"ok": True, "saved_sheets": cfg["saved_sheets"]}


def reauthenticate_google() -> dict:
    with sheets_lock:
        get_credentials(force_reauth=True)
        cfg = load_config()
        if not cfg.get("spreadsheet_id"):
            return fetch_bootstrap_data(cfg=cfg)
        svc = get_sheets_service()
        return fetch_bootstrap_data(svc, cfg=cfg)


class TaskWorklogHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError("Invalid JSON request body.") from exc

    def _handle_exception(self, exc: Exception):
        if isinstance(exc, ApiError):
            self._send_json({"error": exc.message, "code": exc.code}, status=exc.status)
        elif isinstance(exc, HttpError):
            code = getattr(exc.resp, "status", None)
            if code == 403:
                msg = (
                    "Permission denied (403) accessing this Google Sheet. "
                    "Make sure the sheet is shared with Editor permission to the signed-in Google account, "
                    "or use Setup -> Switch Google Account."
                )
            elif code == 404:
                msg = "Google Sheet not found (404). Please check the Sheet link or ID in Setup."
            else:
                msg = f"Google Sheets API error ({code}): {exc}"
            self._send_json({"error": msg, "code": "SHEETS_ERROR"}, status=code if code in (403, 404) else 502)
        else:
            self._send_json({"error": str(exc), "code": "INTERNAL_ERROR"}, status=500)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            if not os.path.isfile(UI_HTML_FILE):
                self.send_error(500, "UI file missing")
                return
            with open(UI_HTML_FILE, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/sheets_browser.js":
            if not os.path.isfile(BROWSER_JS_FILE):
                self.send_error(404)
                return
            with open(BROWSER_JS_FILE, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/bootstrap":
            try:
                self._send_json(fetch_bootstrap_data())
            except Exception as exc:
                self._handle_exception(exc)
        elif parsed.path == "/api/setup":
            try:
                cfg = load_config()
                self._send_json(build_setup_status(cfg))
            except Exception as exc:
                self._handle_exception(exc)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._read_json_body()
            if parsed.path == "/api/tasks":
                self._send_json(create_task(payload), status=201)
            elif parsed.path == "/api/worklogs":
                self._send_json(create_worklog(payload), status=201)
            elif parsed.path == "/api/perf/months":
                self._send_json(create_perf_month(payload), status=201)
            elif parsed.path == "/api/setup/test":
                self._send_json(test_setup_connection(payload))
            elif parsed.path == "/api/setup":
                self._send_json(apply_setup(payload))
            elif parsed.path == "/api/setup/remove":
                self._send_json(remove_saved_sheet(payload))
            elif parsed.path == "/api/setup/reauth":
                self._send_json(reauthenticate_google())
            else:
                self.send_error(404)
        except Exception as exc:
            self._handle_exception(exc)

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._read_json_body()
            m_task_sup = re.fullmatch(r"/api/tasks/(\d+)/supervisor-comment", parsed.path)
            m_task = re.fullmatch(r"/api/tasks/(\d+)", parsed.path)
            m_wl = re.fullmatch(r"/api/worklogs/(\d+)", parsed.path)
            if m_task_sup:
                self._send_json(update_task_supervisor_comment(int(m_task_sup.group(1)), payload))
            elif m_task:
                self._send_json(update_task(int(m_task.group(1)), payload))
            elif m_wl:
                self._send_json(update_worklog(int(m_wl.group(1)), payload))
            elif parsed.path == "/api/perf/achievements":
                self._send_json(update_perf_achievements(payload))
            elif parsed.path == "/api/perf/supervisor-remarks":
                self._send_json(update_perf_supervisor_remarks(payload))
            else:
                self.send_error(404)
        except Exception as exc:
            self._handle_exception(exc)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._read_json_body()
            m_task = re.fullmatch(r"/api/tasks/(\d+)", parsed.path)
            m_wl = re.fullmatch(r"/api/worklogs/(\d+)", parsed.path)
            if m_task:
                self._send_json(delete_task(int(m_task.group(1)), expected=payload.get("expected_snapshot")))
            elif m_wl:
                sync_md = payload.get("sync_md", None)
                self._send_json(delete_worklog(int(m_wl.group(1)), expected=payload.get("expected_snapshot"), sync_md=sync_md))
            else:
                self.send_error(404)
        except Exception as exc:
            self._handle_exception(exc)


def main():
    parser = argparse.ArgumentParser(description="Tasks & Work Log Web Manager")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on (default: 8766)")
    parser.add_argument("--no-open", action="store_true", help="Do not automatically open the browser")
    args = parser.parse_args()

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", args.port), TaskWorklogHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Tasks & Work Log Manager running at {url}", flush=True)
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
