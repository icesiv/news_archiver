#!/usr/bin/env python3
"""
FastAPI backend for News Archiver Web & Windows Desktop App.
Reuses archiver.py's Playwright extraction and Reader Mode PDF generation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from playwright._impl._driver import compute_driver_executable, get_driver_env
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

from archiver import parse_entries_from_csv_text, process_single_url

logger = logging.getLogger("news_archiver.server")


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_bundle_dir() -> Path:
    """Directory containing bundled read-only assets (works for both source and PyInstaller)."""
    if is_frozen() and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def get_app_dir() -> Path:
    """Directory containing the executable (when frozen) or source script."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_default_output_dir() -> Path:
    """
    Returns a writable directory for generated PDFs.
    When packaged as a Windows/desktop app, stores PDFs in Documents/NewsArchiver/output
    instead of a read-only Program Files install directory.
    """
    override = os.environ.get("NEWS_ARCHIVER_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        return (Path.home() / "Documents" / "NewsArchiver" / "output").resolve()
    return (Path(__file__).resolve().parent / "output").resolve()


def configure_playwright_browsers_path() -> None:
    """Point Playwright to a bundled ms-playwright folder if one ships alongside the app."""
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    candidates = [
        get_app_dir() / "ms-playwright",
        get_bundle_dir() / "ms-playwright",
    ]
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.iterdir()):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
            logger.info(f"Using bundled Playwright browsers at: {candidate}")
            return


configure_playwright_browsers_path()

OUTPUT_DIR: Path = get_default_output_dir()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR: Path = get_bundle_dir() / "web"

# In-memory state for active/completed jobs and browser installation
JOBS: dict[str, dict] = {}
BROWSER_INSTALL_STATE: dict = {
    "installing": False,
    "last_error": "",
    "last_log": "",
}
BROWSER_INSTALL_LOCK = asyncio.Lock()


class ArchiveRequest(BaseModel):
    urls: list[str] = Field(default_factory=list)
    date: str = ""
    concurrency: int = Field(default=5, ge=1, le=10)
    timeout_ms: int = Field(default=30000, ge=5000, le=120000)


class BulkDownloadRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)


def normalize_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        return ""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url
    return url


def parse_url_lines(raw_urls: list[str], default_date: str = "") -> list[dict]:
    """
    Parses a list of URL strings (also supports optional 'date,url' per line).
    """
    entries: list[dict] = []
    cleaned_default_date = default_date.strip()
    for line in raw_urls:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        # Support optional "date,url" if user pastes CSV-like lines
        if "," in text and not text.startswith("http"):
            parts = [p.strip() for p in text.split(",", 1)]
            if len(parts) == 2 and parts[1]:
                url = normalize_url(parts[1])
                if url:
                    entries.append({"url": url, "date": parts[0] or cleaned_default_date})
                continue
        url = normalize_url(text)
        if url:
            entries.append({"url": url, "date": cleaned_default_date})
    return entries


async def check_chromium_installed() -> tuple[bool, str]:
    """Checks whether Playwright's Chromium binary is available on disk."""
    try:
        async with async_playwright() as p:
            exe_path = Path(p.chromium.executable_path)
            return exe_path.exists(), str(exe_path)
    except Exception as exc:
        return False, str(exc)


