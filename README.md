# Tasks & Work Log Manager

A lightweight web application for **creating, editing, filtering, and safely deleting** records in the Employee Monthly Reporting Google Sheet (`Tasks` and `Work log` tabs), with multi-sheet switching via a built-in **Setup** modal.

Works in **two modes** using the same interface:
1. **Always-Online Static Mode (GitHub Pages):** Hosted at **https://mbnepal.github.io/task-worklog-manager/** — lets **every user sign in with their own Google account** directly in the browser (`Sign in with Google`) and enter their Google Sheet link.
2. **Local Python Server Mode:** Run `python task_worklog_manager.py` locally with automatic `Work_Log_History.md` disk syncing.

## Features

- **Per-User Google Sign-In (`Sign in with Google`)**
  - Each team member signs in with their own Google account using Google Identity Services (no backend server and no OAuth secrets stored on the host).
  - Enter your Google Sheet link, click **Test Connection** to sign in and verify sheet access, then **Save & Load Sheet**.
  - Supports **Switch Google Account**, **Sign Out**, and a 1-click **🔗 Copy Team Link** so colleagues don't need to re-enter the Web Client ID.
- **Tasks Tab (`Tasks`)**
  - Create, edit, quick-complete, duplicate, and safely delete tasks across editable columns (`B:M`, `O`, `Q:U`).
  - **Formula-Safe Operations:** Never overwrites sheet-wide `ARRAYFORMULA` columns (`A`, `N`, `P`, `V`, `W`). Deleting a row shifts subsequent rows' editable cells upward and clears the bottom row instead of using `deleteDimension`, preserving row-9 `ARRAYFORMULA`s, `V7`/`W7` checks, data validations, and `Report` tab `VLOOKUP`s.
  - **Live Dropdowns & Validation Preview:** Reads staff, departments, priorities, and difficulty options from the `Data` tab (with built-in fallbacks if `Data` is absent) and previews `Check for Employee` (`V`) requirements before saving.
- **Work Log Tab (`Work log`)**
  - Create, edit, quick-complete (with editable date & time dialog), duplicate, and safely delete daily work log entries across `B:G`.
  - Quick duration chips (`15m`–`240m`), project autocomplete from existing records, date range presets, and `Work_Log_History.md` export/sync.
- **Perf Standard Tab (`Perf Standard`)**
  - View historical months read-only via the Month selector, or click **+ New Month** to copy KPI structure, weights, targets, Score formulas (`Col I`), `SF` (`Col M`), and dropdown validations from any previous month (`PASTE_NORMAL`), clearing old remarks and supervisor scores (`J:L`).
  - Strictly locks all columns except **Achievement (`Col H`)** on the current/latest month (respecting `ONE_OF_LIST` validations where configured).
- **Report Tab (`Report` — Read-Only View & Print)**
  - Displays the live formatted Google Sheet `Report` tab (merged cells, colors, fonts, borders, alignment, and visible rows/columns) in a strictly read-only paper preview.
  - Supports **Print Report** (`@media print` with A4 Portrait/Landscape & Fit-to-width options) as well as **Google Sheet Print PDF** (`export?format=pdf&gid=...`) to print directly in Google Sheets' native print format.
- **Supervision Mode (`Setup → Supervision (Supervisor)`)**
  - Supervisors can select **Supervision (Supervisor)** in **Setup** before opening a staff member's Google Sheet.
  - Locks all employee fields, Work Log entries, KPI Achievements (`Col H`), and row creation/completion/deletion across the app and backends, allowing edits **only** to Supervisor fields (**Tasks `Col T` Supervisor Comments** and **Perf Standard `Col K` Supervisor Score + `Col L` Supervisor Remarks**).
- **Conflict Protection**
  - Detects external edits before `PUT` / `DELETE` and refreshes the UI instead of overwriting changed rows.

---

## Option A: Use Online on GitHub Pages (Multi-User Google Sign-In — Recommended)

Open: **https://mbnepal.github.io/task-worklog-manager/**

### Everyday User Flow (For Any Employee — Zero Developer Setup)
1. Open **https://mbnepal.github.io/task-worklog-manager/**.
2. Paste your **Google Sheet Link** (`https://docs.google.com/spreadsheets/d/.../edit`).
3. Click **Test Connection** (or **Connect & Open Sheet**) → Google prompts you to **Sign in with your Google account**.
4. Done — your Tasks and Work Log load immediately!

### Admin Note (Google OAuth Audience / Test Users)
The app's public Web OAuth Client ID for `https://mbnepal.github.io` is already pre-configured in `sheets_browser.js` (`DEFAULT_WEB_CLIENT_ID`). Until the app is published for everyone, add team members' Google emails under **Google Cloud Console → Google Auth Platform → Audience → Test users** (or complete **Branding** and click **Publish app**).

*(To point a fork or custom domain to your own OAuth Web Client ID, update `DEFAULT_WEB_CLIENT_ID` in `sheets_browser.js` or expand **Advanced Options** in Setup.)*

---

## Option B: Run Locally with Python

### Prerequisites
- Python 3.10+
- A Google Cloud **Desktop app** OAuth 2.0 Client ID JSON file.

### Installation & Run
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Save your Google Cloud Desktop OAuth JSON file as:
   ```text
   config/client_secret.json
   ```
   *(Or set `TASK_MANAGER_CREDENTIALS` to its path.)*
3. Start the server:
   ```bash
   python task_worklog_manager.py
   ```
   Opens `http://127.0.0.1:8766/` (override port with `--port` or `TASK_MANAGER_PORT`; pass `--no-open` to skip opening the browser).

---

## Expected Google Sheet Structure

- **`Tasks` tab**:
  - Employee metadata in `C5` (Name), `C6` (Department), `C7` (Employee ID), `V7`/`W7` (Report checks), `D5`/`D6` (Status messages).
  - Task rows starting at Row 9 (`B:M`, `O`, `Q:U` editable; `A`, `N`, `P`, `V`, `W` reserved for sheet-wide `ARRAYFORMULA`s).
- **`Work log` tab**:
  - Entries starting at Row 10 (`B` Date, `C` Task, `D` Client/Project, `E` Time in minutes, `F` Status `On-going`/`Complete`, `G` Comments; `A` reserved for `ARRAYFORMULA`).
- **`Data` tab (optional)**:
  - Populates dropdown choices (`C` Staff Name, `J` Department, `N` Priority, `O` Difficulty, `I` Schedule Status).
- **`Report` tab (optional, read-only)**:
  - Summary/printable monthly report tab rendered read-only with Google Sheet formatting and print / PDF export options.

## Running Tests

```bash
python -m unittest discover -s tests -v
```
