# Tasks & Work Log Manager

A lightweight web application for **creating, editing, filtering, and safely deleting** records in the Employee Monthly Reporting Google Sheet (`Tasks` and `Work log` tabs), with multi-sheet switching via a built-in **Setup** modal.

Works in **two modes** using the same interface:
1. **Always-Online Static Mode (GitHub Pages):** Hosted at **https://mbnepal.github.io/task-worklog-manager/** — lets **every user sign in with their own Google account** directly in the browser (`Sign in with Google`) and enter their Google Sheet link (with an optional Apps Script Web App tab as an alternative).
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
  - Create, edit, 1-click status toggle (`On-going` / `Complete`), duplicate, and safely delete daily work log entries across `B:G`.
  - Quick duration chips (`15m`–`240m`), project autocomplete from existing records, date range presets, and `Work_Log_History.md` export/sync.
- **Conflict Protection**
  - Detects external edits before `PUT` / `DELETE` and refreshes the UI instead of overwriting changed rows.

---

## Option A: Use Online on GitHub Pages (Multi-User Google Sign-In — Recommended)

Open: **https://mbnepal.github.io/task-worklog-manager/**

### One-Time Admin Setup (Once for Your Whole Organization)
1. Enable **Google Sheets API** in [Google Cloud Console](https://console.cloud.google.com/apis/library/sheets.googleapis.com).
2. Configure **OAuth consent screen / Audience** (`https://console.cloud.google.com/auth/audience`):
   - While in **Testing** status, add your team members' Google email addresses under **Test users** (up to 100 users, no Google app verification banner), **or** click **Publish App** to allow any Google account.
3. Under **[APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)**, click **Create Credentials → OAuth client ID → Web application**:
   - **Authorized JavaScript origins:** `https://mbnepal.github.io` *(no trailing slash)*
   - *(No Authorized redirect URIs needed for Google Identity Services popups.)*
4. Copy the generated **Client ID** (`...apps.googleusercontent.com`) and either:
   - Paste it once into `DEFAULT_WEB_CLIENT_ID` in `sheets_browser.js`, **OR**
   - Paste it in **⚙ Setup → Google OAuth Web Client ID Setup** and click **🔗 Copy Team Link** to share `?client_id=...` with teammates.

### Everyday User Flow (For Any Employee)
1. Open the app (or Team Link).
2. Paste your **Google Sheet Link or Spreadsheet ID** (e.g. `https://docs.google.com/spreadsheets/d/.../edit`).
3. Click **Test Connection** → Google asks you to **Sign in with your Google account** and verify the sheet.
4. Click **Save & Load Sheet** — done!

---

## Option B: Apps Script Web App (Alternative for Single Owner / Shared Admin Proxy)

If you prefer a fixed owner-executed endpoint without Google Cloud OAuth setup:
1. Inside **⚙ Setup**, switch to the **Apps Script Web App** tab.
2. Open **[script.google.com](https://script.google.com/home/projects/create)** → **New project**, copy [`apps_script.gs`](./apps_script.gs) into `Code.gs`, and Save.
3. Click **Deploy → New deployment → Web app** (**Execute as:** `Me`, **Who has access:** `Anyone`).
4. Paste the `/exec` URL and optional Google Sheet link in **⚙ Setup**, then click **Test Connection** → **Save & Load Sheet**.

---

## Option C: Run Locally with Python

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
