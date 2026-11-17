# News / Webpage to PDF Archiver (Reader Mode & Multi-threaded)

A high-performance Python utility that reads URLs from `data.csv`, navigates to each URL concurrently (e.g. 5 pages at a time), cleanly extracts **ONLY the site logo, title/headline, main hero image, and article body** (stripping all ads, sidebars, popups, and widgets), and saves a publication-grade PDF into the `output/` directory named as `<baseurl>_<headline>.pdf`.

## Features

- **Pristine Reader Mode with Full-Page Fallback**: Extracts and compiles only:
  1. **Site Logo** (Header logo or high-res brand icon)
  2. **Headline / Title**
  3. **Main / Featured Hero Image**
  4. **Clean Article Body** (Clean paragraphs, quotes, subheadings, while removing ads, navbars, sidebars, related posts, and clutter)
  - *Fallback*: If no article body text is found on the target page, the archiver automatically captures and renders the full webpage directly to PDF.
- **Multi-threaded / Parallel Concurrency**: Scrapes multiple URLs simultaneously using asynchronous Playwright workers (default: 5 concurrent pages), dramatically reducing execution time.
- **Multilingual & Bengali Font Support**: Includes modern typography and Bangla font stacks (`Noto Sans Bengali`, `Kalpurush`, `SolaimanLipi`, system-ui).

## Setup

