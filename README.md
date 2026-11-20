# Tasks & Work Log Manager

A lightweight web application for **creating, editing, filtering, and safely deleting** records in the Employee Monthly Reporting Google Sheet (`Tasks` and `Work log` tabs), with multi-sheet switching via a built-in **Setup** modal.

Works in **two modes** using the same interface:
1. **Always-Online Static Mode (GitHub Pages):** Hosted at **https://mbnepal.github.io/task-worklog-manager/** — connects directly to your Google Sheet via a lightweight `apps_script.gs` Web App (just like Sujan Store Inventory Manager — **no Google Cloud Console or OAuth Web Client ID required**).
2. **Local Python Server Mode:** Run `python task_worklog_manager.py` locally with automatic `Work_Log_History.md` disk syncing.

## Features

- **Tasks Tab (`Tasks`)**
  - Create, edit, quick-complete, duplicate, and safely delete tasks across editable columns (`B:M`, `O`, `Q:U`).
  - **Formula-Safe Operations:** Never overwrites sheet-wide `ARRAYFORMULA` columns (`A`, `N`, `P`, `V`, `W`). Deleting a row shifts subsequent rows' editable cells upward and clears the bottom row instead of using `deleteDimension`, preserving row-9 `ARRAYFORMULA`s, `V7`/`W7` checks, data validations, and `Report` tab `VLOOKUP`s.
  - **Live Dropdowns & Validation Preview:** Reads staff, departments, priorities, and difficulty options from the `Data` tab (with built-in fallbacks if `Data` is absent) and previews `Check for Employee` (`V`) requirements before saving.
- **Work Log Tab (`Work log`)**
  - Create, edit, 1-click status toggle (`On-going` / `Complete`), duplicate, and safely delete daily work log entries across `B:G`.
  - Quick duration chips (`15m`–`240m`), project autocomplete from existing records, date range presets, and `Work_Log_History.md` export/sync.
- **Multi-Sheet Setup (`⚙ Setup`)**
  - Connects to your Google Sheet via Apps Script Web App URL (with optional PIN and optional Google Sheet link override to access shared sheets).
  - Supports custom tab names (`Tasks`, `Work log`, `Data`), connection testing, and quick-switching between saved sheets.
- **Conflict Protection**
  - Detects external edits before `PUT` / `DELETE` and refreshes the UI instead of overwriting changed rows.

---

## Option A: Use Online on GitHub Pages (No OAuth Client ID Needed)

Open: **https://mbnepal.github.io/task-worklog-manager/**

### One-Time Setup via Google Apps Script (30 seconds)
1. Open your **Employee Monthly Reporting** Google Sheet → **Extensions → Apps Script**.
2. Copy the contents of [`apps_script.gs`](./apps_script.gs) (or click **📋 Copy apps_script.gs** directly inside the Setup modal) and paste it into `Code.gs`, then Save.
   - *(Optional)* Set `TWM_ACCESS_PIN` at the top of `apps_script.gs` if you want a PIN lock.
3. Click **Deploy → New deployment → Select type: Web app**:
   - **Execute as:** `Me`
   - **Who has access:** `Anyone`
4. Authorize once when prompted and copy the **Web App URL** (ends with `/exec`).
5. Paste the Web App URL into **⚙ Setup** on **https://mbnepal.github.io/task-worklog-manager/** and click **Test Connection** → **Save & Load Sheet**.

*(An alternative OAuth Web Client ID option is also tucked inside Setup if you ever want direct REST API calls without Apps Script.)*

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

## Running Tests

```bash
python -m unittest discover -s tests -v
```
