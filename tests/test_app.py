import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import task_worklog_manager as twm


class FakeExecute:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeSheetsMemory:
    """In-memory Google Sheets mock preserving read-only formula columns."""

    def __init__(self):
        self.meta = {
            "properties": {"title": "Sample Employee Monthly Reporting"},
            "sheets": [
                {"properties": {"sheetId": 101, "title": "Tasks", "gridProperties": {"rowCount": 150, "columnCount": 26}}},
                {"properties": {"sheetId": 202, "title": "Work log", "gridProperties": {"rowCount": 998, "columnCount": 7}}},
                {"properties": {"sheetId": 303, "title": "Data", "gridProperties": {"rowCount": 1000, "columnCount": 29}}},
                {"properties": {"sheetId": 404, "title": "Perf Standard", "gridProperties": {"rowCount": 200, "columnCount": 26}}},
                {"properties": {"sheetId": 505, "title": "Report", "gridProperties": {"rowCount": 40, "columnCount": 12}}},
            ],
        }
        self.cells = {}
        self.written_ranges = []
        self.batch_requests = []
        self.perf_h_dv = {}
        self.report_formats = {}
        self.report_dv = {}
        self.report_hidden_rows = set()
        self.report_hidden_cols = set()
        self.report_merges = []
        self.report_col_widths = {}
        self.report_row_heights = {}

        self.set_row("Data", 1, ["Sn.", "HR ID", "Staff Name", "Email", "Designation", "Department", "", "", "On Schedule", "Department", "January", "01", "2025", "Low", "Very Difficult"])
        self.set_row("Data", 2, ["1", "10", "Alice Example", "", "Programmer", "Programming", "", "", "Behind Schedule", "Management", "February", "02", "2026", "Medium", "Difficult"])
        self.set_row("Data", 3, ["2", "11", "Bob Example", "", "Manager", "Management", "", "", "", "Programming", "March", "03", "", "High", "Moderate"])
        self.set_row("Data", 4, ["", "", "Office Assistant", "", "", "", "", "", "", "Support", "April", "04", "2027", "", "Easy"])

        # Seed Perf Standard: 2025 October (R4..R5) and 2026 October (R6..R7)
        self.set_row("Perf Standard", 2, ["Year", "Month", "Criteria", "Description", "Source", "Weight", "Target", "Achievement", "Score", "Remarks", "Supervisor Score", "Remarks", "SF"])
        self.set_row("Perf Standard", 4, ["2025", "October", "Punctuality", "Late Arrival/ Early Exit", "Attendance Report", "5", "0", "4", "4", "", "0", "", "0"])
        self.set_row("Perf Standard", 5, ["2025", "October", "", "Find and fix issues quickly", "Reporting", "10", "10", "10", "10", "", "-1", "Good", "0"])
        self.set_row("Perf Standard", 6, ["2026", "October", "Punctuality", "Late Arrival/ Early Exit", "Attendance Report", "5", "0", "0", "5", "On time", "-1", "Keep going", "0"])
        self.set_row("Perf Standard", 7, ["2026", "October", "", "Find and fix issues quickly", "Reporting", "10", "10", "10", "10", "", "0", "", "0"])
        self.set_cell("Perf Standard", 8, 13, "0")  # Formula M column on empty row should not count as used row
        self.perf_h_dv[("Perf Standard", 4)] = ["0", "4", "5"]
        self.perf_h_dv[("Perf Standard", 6)] = ["0", "4", "5"]

        self.set_row("Tasks", 5, ["", "Name", "Alice Example", ""])
        self.set_row("Tasks", 6, ["", "Department", "Programming", ""])
        self.set_row("Tasks", 7, ["", "Employee ID", "10"] + [""] * 18 + ["TRUE", "TRUE"])

        self.set_row("Tasks", 9, ["1", "11/01/2026", "Task One", "Bob Example", "Management", "High", "11/01/2026", "11/01/2026", "11/10/2026", "11/10/2026", "4", "4", "100.00%", "On Schedule", "", "0", "Outcome 1", "Moderate", "", "OK", "", "1", "1"])
        self.set_row("Tasks", 10, ["2", "11/05/2026", "Task Two", "", "Programming", "Medium", "11/05/2026", "", "11/25/2026", "", "8", "", "0.00%", "On Schedule", "", "0", "Outcome 2", "Easy", "", "", "", "1", "1"])
        self.set_cell("Tasks", 11, 1, "3")
        self.set_cell("Tasks", 11, 16, "0")
        self.set_cell("Tasks", 11, 22, "1")
        self.set_cell("Tasks", 11, 23, "1")

        self.set_row("Work log", 10, ["1", "11/19/26", "WL One", "Project Alpha", "30", "Complete", "Note 1"])
        self.set_row("Work log", 11, ["2", "11/20/26", "WL Two", "Project Beta", "60", "On-going", "Note 2"])
        self.set_cell("Work log", 12, 1, "3")

        # Seed Report tab (rows 1..7 with values + H7/I7 filter, row 2 hidden, row 8 bordered signature)
        self.set_row("Report", 1, ["Monthly Employee Performance Report", "", "", ""])
        self.set_row("Report", 2, ["Hidden Helper", "Secret Note", "", ""])
        self.set_row("Report", 3, ["Employee", "Alice Example", "Score", "95%"])
        self.set_row("Report", 4, ["Summary", "On track", "", "Approved"])
        self.set_row("Report", 6, ["Name", "", "Alice Example", "", "", "", "Report for the month"])
        self.set_row("Report", 7, ["Employee ID", "", "10", "", "", "", "of", "October", "2026"])
        self.report_hidden_rows = {2}
        self.report_merges = [{"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 4}]
        self.report_col_widths = {1: 140, 2: 200, 3: 110, 4: 120}
        self.report_row_heights = {1: 32, 3: 24, 8: 28}
        self.report_dv[(7, 8)] = ["October", "November", "December"]
        self.report_formats[(1, 1)] = {
            "backgroundColor": {"red": 0.1, "green": 0.2, "blue": 0.6},
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True, "fontSize": 14},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "borders": {"top": {"style": "SOLID"}, "left": {"style": "SOLID"}, "bottom": {"style": "SOLID"}},
        }
        self.report_formats[(1, 4)] = {
            "borders": {"right": {"style": "SOLID_MEDIUM"}, "bottom": {"style": "SOLID"}},
        }
        self.report_formats[(3, 4)] = {
            "horizontalAlignment": "RIGHT",
            "textFormat": {"bold": True},
            "wrapStrategy": "WRAP",
        }
        self.report_formats[(8, 1)] = {
            "borders": {"bottom": {"style": "DASHED"}},
        }

    def set_cell(self, sheet, r, c, val):
        self.cells[(sheet, r, c)] = val

    def set_row(self, sheet, r, vals):
        for ci, v in enumerate(vals, start=1):
            self.cells[(sheet, r, ci)] = v

    @staticmethod
    def col_to_num(col_str):
        n = 0
        for ch in col_str:
            n = n * 26 + (ord(ch) - 64)
        return n

    @staticmethod
    def parse_a1(rng):
        sheet, coords = rng.split("!")
        sheet = sheet.strip("'")
        parts = coords.split(":")
        m1 = re.fullmatch(r"([A-Z]+)(\d+)", parts[0])
        c1, r1 = FakeSheetsMemory.col_to_num(m1.group(1)), int(m1.group(2))
        if len(parts) == 1:
            return sheet, r1, c1, r1, c1
        m2 = re.fullmatch(r"([A-Z]+)(\d+)", parts[1])
        c2, r2 = FakeSheetsMemory.col_to_num(m2.group(1)), int(m2.group(2))
        return sheet, r1, c1, r2, c2

    def read_range(self, rng):
        sheet, r1, c1, r2, c2 = self.parse_a1(rng)
        out = []
        for r in range(r1, r2 + 1):
            out.append([self.cells.get((sheet, r, c), "") for c in range(c1, c2 + 1)])
        while out and all(v == "" for v in out[-1]):
            out.pop()
        return out

    def write_range(self, rng, values):
        self.written_ranges.append(rng)
        sheet, r1, c1, r2, c2 = self.parse_a1(rng)
        for ro, row_vals in enumerate(values):
            for co, v in enumerate(row_vals):
                self.cells[(sheet, r1 + ro, c1 + co)] = "" if v is None else str(v)

    def build_service(self):
        mem = self

        class ValuesAPI:
            def get(self, spreadsheetId, range, valueRenderOption=None):
                return FakeExecute({"values": mem.read_range(range)})

            def batchGet(self, spreadsheetId, ranges, valueRenderOption=None):
                return FakeExecute({"valueRanges": [{"values": mem.read_range(r)} for r in ranges]})

            def update(self, spreadsheetId, range, valueInputOption, body):
                mem.write_range(range, body["values"])
                return FakeExecute({})

            def batchUpdate(self, spreadsheetId, body):
                for item in body["data"]:
                    mem.write_range(item["range"], item["values"])
                return FakeExecute({})

        class SpreadsheetsAPI:
            def get(self, spreadsheetId, includeGridData=False, ranges=None, fields=None):
                if ranges and fields:
                    sheet_title, r1, c1, r2, c2 = mem.parse_a1(ranges[0])
                    if "Perf Standard" in sheet_title:
                        row_data = []
                        for rn in range(r1, r2 + 1):
                            opts = mem.perf_h_dv.get((sheet_title, rn), [])
                            if opts:
                                dv = {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in opts]}}
                                row_data.append({"values": [{"dataValidation": dv}]})
                            else:
                                row_data.append({"values": [{}]})
                        return FakeExecute({"sheets": [{"data": [{"rowData": row_data}]}]})
                    if sheet_title == "Report":
                        row_data = []
                        row_meta = []
                        for rn in range(r1, r2 + 1):
                            row_vals = []
                            for cn in range(c1, c2 + 1):
                                val = mem.cells.get((sheet_title, rn, cn), "")
                                cell_d = {"formattedValue": "" if val is None else str(val)}
                                if (rn, cn) in mem.report_formats:
                                    cell_d["effectiveFormat"] = mem.report_formats[(rn, cn)]
                                if (rn, cn) in mem.report_dv:
                                    cell_d["dataValidation"] = {
                                        "condition": {
                                            "type": "ONE_OF_LIST",
                                            "values": [{"userEnteredValue": v} for v in mem.report_dv[(rn, cn)]],
                                        }
                                    }
                                row_vals.append(cell_d)
                            row_data.append({"values": row_vals})
                            row_meta.append({
                                "hiddenByUser": rn in mem.report_hidden_rows,
                                "pixelSize": mem.report_row_heights.get(rn, 21),
                            })
                        col_meta = [
                            {
                                "hiddenByUser": cn in mem.report_hidden_cols,
                                "pixelSize": mem.report_col_widths.get(cn, 100),
                            }
                            for cn in range(c1, c2 + 1)
                        ]
                        return FakeExecute({
                            "sheets": [{
                                "data": [{
                                    "rowData": row_data,
                                    "rowMetadata": row_meta,
                                    "columnMetadata": col_meta,
                                }],
                                "merges": list(mem.report_merges),
                            }]
                        })
                return FakeExecute(mem.meta)

            def batchUpdate(self, spreadsheetId, body):
                id_to_title = {s["properties"]["sheetId"]: s["properties"]["title"] for s in mem.meta["sheets"]}
                for req in body.get("requests", []):
                    mem.batch_requests.append(req)
                    if "copyPaste" in req:
                        cp = req["copyPaste"]
                        src = cp["source"]
                        dst = cp["destination"]
                        st_title = id_to_title[src["sheetId"]]
                        for off in range(src["endRowIndex"] - src["startRowIndex"]):
                            sr = src["startRowIndex"] + 1 + off
                            dr = dst["startRowIndex"] + 1 + off
                            for c in range(src["startColumnIndex"] + 1, src["endColumnIndex"] + 1):
                                mem.cells[(st_title, dr, c)] = mem.cells.get((st_title, sr, c), "")
                            if (st_title, sr) in mem.perf_h_dv:
                                mem.perf_h_dv[(st_title, dr)] = list(mem.perf_h_dv[(st_title, sr)])
                return FakeExecute({})

            def values(self):
                return ValuesAPI()

        svc = MagicMock()
        svc.spreadsheets.return_value = SpreadsheetsAPI()
        return svc


class TaskWorklogTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_md = os.path.join(self.tmpdir, "Work_Log_History.md")
        self.tmp_cfg = os.path.join(self.tmpdir, "task_worklog_config.json")
        self.tmp_token = os.path.join(self.tmpdir, "token.json")
        self.cfg_patcher = patch.object(twm, "CONFIG_FILE", self.tmp_cfg)
        self.md_patcher = patch.object(twm, "WORK_LOG_MD_FILE", self.tmp_md)
        self.tok_patcher = patch.object(twm, "TOKEN_FILE", self.tmp_token)
        self.cfg_patcher.start()
        self.md_patcher.start()
        self.tok_patcher.start()
        self.mem = FakeSheetsMemory()
        self.svc = self.mem.build_service()

    def tearDown(self):
        self.cfg_patcher.stop()
        self.md_patcher.stop()
        self.tok_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_run_needs_setup(self):
        boot = twm.fetch_bootstrap_data()
        self.assertTrue(boot["needs_setup"])
        self.assertEqual(boot["tasks"], [])
        self.assertEqual(boot["worklogs"], [])

    def test_setup_url_parsing_and_connection(self):
        self.assertEqual(
            twm.parse_spreadsheet_id("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit?gid=0#gid=0"),
            "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        )
        with self.assertRaises(twm.ApiError):
            twm.parse_spreadsheet_id("https://example.com/invalid")

        with patch.object(twm, "get_sheets_service", return_value=self.svc):
            probe = twm.test_setup_connection({
                "sheet_url": "https://docs.google.com/spreadsheets/d/1SampleSheetId1234567890abcdef/edit",
            })
            self.assertEqual(probe["employee_name"], "Alice Example")
            self.assertEqual(probe["tasks_tab"], "Tasks")
            self.assertEqual(probe["worklog_tab"], "Work log")

            boot = twm.apply_setup({
                "sheet_url": "https://docs.google.com/spreadsheets/d/1SampleSheetId1234567890abcdef/edit",
            })
            self.assertFalse(boot["needs_setup"])
            self.assertEqual(len(boot["tasks"]), 2)
            self.assertEqual(len(boot["worklogs"]), 2)

    def test_tasks_crud_preserves_formula_columns(self):
        with patch.object(twm, "get_sheets_service", return_value=self.svc):
            twm.apply_setup({"sheet_url": "1SampleSheetId1234567890abcdef"})

            res = twm.create_task({
                "date_of_entry": "2026-11-20",
                "detail": "Task Three",
                "priority": "High",
                "difficulty": "Difficult",
                "expected_outcome": "Outcome 3",
                "est_start_date": "2026-11-20",
                "expected_deadline": "2026-11-28",
                "est_time": "6",
                "pct_completed": "0",
            })
            self.assertEqual(res["affected_row"], 11)
            self.assertEqual(len(res["tasks"]), 3)

            res_upd = twm.update_task(10, {
                "date_of_entry": "2026-11-05",
                "detail": "Task Two Updated",
                "priority": "High",
                "difficulty": "Easy",
                "expected_outcome": "Outcome 2 Updated",
                "est_start_date": "2026-11-05",
                "expected_deadline": "2026-11-25",
                "est_time": "10",
                "pct_completed": "50",
                "expected_snapshot": {"detail": "Task Two", "date_of_entry": "11/05/2026"},
            })
            self.assertEqual([t for t in res_upd["tasks"] if t["row"] == 10][0]["detail"], "Task Two Updated")

            with self.assertRaises(twm.ApiError) as ctx:
                twm.update_task(9, {
                    "date_of_entry": "2026-11-01",
                    "detail": "Edited",
                    "priority": "High",
                    "difficulty": "Moderate",
                    "expected_outcome": "Outcome 1",
                    "est_start_date": "2026-11-01",
                    "act_start_date": "2026-11-01",
                    "expected_deadline": "2026-11-10",
                    "completion_date": "2026-11-10",
                    "est_time": "4",
                    "act_time": "4",
                    "pct_completed": "100",
                    "expected_snapshot": {"detail": "STALE", "date_of_entry": "11/01/2026"},
                })
            self.assertEqual(ctx.exception.status, 409)

            self.mem.written_ranges.clear()
            res_del = twm.delete_task(9, expected={"detail": "Task One", "date_of_entry": "11/01/2026"})
            self.assertEqual(len(res_del["tasks"]), 2)
            self.assertEqual(self.mem.cells[("Tasks", 9, 3)], "Task Two Updated")
            self.assertEqual(self.mem.cells[("Tasks", 10, 3)], "Task Three")
            self.assertEqual(self.mem.cells[("Tasks", 11, 3)], "")
            self.assertEqual(self.mem.cells[("Tasks", 9, 1)], "1")
            self.assertEqual(self.mem.cells[("Tasks", 9, 14)], "On Schedule")
            for rng in self.mem.written_ranges:
                self.assertNotRegex(rng, r"!(A|N|P|V|W)\d")

    def test_worklog_crud_and_md_sync(self):
        with patch.object(twm, "get_sheets_service", return_value=self.svc):
            twm.apply_setup({"sheet_url": "1SampleSheetId1234567890abcdef"})

            res = twm.create_worklog({
                "date": "2026-11-20",
                "task": "WL Three",
                "project": "Project Gamma",
                "time_minutes": "45",
                "status": "on-going",
                "comments": "In progress",
                "sync_md": True,
            })
            self.assertEqual(res["affected_row"], 12)
            self.assertEqual(res["worklogs"][-1]["status"], "On-going")
            self.assertTrue(os.path.isfile(self.tmp_md))

            with open(self.tmp_md, "r", encoding="utf-8") as f:
                md_text = f.read()
            self.assertIn("WL Three", md_text)

            self.mem.written_ranges.clear()
            res_del = twm.delete_worklog(
                10,
                expected={"task": "WL One", "project": "Project Alpha", "date": "11/19/26"},
                sync_md=True,
            )
            self.assertEqual(len(res_del["worklogs"]), 2)
            self.assertEqual(self.mem.cells[("Work log", 10, 3)], "WL Two")
            self.assertEqual(self.mem.cells[("Work log", 11, 3)], "WL Three")
            self.assertEqual(self.mem.cells[("Work log", 12, 3)], "")
            self.assertEqual(self.mem.cells[("Work log", 10, 1)], "1")
            for rng in self.mem.written_ranges:
                self.assertNotRegex(rng, r"!A\d")

    def test_perf_standard_months_copy_and_achievement_only(self):
        with patch.object(twm, "get_sheets_service", return_value=self.svc):
            twm.apply_setup({"sheet_url": "1SampleSheetId1234567890abcdef"})
            boot = twm.fetch_bootstrap_data(service=self.svc)
            perf = boot["perf"]
            self.assertTrue(perf["has_tab"])
            self.assertEqual(perf["latest_month_key"], "2026-10")
            self.assertEqual([m["key"] for m in perf["months"]], ["2026-10", "2025-10"])
            self.assertEqual(perf["next_year"], "2026")
            self.assertEqual(perf["next_month"], "November")
            oct_rows = [it for it in perf["items"] if it["month_key"] == "2026-10"]
            self.assertEqual(oct_rows[0]["h_options"], ["0", "4", "5"])

            # Create new month copied from 2026 October -> lands at rows 8..9 (ignoring empty SF col row 8)
            created = twm.create_perf_month({
                "year": "2026",
                "month": "November",
                "source_year": "2026",
                "source_month": "October",
                "achievements": ["4", "8"],
            })
            self.assertEqual(created["created_month_key"], "2026-11")
            self.assertEqual(created["perf"]["latest_month_key"], "2026-11")
            nov_rows = [it for it in created["perf"]["items"] if it["month_key"] == "2026-11"]
            self.assertEqual([r["row"] for r in nov_rows], [8, 9])
            self.assertEqual(nov_rows[0]["criteria"], "Punctuality")
            self.assertEqual(nov_rows[0]["achievement"], "4")
            self.assertEqual(nov_rows[1]["achievement"], "8")
            self.assertEqual(nov_rows[0]["remarks"], "")
            self.assertEqual(nov_rows[0]["supervisor_score"], "")
            self.assertEqual(nov_rows[0]["supervisor_remarks"], "")
            self.assertEqual(nov_rows[0]["h_options"], ["0", "4", "5"])

            # Duplicate month blocked
            with self.assertRaises(twm.ApiError) as dup_ctx:
                twm.create_perf_month({
                    "year": "2026",
                    "month": "November",
                    "source_year": "2026",
                    "source_month": "October",
                })
            self.assertEqual(dup_ctx.exception.status, 409)

            # Old month is view-only
            with self.assertRaises(twm.ApiError) as old_ctx:
                twm.update_perf_achievements({
                    "year": "2026",
                    "month": "October",
                    "updates": [{"row": 6, "achievement": "4", "expected_description": "Late Arrival/ Early Exit"}],
                })
            self.assertEqual(old_ctx.exception.status, 403)

            # Invalid ONE_OF_LIST option rejected
            with self.assertRaises(twm.ApiError):
                twm.update_perf_achievements({
                    "year": "2026",
                    "month": "November",
                    "updates": [{"row": 8, "achievement": "3", "expected_description": "Late Arrival/ Early Exit"}],
                })

            # Valid edit updates ONLY Column H on latest month
            self.mem.written_ranges.clear()
            upd = twm.update_perf_achievements({
                "year": "2026",
                "month": "November",
                "updates": [{"row": 8, "achievement": "5", "expected_description": "Late Arrival/ Early Exit"}],
            })
            self.assertEqual(self.mem.written_ranges, ["'Perf Standard'!H8"])
            nov_upd = [it for it in upd["perf"]["items"] if it["row"] == 8][0]
            self.assertEqual(nov_upd["achievement"], "5")

    def test_supervision_mode_supervisor_comments_only(self):
        with patch.object(twm, "get_sheets_service", return_value=self.svc):
            boot = twm.apply_setup({
                "sheet_url": "1SampleSheetId1234567890abcdef",
                "role_mode": "supervisor",
            })
            self.assertEqual(boot["setup"]["role_mode"], "supervisor")

            # Employee mutations & creations are blocked (403) in Supervision mode
            for fn in (
                lambda: twm.create_task({"detail": "Blocked"}),
                lambda: twm.update_task(9, {"detail": "Blocked"}),
                lambda: twm.delete_task(9),
                lambda: twm.create_worklog({"task": "Blocked", "project": "X", "time_minutes": "10"}),
                lambda: twm.update_worklog(10, {"task": "Blocked", "project": "X", "time_minutes": "10"}),
                lambda: twm.delete_worklog(10),
                lambda: twm.create_perf_month({"year": "2026", "month": "November", "source_year": "2026", "source_month": "October"}),
                lambda: twm.update_perf_achievements({"year": "2026", "month": "October", "updates": [{"row": 6, "achievement": "4"}]}),
            ):
                with self.assertRaises(twm.ApiError) as err_ctx:
                    fn()
                self.assertEqual(err_ctx.exception.status, 403)

            # Empty task row 11 cannot receive supervisor comment (404)
            with self.assertRaises(twm.ApiError) as empty_ctx:
                twm.update_task_supervisor_comment(11, {"supervisor_comments": "Test"})
            self.assertEqual(empty_ctx.exception.status, 404)

            # Stale task snapshot triggers 409
            with self.assertRaises(twm.ApiError) as stale_ctx:
                twm.update_task_supervisor_comment(9, {
                    "supervisor_comments": "Updated comment",
                    "expected_snapshot": {"detail": "WRONG", "date_of_entry": "11/01/2026"},
                })
            self.assertEqual(stale_ctx.exception.status, 409)

            # Valid supervisor comment updates ONLY Tasks Col T
            self.mem.written_ranges.clear()
            res_task = twm.update_task_supervisor_comment(9, {
                "supervisor_comments": "Reviewed & approved",
                "expected_snapshot": {"detail": "Task One", "date_of_entry": "11/01/2026"},
            })
            self.assertEqual(self.mem.written_ranges, ["'Tasks'!T9"])
            row9 = [t for t in res_task["tasks"] if t["row"] == 9][0]
            self.assertEqual(row9["supervisor_comments"], "Reviewed & approved")
            self.assertEqual(row9["detail"], "Task One")

            # Valid Perf Standard supervisor review updates ONLY Col K (score) and Col L (remarks)
            self.mem.written_ranges.clear()
            res_perf = twm.update_perf_supervisor_remarks({
                "year": "2026",
                "month": "October",
                "updates": [{
                    "row": 6,
                    "supervisor_score": "-2",
                    "supervisor_remarks": "Need stricter punctuality",
                    "expected_description": "Late Arrival/ Early Exit",
                }],
            })
            self.assertEqual(self.mem.written_ranges, ["'Perf Standard'!K6:L6"])
            perf6 = [it for it in res_perf["perf"]["items"] if it["row"] == 6][0]
            self.assertEqual(perf6["supervisor_score"], "-2")
            self.assertEqual(perf6["supervisor_remarks"], "Need stricter punctuality")
            self.assertEqual(perf6["achievement"], "0")

            # Editing ONLY Col K preserves existing Col L remarks
            self.mem.written_ranges.clear()
            res_score_only = twm.update_perf_supervisor_remarks({
                "year": "2026",
                "month": "October",
                "updates": [{"row": 6, "supervisor_score": "0", "expected_description": "Late Arrival/ Early Exit"}],
            })
            self.assertEqual(self.mem.written_ranges, ["'Perf Standard'!K6"])
            perf6_after = [it for it in res_score_only["perf"]["items"] if it["row"] == 6][0]
            self.assertEqual(perf6_after["supervisor_score"], "0")
            self.assertEqual(perf6_after["supervisor_remarks"], "Need stricter punctuality")

            # Non-numeric Supervisor Score rejected
            with self.assertRaises(twm.ApiError):
                twm.update_perf_supervisor_remarks({
                    "year": "2026",
                    "month": "October",
                    "updates": [{"row": 6, "supervisor_score": "abc", "expected_description": "Late Arrival/ Early Exit"}],
                })

            # Switch back to Employee mode: supervisor-only helpers reject with 403
            boot_emp = twm.apply_setup({
                "sheet_url": "1SampleSheetId1234567890abcdef",
                "role_mode": "employee",
            })
            self.assertEqual(boot_emp["setup"]["role_mode"], "employee")
            with self.assertRaises(twm.ApiError) as sup_only1:
                twm.update_task_supervisor_comment(9, {"supervisor_comments": "Blocked in emp mode"})
            self.assertEqual(sup_only1.exception.status, 403)
            with self.assertRaises(twm.ApiError) as sup_only2:
                twm.update_perf_supervisor_remarks({"year": "2026", "month": "October", "updates": [{"row": 6, "supervisor_remarks": "Blocked"}]})
            self.assertEqual(sup_only2.exception.status, 403)

    def test_report_tab_view_only_and_formatting(self):
        with patch.object(twm, "get_sheets_service", return_value=self.svc):
            boot = twm.apply_setup({
                "sheet_url": "1SampleSheetId1234567890abcdef",
                "role_mode": "employee",
            })
            self.assertTrue(boot["report"]["has_tab"])
            self.assertEqual(boot["report"]["sheet_id"], 505)
            self.assertIn("format=pdf&gid=505", boot["report"]["export_pdf_base"])
            self.assertIn("gid=505", boot["report"]["sheet_url"])

            self.mem.written_ranges.clear()
            self.mem.batch_requests.clear()
            rep = twm.fetch_report_data()
            self.assertTrue(rep["has_tab"])
            self.assertFalse(rep["empty"])
            # Rows 1..8 minus hidden row 2 -> 7 visible rows
            self.assertEqual(len(rep["rows"]), 7)
            self.assertEqual(rep["row_numbers"], [1, 3, 4, 5, 6, 7, 8])
            flat_texts = [c["v"] for row in rep["rows"] for c in row if c["v"]]
            self.assertNotIn("Hidden Helper", flat_texts)
            self.assertEqual(rep["merges"], [{"r": 0, "c": 0, "rs": 1, "cs": 4}])
            self.assertEqual(rep["filter"]["month"], "October")
            self.assertEqual(rep["filter"]["year"], "2026")
            self.assertEqual(rep["filter"]["month_options"], ["October", "November", "December"])
            self.assertIn("2026", rep["filter"]["year_options"])

            title_cell = rep["rows"][0][0]
            self.assertEqual(title_cell["v"], "Monthly Employee Performance Report")
            self.assertEqual(title_cell["bg"], "#1a3399")
            self.assertEqual(title_cell["fg"], "#ffffff")
            self.assertEqual(title_cell["b"], 1)
            self.assertEqual(title_cell["fs"], 14)
            self.assertEqual(title_cell["ha"], "c")
            self.assertEqual(title_cell["va"], "m")
            self.assertEqual(title_cell["br"], "2px solid #000000")
            self.assertEqual(title_cell["bb"], "1px solid #000000")

            score_cell = rep["rows"][1][3]
            self.assertEqual(score_cell["v"], "95%")
            self.assertEqual(score_cell["ha"], "r")
            self.assertEqual(score_cell["w"], 1)

            sig_cell = rep["rows"][6][0]
            self.assertEqual(sig_cell["bb"], "1px dashed #000000")
            self.assertEqual(self.mem.written_ranges, [])
            self.assertEqual(self.mem.batch_requests, [])

            # Filtering via H7 & I7 updates ONLY Report!H7:I7
            filtered = twm.update_report_filter({"month": "november", "year": "2027"})
            self.assertEqual(self.mem.written_ranges, ["'Report'!H7:I7"])
            self.assertEqual(filtered["filter"]["month"], "November")
            self.assertEqual(filtered["filter"]["year"], "2027")

            with self.assertRaises(twm.ApiError) as bad_filter:
                twm.update_report_filter({"month": "", "year": "2027"})
            self.assertEqual(bad_filter.exception.status, 400)

            # Works in Supervision mode as well (view & filter H7:I7)
            twm.apply_setup({
                "sheet_url": "1SampleSheetId1234567890abcdef",
                "role_mode": "supervisor",
            })
            self.mem.written_ranges.clear()
            rep_sup = twm.fetch_report_data()
            self.assertFalse(rep_sup["empty"])
            self.assertEqual(rep_sup["filter"]["month"], "November")
            self.assertEqual(self.mem.written_ranges, [])
            rep_sup_filtered = twm.update_report_filter({"month": "December", "year": "2026"})
            self.assertEqual(self.mem.written_ranges, ["'Report'!H7:I7"])
            self.assertEqual(rep_sup_filtered["filter"]["month"], "December")
            self.assertEqual(rep_sup_filtered["filter"]["year"], "2026")

            # Missing Report tab degrades cleanly
            self.mem.meta["sheets"] = [s for s in self.mem.meta["sheets"] if s["properties"]["title"] != "Report"]
            rep_missing = twm.fetch_report_data()
            self.assertFalse(rep_missing["has_tab"])
            self.assertTrue(rep_missing["empty"])
            self.assertEqual(rep_missing["rows"], [])
            with self.assertRaises(twm.ApiError) as missing_filter:
                twm.update_report_filter({"month": "October", "year": "2026"})
            self.assertEqual(missing_filter.exception.status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
