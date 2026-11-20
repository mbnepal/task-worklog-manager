# Tasks & Work Log Manager

A lightweight web application for **creating, editing, filtering, and safely deleting** records in the Employee Monthly Reporting Google Sheet (`Tasks` and `Work log` tabs), with multi-sheet switching via a built-in **Setup** modal.

Works in **two modes** using the same interface:
1. **Always-Online Static Mode (GitHub Pages):** Hosted at **https://mbnepal.github.io/task-worklog-manager/** — runs 100% in your browser using Google Identity Services and the Google Sheets REST API v4.
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
  - Paste any Google Sheets URL or Spreadsheet ID with matching `Tasks`, `Work log`, and `Data` tabs.
  - Customize tab names if needed, test the connection before saving, switch Google accounts, and quick-switch between saved sheets.
- **Conflict Protection**
  - Detects external edits before `PUT` / `DELETE` and refreshes the UI instead of overwriting changed rows.

---

## Option A: Use Online on GitHub Pages (Always Online)

Open: **https://mbnepal.github.io/task-worklog-manager/**

### One-Time OAuth Setup (Web Application Client ID)
Because GitHub Pages is static HTML/JS, Google sign-in in the browser requires a **Web application** OAuth Client ID (public Client ID only — no client secret is used):

1. Open [Google Cloud Console](https://console.cloud.google.com/) → **APIs & Services → Library** and enable **Google Sheets API**.
2. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
3. Choose Application type: **Web application**.
4. Under **Authorized JavaScript origins**, add:
   - `https://mbnepal.github.io`
5. Copy the generated **Client ID** (`....apps.googleusercontent.com`).
6. On the web app's **⚙ Setup** screen:
   - Paste the **Google OAuth Web Client ID**.
   - Paste your **Google Sheet link** (ensure your Google account has Editor access to it).
   - Click **Test Connection**, sign in with Google, and click **Save & Load Sheet**.
   - *(Optional)* Click **Copy Team Link** inside Setup to share a URL (`?client_id=...`) with teammates so they don't have to look up the Client ID.

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