1. **Activate the Virtual Environment**:
   ```bash
   source .venv/bin/activate
   ```
   *(If initializing on a new machine: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && playwright install chromium`)*

## Usage

1. Add your URLs and optional dates to `data.csv`:
   ```csv
   date,url
   08.06.26,https://www.dainiksangbadpratidin.com/news/150433
   08.06.26,https://banglascoop.com/34518
   09.06.26,https://www.71sangbad.com/news/155068
   ```

2. Run the archiver (default 5 concurrent workers):
   ```bash
   .venv/bin/python archiver.py
   ```

3. Or customize concurrency and limits:
   ```bash
   # Run with 10 concurrent tabs
   .venv/bin/python archiver.py --concurrency 10

   # Test on only the first 5 URLs
   .venv/bin/python archiver.py --limit 5
   ```

4. The generated PDFs will be saved into their respective date subfolders under `./output`:
   ```
   output/
   ├── 08.06.26/
   │   ├── www.dainiksangbadpratidin.com_...pdf
   │   └── banglascoop.com_...pdf
   └── 09.06.26/
       └── www.71sangbad.com_...pdf
   ```

### Optional Arguments

| Argument | Default | Description |
| :--- | :--- | :--- |
| `--csv` | `data.csv` | Path to custom CSV file |
| `--output` | `output` | Directory to save generated PDFs |
| `--concurrency` | `5` | Number of simultaneous browser tabs/workers |
| `--timeout` | `30000` | Page navigation timeout in milliseconds |
| `--limit` | `None` | Process only the first N URLs from the CSV (useful for testing) |

## Desktop & One-Page Web App

Run the single-page UI as a native desktop window (`pywebview`):
```bash
.venv/bin/python desktop_app.py
```

Or open in your browser / run as a web server:
```bash
.venv/bin/python desktop_app.py --web
.venv/bin/python desktop_app.py --server-only --host 0.0.0.0 --port 8000
```

Features included in the UI:
- Paste URLs (one per line) or upload a `.csv` file with optional date subfolder, concurrency, and timeout controls.
- Live per-URL progress and status (`Reader Mode`, `Print Mode`, `Full Webpage`).
- Download generated PDFs one by one, download selected/all PDFs in bulk as a `.zip`, or open the local output folder.
- **Delete All PDFs** button with confirmation dialog to clear generated PDFs.

## Building the Standalone Windows `.exe` / Installer

Since PyInstaller produces native binaries per OS, run the Windows build on a Windows machine or via GitHub Actions:

1. **On a Windows PC**:
   - Run `build_windows.bat` (creates `dist\NewsArchiver\NewsArchiver.exe`, and compiles `dist\installer\NewsArchiver-Setup.exe` if Inno Setup 6 is installed).
   - Pass `build_windows.bat --bundle-chromium` to embed Chromium directly inside the installer for offline PCs; otherwise the app includes a one-click **Install Browser Engine** button on first launch.
2. **Via GitHub Actions (from macOS/Linux)**:
   - Push to `main` (or trigger **Build Windows Installer** manually in the **Actions** tab) using `.github/workflows/build-windows.yml`, then download the `NewsArchiver-Windows` artifact containing `NewsArchiver-Setup.exe` and `NewsArchiver-Portable-Windows.zip`.



--------------------------

Analyzed build_windows.bat:L1-40
Analyzed news_archiver.spec:L1-80
Ran check non-Python files shipped by pywebview
Edited news_archiver.spec
Edited build_windows.bat

Yes — copying via USB and running [build_windows.bat](file:///Users/icesiv/Work/news_archiver/build_windows.bat) on a Windows PC works. Follow these exact steps so it builds cleanly on the first try:

### 1. Copy Only the Project Files to USB (Skip `.venv` and `output`)
Do **not** copy `.venv/` (those are macOS binaries and won't work on Windows) or `output/` (your existing PDFs). Copy these files/folders to your USB drive:
- [archiver.py](file:///Users/icesiv/Work/news_archiver/archiver.py)
- [app_server.py](file:///Users/icesiv/Work/news_archiver/app_server.py)
- [desktop_app.py](file:///Users/icesiv/Work/news_archiver/desktop_app.py)
- [requirements.txt](file:///Users/icesiv/Work/news_archiver/requirements.txt)
- [news_archiver.spec](file:///Users/icesiv/Work/news_archiver/news_archiver.spec)
- [build_windows.bat](file:///Users/icesiv/Work/news_archiver/build_windows.bat)
- `web/`
- `installer/`

---

### 2. Prepare the Windows Build PC (One-Time Setup)
On the Windows PC where you will compile the `.exe`:
1. Install **Python 3.11 or 3.12** from python.org — **make sure to check "Add Python to PATH"** during installation.
2. *(Optional, to get a single `NewsArchiver-Setup.exe` installer)* Install **Inno Setup 6** from jrsoftware.org.
3. Make sure this build PC has internet access (so `pip` can download Windows packages).

---

### 3. Paste & Build on the Windows PC
1. Paste the folder from USB to a local path on the Windows PC (e.g. `C:\news_archiver` — avoid building directly on the slow USB stick).
2. Open **Command Prompt** (`cmd`) in that folder and run **one** of these:
   - **Standard lightweight build** (smaller installer; users click **Install Browser Engine** once inside the app):
     ```bat
     build_windows.bat
     ```
   - **All-in-one offline build** (embeds Chromium ~200 MB inside the package so end users need zero extra downloads):
     ```bat
     build_windows.bat --bundle-chromium
     ```

---

### 4. What You Get to Share with End Users
- **If Inno Setup 6 was installed:** `dist\installer\NewsArchiver-Setup.exe` (give this single installer file to your users).
- **If Inno Setup 6 was not installed:** `dist\NewsArchiver\NewsArchiver.exe` (zip the entire `dist\NewsArchiver\` folder and share it; users extract and double-click `NewsArchiver.exe`).

> [!TIP]
> **No-USB Alternative:** If this repo is on GitHub, you can also push these files and run the **Build Windows Installer** workflow in [.github/workflows/build-windows.yml](file:///Users/icesiv/Work/news_archiver/.github/workflows/build-windows.yml) from the **Actions** tab to download `NewsArchiver-Setup.exe` directly.

*(Note: I also updated [news_archiver.spec](file:///Users/icesiv/Work/news_archiver/news_archiver.spec) to bundle `webview`/`pythonnet`/`clr_loader` DLLs and [build_windows.bat](file:///Users/icesiv/Work/news_archiver/build_windows.bat) to auto-detect Inno Setup in standard Windows install paths.)*