def _run_playwright_install_chromium() -> tuple[int, str]:
    """
    Runs Playwright's bundled Node CLI to install Chromium.
    Works both from Python source and inside a frozen PyInstaller executable.
    """
    driver_exe, driver_cli = compute_driver_executable()
    kwargs: dict = {
        "env": get_driver_env(),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    proc = subprocess.run([driver_exe, driver_cli, "install", "chromium"], **kwargs)
    return proc.returncode, (proc.stdout or "").strip()


async def execute_archive_job(job_id: str, entries: list[dict], concurrency: int, timeout_ms: int) -> None:
    """Runs Playwright archiving for a batch of URLs and updates JOBS[job_id] incrementally."""
    job = JOBS[job_id]
    job["status"] = "running"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ready, detail = await check_chromium_installed()
    if not ready:
        job["status"] = "failed"
        job["error"] = (
            "Chromium browser engine is not installed yet. "
            "Click 'Install Browser Engine' at the top to complete one-time setup."
        )
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        return

    semaphore = asyncio.Semaphore(max(1, concurrency))
    total = len(entries)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                async def run_item(idx: int, item: dict) -> None:
                    res = await process_single_url(
                        browser=browser,
                        url=item["url"],
                        date=item.get("date", ""),
                        index=idx,
                        total=total,
                        output_dir=OUTPUT_DIR,
                        timeout_ms=timeout_ms,
                        semaphore=semaphore,
                    )
                    job["results"].append(res)
                    job["completed"] += 1
                    if res.get("success"):
                        job["succeeded"] += 1
                    else:
                        job["failed"] += 1

                await asyncio.gather(*(run_item(i, item) for i, item in enumerate(entries, 1)))
            finally:
                await browser.close()

        job["status"] = "completed"
    except Exception as exc:
        logger.exception("Archive job %s failed", job_id)
        job["status"] = "failed"
        job["error"] = str(exc)
    finally:
        job["finished_at"] = datetime.now(timezone.utc).isoformat()


def list_generated_pdfs() -> list[dict]:
    """Lists all PDF files inside OUTPUT_DIR, sorted newest first."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for path in OUTPUT_DIR.rglob("*.pdf"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            rel = path.relative_to(OUTPUT_DIR)
            rel_posix = rel.as_posix()
            folder = rel.parent.as_posix()
            if folder == ".":
                folder = ""
            items.append(
                {
                    "rel_path": rel_posix,
                    "name": path.name,
                    "folder": folder,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "mtime": stat.st_mtime,
                }
            )
        except OSError:
            continue

    items.sort(key=lambda x: x["mtime"], reverse=True)
    for item in items:
        item.pop("mtime", None)
    return items


def resolve_safe_pdf_path(rel_path: str) -> Path:
    """Resolves a relative path safely inside OUTPUT_DIR and prevents directory traversal."""
    if not rel_path or rel_path.startswith(("/", "\\")):
        raise HTTPException(status_code=400, detail="Invalid PDF path.")
    base = OUTPUT_DIR.resolve()
    target = (base / rel_path).resolve()
    if base not in target.parents:
        raise HTTPException(status_code=403, detail="Access denied.")
    if target.suffix.lower() != ".pdf" or not target.is_file():
        raise HTTPException(status_code=404, detail="PDF file not found.")
    return target


def remove_file_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def create_job_record(entries: list[dict], concurrency: int, timeout_ms: int) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "total": len(entries),
        "completed": 0,
        "succeeded": 0,
        "failed": 0,
        "error": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "results": [],
    }
    asyncio.create_task(execute_archive_job(job_id, entries, concurrency, timeout_ms))
    return job_id


@asynccontextmanager
async def lifespan(_app: FastAPI):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="News Archiver", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if (WEB_DIR / "static").is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = WEB_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=500, detail="Frontend web/index.html not found.")
    return HTMLResponse(index_path.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "output_dir": str(OUTPUT_DIR),
        "frozen": is_frozen(),
        "platform": sys.platform,
    }


@app.get("/api/browser-status")
async def browser_status():
    installed, exe_path = await check_chromium_installed()
    return {
        "installed": installed,
        "executable_path": exe_path,
        "installing": BROWSER_INSTALL_STATE["installing"],
        "last_error": BROWSER_INSTALL_STATE["last_error"],
    }


@app.post("/api/install-browser")
async def install_browser():
    if BROWSER_INSTALL_LOCK.locked():
        return {"status": "installing", "installed": False}

    async def _do_install():
        async with BROWSER_INSTALL_LOCK:
            BROWSER_INSTALL_STATE["installing"] = True
            BROWSER_INSTALL_STATE["last_error"] = ""
            try:
                code, output = await asyncio.to_thread(_run_playwright_install_chromium)
                BROWSER_INSTALL_STATE["last_log"] = output[-2000:]
                if code != 0:
                    BROWSER_INSTALL_STATE["last_error"] = output or f"Installer exited with code {code}"
            except Exception as exc:
                BROWSER_INSTALL_STATE["last_error"] = str(exc)
            finally:
                BROWSER_INSTALL_STATE["installing"] = False

    asyncio.create_task(_do_install())
    return {"status": "started"}


@app.post("/api/archive")
async def start_archive_from_urls(payload: ArchiveRequest):
    entries = parse_url_lines(payload.urls, default_date=payload.date)
    if not entries:
        raise HTTPException(status_code=400, detail="Please provide at least one valid URL.")
    job_id = create_job_record(entries, payload.concurrency, payload.timeout_ms)
    return {"job_id": job_id, "total": len(entries)}


@app.post("/api/archive/csv")
async def start_archive_from_csv(
    file: UploadFile = File(...),
    concurrency: int = Form(default=5, ge=1, le=10),
    timeout_ms: int = Form(default=30000, ge=5000, le=120000),
):
    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8-sig", errors="ignore")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read CSV file: {exc}")

    entries = parse_entries_from_csv_text(text)
    if not entries:
        raise HTTPException(status_code=400, detail="No valid URLs found in the uploaded CSV file.")
    job_id = create_job_record(entries, concurrency, timeout_ms)
    return {"job_id": job_id, "total": len(entries)}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/api/pdfs")
async def get_pdfs():
    files = list_generated_pdfs()
    total_size = sum(f["size_bytes"] for f in files)
    return {
        "output_dir": str(OUTPUT_DIR),
        "count": len(files),
        "total_size_bytes": total_size,
        "files": files,
    }


@app.get("/api/pdfs/download")
async def download_single_pdf(path: str = Query(..., description="Relative path inside output directory")):
    target = resolve_safe_pdf_path(path)
    return FileResponse(
        path=str(target),
        media_type="application/pdf",
        filename=target.name,
        content_disposition_type="attachment",
    )


def _build_bulk_zip_response(background_tasks: BackgroundTasks, requested_paths: Optional[list[str]] = None) -> FileResponse:
    if requested_paths:
        targets = [resolve_safe_pdf_path(p) for p in requested_paths]
    else:
        targets = [OUTPUT_DIR / item["rel_path"] for item in list_generated_pdfs()]

    if not targets:
        raise HTTPException(status_code=404, detail="No PDFs available to download.")

    fd, tmp_name = tempfile.mkstemp(prefix="news_archiver_", suffix=".zip")
    os.close(fd)
    zip_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            seen_arcnames: set[str] = set()
            for file_path in targets:
                try:
                    arcname = file_path.resolve().relative_to(OUTPUT_DIR.resolve()).as_posix()
                except ValueError:
                    arcname = file_path.name
                if arcname in seen_arcnames:
                    arcname = f"{time.time_ns()}_{arcname}"
                seen_arcnames.add(arcname)
                zf.write(file_path, arcname=arcname)
    except Exception:
        remove_file_quietly(zip_path)
        raise

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    background_tasks.add_task(remove_file_quietly, zip_path)
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"news_archiver_pdfs_{timestamp}.zip",
        content_disposition_type="attachment",
    )


@app.get("/api/pdfs/download-bulk")
async def download_bulk_get(background_tasks: BackgroundTasks):
    return _build_bulk_zip_response(background_tasks)


@app.post("/api/pdfs/download-bulk")
async def download_bulk_post(background_tasks: BackgroundTasks, payload: Optional[BulkDownloadRequest] = None):
    paths = payload.paths if payload else []
    return _build_bulk_zip_response(background_tasks, requested_paths=paths or None)


@app.post("/api/pdfs/download-selected")
async def download_selected_form(background_tasks: BackgroundTasks, paths: list[str] = Form(default=[])):
    return _build_bulk_zip_response(background_tasks, requested_paths=paths or None)


@app.delete("/api/pdfs")
async def delete_all_pdfs():
    """Deletes all generated PDFs and empty subfolders inside OUTPUT_DIR."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    deleted_count = 0

    for pdf_path in list(OUTPUT_DIR.rglob("*.pdf")):
        if pdf_path.is_file():
            try:
                pdf_path.unlink()
                deleted_count += 1
            except OSError as exc:
                logger.warning(f"Failed to delete {pdf_path}: {exc}")

    # Clean up empty subdirectories inside OUTPUT_DIR (bottom-up)
    for dir_path in sorted((p for p in OUTPUT_DIR.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            if not any(dir_path.iterdir()):
                dir_path.rmdir()
        except OSError:
            pass

    return {"deleted_count": deleted_count}


@app.post("/api/open-output-folder")
async def open_output_folder():
    """Opens the PDF output folder in the OS file manager (Windows Explorer / macOS Finder)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(OUTPUT_DIR))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(OUTPUT_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(OUTPUT_DIR)])
        return {"opened": True, "path": str(OUTPUT_DIR)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open folder: {exc}")
