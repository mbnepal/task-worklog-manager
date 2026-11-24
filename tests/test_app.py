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
            ],
        }
        self.cells = {}
        self.written_ranges = []
        self.batch_requests = []
        self.perf_h_dv = {}

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
                if ranges and fields and "Perf Standard" in ranges[0]:
                    sheet_title, r1, _c1, r2, _c2 = mem.parse_a1(ranges[0])
                    row_data = []
                    for rn in range(r1, r2 + 1):
                        opts = mem.perf_h_dv.get((sheet_title, rn), [])
                        if opts:
                            dv = {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": v} for v in opts]}}
                            row_data.append({"values": [{"dataValidation": dv}]})
                        else:
                            row_data.append({"values": [{}]})
                    return FakeExecute({"sheets": [{"data": [{"rowData": row_data}]}]})
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
