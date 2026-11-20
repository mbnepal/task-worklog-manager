# Tasks & Work Log Manager

A lightweight local web application for **creating, editing, filtering, and safely deleting** records in the Employee Monthly Reporting Google Sheet (`Tasks` and `Work log` tabs), with multi-sheet switching via a built-in **Setup** modal.

## Features

- **Tasks Tab (`Tasks`)**
  - Create, edit, quick-complete, duplicate, and safely delete tasks across editable columns (`B:M`, `O`, `Q:U`).
  - **Formula-Safe Operations:** Never overwrites sheet-wide `ARRAYFORMULA` columns (`A`, `N`, `P`, `V`, `W`). Deleting a row shifts subsequent rows' editable cells upward and clears the bottom row instead of using `deleteDimension`, preserving row-9 `ARRAYFORMULA`s, `V7`/`W7` checks, data validations, and `Report` tab `VLOOKUP`s.
  - **Live Dropdowns & Validation Preview:** Reads staff, departments, priorities, and difficulty options from the `Data` tab (with sensible built-in fallbacks if `Data` is absent) and previews `Check for Employee` (`V`) requirements before saving.
- **Work Log Tab (`Work log`)**
  - Create, edit, 1-click status toggle (`On-going` / `Complete`), duplicate, and safely delete daily work log entries across `B:G`.
  - Quick duration chips (`15m`–`240m`), project autocomplete from existing records, date range presets, and optional local Markdown sync (`Work_Log_History.md`).
- **Multi-Sheet Setup (`⚙ Setup`)**
  - Paste any Google Sheets URL or Spreadsheet ID with matching `Tasks`, `Work log`, and `Data` tabs.
  - Customize tab names if needed, test the connection before saving, switch Google accounts (`Switch Google Account`), and quick-switch between saved sheets.
- **Conflict Protection**
  - Detects external edits before `PUT` / `DELETE` and refreshes the UI instead of overwriting changed rows.

## Prerequisites

- Python 3.10+
- A Google Cloud project with the **Google Sheets API** enabled and an **OAuth 2.0 Client ID (Desktop App)** JSON file.

## Installation & Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Add Google OAuth Desktop credentials:**
   - In Google Cloud Console, enable the **Google Sheets API**.
   - Create an **OAuth 2.0 Client ID** of type **Desktop app** and download the JSON file.
   - Save it inside this project as:
     ```text
     config/client_secret.json
     ```
     *(Alternatively, set the `TASK_MANAGER_CREDENTIALS` environment variable to the JSON file path.)*

3. **Run the server:**
   ```bash
   python task_worklog_manager.py
   ```
   By default it listens on `http://127.0.0.1:8766/` (override with `--port` or `TASK_MANAGER_PORT`) and opens your browser. Pass `--no-open` to skip auto-opening the browser.

4. **Connect your Google Sheet:**
   - Click **⚙ Setup** (opens automatically on first run).
   - Paste your Google Sheets link (or Spreadsheet ID).
   - Verify or customize tab names (`Tasks`, `Work log`, `Data`).
   - Click **Test Connection**, sign in with Google when prompted (using an account with **Editor** access to that sheet), and click **Save & Load Sheet**.

## Expected Google Sheet Structure

- **`Tasks` tab**:
  - Header/employee metadata in `C5` (Name), `C6` (Department), `C7` (Employee ID), `V7`/`W7` (Report checks), `D5`/`D6` (Status messages).
  - Task rows starting at Row 9 (`B:M`, `O`, `Q:U` editable; `A`, `N`, `P`, `V`, `W` reserved for sheet-wide `ARRAYFORMULA`s).
- **`Work log` tab**:
  - Entries starting at Row 10 (`B` Date, `C` Task, `D` Client/Project, `E` Time in minutes, `F` Status `On-going`/`Complete`, `G` Comments; `A` reserved for `ARRAYFORMULA`).
- **`Data` tab (optional)**:
  - Populates dropdown choices (`C` Staff Name, `J` Department, `N` Priority, `O` Difficulty, `I` Schedule Status).

## Configuration & Environment Variables

All runtime config and tokens are stored locally in `config/` (excluded from Git via `.gitignore`):

| Variable | Default | Description |
| --- | --- | --- |
| `TASK_MANAGER_PORT` | `8766` | Local HTTP server port |
| `TASK_MANAGER_CREDENTIALS` | `config/client_secret.json` | Path to Google OAuth Desktop client secrets JSON |
| `TASK_MANAGER_TOKEN` | `config/token.json` | Path to cached OAuth user token JSON |
| `TASK_MANAGER_CONFIG` | `config/task_worklog_config.json` | Path to saved spreadsheet setup JSON |
| `TASK_MANAGER_WORKLOG_MD` | `./Work_Log_History.md` | Optional local Markdown work log file path |

## Running Tests

```bash
python -m unittest discover -s tests -v
```
