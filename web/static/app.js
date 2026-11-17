"use strict";

(() => {
  const els = {
    browserPill: document.getElementById("browser-pill"),
    setupBanner: document.getElementById("setup-banner"),
    setupMessage: document.getElementById("setup-message"),
    installBrowserBtn: document.getElementById("install-browser-btn"),

    archiveForm: document.getElementById("archive-form"),
    urlsInput: document.getElementById("urls-input"),
    csvInput: document.getElementById("csv-input"),
    dateInput: document.getElementById("date-input"),
    concurrencyInput: document.getElementById("concurrency-input"),
    timeoutInput: document.getElementById("timeout-input"),
    formError: document.getElementById("form-error"),
    generateBtn: document.getElementById("generate-btn"),
    clearInputBtn: document.getElementById("clear-input-btn"),

    progressCard: document.getElementById("progress-card"),
    jobSummaryBadge: document.getElementById("job-summary-badge"),
    jobProgressLabel: document.getElementById("job-progress-label"),
    jobProgress: document.getElementById("job-progress"),
    jobErrorBanner: document.getElementById("job-error-banner"),
    jobResultsList: document.getElementById("job-results-list"),

    storageMeta: document.getElementById("storage-meta"),
    refreshPdfsBtn: document.getElementById("refresh-pdfs-btn"),
    openFolderBtn: document.getElementById("open-folder-btn"),
    downloadSelectedBtn: document.getElementById("download-selected-btn"),
    downloadAllLink: document.getElementById("download-all-link"),
    deleteAllBtn: document.getElementById("delete-all-btn"),
    libraryStatus: document.getElementById("library-status"),
    emptyState: document.getElementById("empty-state"),
    pdfsTableWrapper: document.getElementById("pdfs-table-wrapper"),
    pdfsTbody: document.getElementById("pdfs-tbody"),
    selectAllCheckbox: document.getElementById("select-all-checkbox"),
    footerOutputPath: document.getElementById("footer-output-path"),

    deleteDialog: document.getElementById("delete-confirm-dialog"),
    deleteCountLabel: document.getElementById("delete-count-label"),
  };

  let activeJobTimer = null;
  let browserPollTimer = null;
  let currentPdfCount = 0;
  const selectedPaths = new Set();

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let idx = 0;
    let val = bytes;
    while (val >= 1024 && idx < units.length - 1) {
      val /= 1024;
      idx += 1;
    }
    return `${val.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
  }

  function formatTimestamp(isoString) {
    if (!isoString) return "";
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) return isoString;
    return date.toLocaleString();
  }

  function showFormError(message) {
    els.formError.textContent = message;
    els.formError.hidden = false;
    els.urlsInput.setAttribute("aria-invalid", "true");
    els.urlsInput.focus();
  }

  function clearFormError() {
    els.formError.textContent = "";
    els.formError.hidden = true;
    els.urlsInput.removeAttribute("aria-invalid");
  }

  function showLibraryStatus(message) {
    els.libraryStatus.textContent = message;
    els.libraryStatus.hidden = false;
  }

  function updateSelectionButtons() {
    const count = selectedPaths.size;
    if (count > 0) {
      els.downloadSelectedBtn.hidden = false;
      els.downloadSelectedBtn.textContent = `Download Selected (${count}) (.zip)`;
    } else {
      els.downloadSelectedBtn.hidden = true;
    }
  }

  async function refreshBrowserStatus() {
    try {
      const res = await fetch("/api/browser-status");
      if (!res.ok) return;
      const data = await res.json();

      if (data.installing) {
        els.browserPill.textContent = "Installing Chromium…";
        els.browserPill.className = "status-pill status-missing";
        els.setupBanner.hidden = false;
        els.setupMessage.textContent = "Downloading Playwright Chromium engine… Please wait, this happens only once.";
        els.installBrowserBtn.disabled = true;
        els.installBrowserBtn.textContent = "Installing…";
        if (!browserPollTimer) {
          browserPollTimer = setInterval(refreshBrowserStatus, 2000);
        }
        return;
      }

      if (browserPollTimer) {
        clearInterval(browserPollTimer);
        browserPollTimer = null;
      }

      if (data.installed) {
        els.browserPill.textContent = "Engine Ready";
        els.browserPill.className = "status-pill status-ready";
        els.setupBanner.hidden = true;
      } else {
        els.browserPill.textContent = "Browser Setup Needed";
        els.browserPill.className = "status-pill status-missing";
        els.setupBanner.hidden = false;
        els.installBrowserBtn.disabled = false;
        els.installBrowserBtn.textContent = "Install Browser Engine";
        if (data.last_error) {
          els.setupMessage.textContent = `Setup error: ${data.last_error}`;
        }
      }
    } catch {
      els.browserPill.textContent = "Offline";
      els.browserPill.className = "status-pill status-missing";
    }
  }

  async function triggerBrowserInstall() {
    els.installBrowserBtn.disabled = true;
    els.installBrowserBtn.textContent = "Starting…";
    try {
      await fetch("/api/install-browser", { method: "POST" });
      await refreshBrowserStatus();
      if (!browserPollTimer) {
        browserPollTimer = setInterval(refreshBrowserStatus, 2000);
      }
    } catch (err) {
      els.setupMessage.textContent = `Could not start browser installer: ${err.message}`;
      els.installBrowserBtn.disabled = false;
      els.installBrowserBtn.textContent = "Install Browser Engine";
    }
  }

  async function refreshPdfList() {
    try {
      const res = await fetch("/api/pdfs");
      if (!res.ok) throw new Error("Failed to load PDFs.");
      const data = await res.json();

      currentPdfCount = data.count || 0;
      els.footerOutputPath.textContent = data.output_dir || "output";
      els.storageMeta.textContent = `${currentPdfCount} PDF${currentPdfCount === 1 ? "" : "s"} (${formatBytes(data.total_size_bytes || 0)})`;

      // Prune stale selections
      const availableSet = new Set((data.files || []).map((f) => f.rel_path));
      for (const path of Array.from(selectedPaths)) {
        if (!availableSet.has(path)) selectedPaths.delete(path);
      }

      const hasFiles = currentPdfCount > 0;
      els.emptyState.hidden = hasFiles;
      els.pdfsTableWrapper.hidden = !hasFiles;
      els.deleteAllBtn.disabled = !hasFiles;
      if (hasFiles) {
        els.downloadAllLink.removeAttribute("aria-disabled");
      } else {
        els.downloadAllLink.setAttribute("aria-disabled", "true");
      }

      renderPdfRows(data.files || []);
      syncSelectAllCheckbox(data.files || []);
      updateSelectionButtons();
    } catch (err) {
      showLibraryStatus(`Error loading PDFs: ${err.message}`);
    }
  }

  function syncSelectAllCheckbox(files) {
    if (!files.length) {
      els.selectAllCheckbox.checked = false;
      els.selectAllCheckbox.indeterminate = false;
      return;
    }
    const selectedCount = files.filter((f) => selectedPaths.has(f.rel_path)).length;
    els.selectAllCheckbox.checked = selectedCount === files.length;
    els.selectAllCheckbox.indeterminate = selectedCount > 0 && selectedCount < files.length;
  }

  function renderPdfRows(files) {
    els.pdfsTbody.replaceChildren();

    for (const file of files) {
      const tr = document.createElement("tr");

      // Checkbox cell
      const tdCheck = document.createElement("td");
      tdCheck.className = "col-check";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selectedPaths.has(file.rel_path);
      checkbox.setAttribute("aria-label", `Select ${file.name}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          selectedPaths.add(file.rel_path);
        } else {
          selectedPaths.delete(file.rel_path);
        }
        syncSelectAllCheckbox(files);
        updateSelectionButtons();
      });
      tdCheck.appendChild(checkbox);

      // Row header (filename)
      const thName = document.createElement("th");
      thName.scope = "row";
      thName.textContent = file.name;

      // Folder cell
      const tdFolder = document.createElement("td");
      if (file.folder) {
        const span = document.createElement("span");
        span.className = "folder-tag";
        span.textContent = file.folder;
        tdFolder.appendChild(span);
      } else {
        tdFolder.textContent = "Root";
      }

      // Size cell
      const tdSize = document.createElement("td");
      tdSize.textContent = formatBytes(file.size_bytes);

      // Created cell
      const tdCreated = document.createElement("td");
      tdCreated.textContent = formatTimestamp(file.modified_at);

      // Action cell (single download)
      const tdAction = document.createElement("td");
      tdAction.className = "col-action";
      const dlLink = document.createElement("a");
      dlLink.className = "btn btn-secondary btn-sm";
      dlLink.href = `/api/pdfs/download?path=${encodeURIComponent(file.rel_path)}`;
      dlLink.setAttribute("download", file.name);
      dlLink.textContent = "Download";
      tdAction.appendChild(dlLink);

      tr.append(tdCheck, thName, tdFolder, tdSize, tdCreated, tdAction);
      els.pdfsTbody.appendChild(tr);
    }
  }

  function renderJobState(job) {
    els.progressCard.hidden = false;
    const total = job.total || 0;
    const completed = job.completed || 0;
    const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

    els.jobProgress.value = pct;
    els.jobProgressLabel.textContent = `${completed} of ${total} processed (${job.succeeded || 0} succeeded, ${job.failed || 0} failed)`;

    if (job.status === "completed") {
      els.jobSummaryBadge.textContent = "Completed";
      els.jobSummaryBadge.className = "badge badge-success";
    } else if (job.status === "failed") {
      els.jobSummaryBadge.textContent = "Failed";
      els.jobSummaryBadge.className = "badge badge-error";
    } else {
      els.jobSummaryBadge.textContent = job.status === "running" ? "Processing…" : "Queued";
      els.jobSummaryBadge.className = "badge";
    }

    if (job.error) {
      els.jobErrorBanner.textContent = job.error;
      els.jobErrorBanner.hidden = false;
    } else {
      els.jobErrorBanner.hidden = true;
    }

    els.jobResultsList.replaceChildren();
    for (const item of job.results || []) {
      const li = document.createElement("li");
      li.className = "job-result-item";

      const urlSpan = document.createElement("span");
      urlSpan.className = "job-result-url";
      urlSpan.textContent = item.url || "";

      const rightWrap = document.createElement("span");
      rightWrap.style.display = "inline-flex";
      rightWrap.style.alignItems = "center";
      rightWrap.style.gap = "0.5rem";

      const statusBadge = document.createElement("span");
      if (item.success) {
        statusBadge.className = "badge badge-success";
        statusBadge.textContent = `${item.mode || "PDF"}${item.elapsed ? ` • ${item.elapsed}s` : ""}`;
        rightWrap.appendChild(statusBadge);

        if (item.rel_path) {
          const dlLink = document.createElement("a");
          dlLink.className = "btn btn-secondary btn-sm";
          dlLink.href = `/api/pdfs/download?path=${encodeURIComponent(item.rel_path)}`;
          dlLink.setAttribute("download", item.filename || "article.pdf");
          dlLink.textContent = "Download";
          rightWrap.appendChild(dlLink);
        }
      } else {
        statusBadge.className = "badge badge-error";
        statusBadge.textContent = "Failed";
        statusBadge.title = item.detail || "Failed";
        rightWrap.appendChild(statusBadge);
      }

      li.append(urlSpan, rightWrap);
      els.jobResultsList.appendChild(li);
    }
  }

  async function pollJob(jobId) {
    if (activeJobTimer) {
      clearInterval(activeJobTimer);
      activeJobTimer = null;
    }

    const fetchOnce = async () => {
      const res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) return true;
      const job = await res.json();
      renderJobState(job);
      await refreshPdfList();

      if (job.status === "completed" || job.status === "failed") {
        clearInterval(activeJobTimer);
        activeJobTimer = null;
        els.generateBtn.disabled = false;
        els.generateBtn.textContent = "Generate PDFs";
        return true;
      }
      return false;
    };

    const done = await fetchOnce();
    if (!done) {
      activeJobTimer = setInterval(fetchOnce, 1200);
    }
  }

  // Event listeners
  els.urlsInput.addEventListener("input", clearFormError);
  els.csvInput.addEventListener("change", clearFormError);

  els.clearInputBtn.addEventListener("click", () => {
    els.urlsInput.value = "";
    els.csvInput.value = "";
    els.dateInput.value = "";
    clearFormError();
  });

  els.installBrowserBtn.addEventListener("click", triggerBrowserInstall);

  els.archiveForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearFormError();

    const csvFile = els.csvInput.files && els.csvInput.files[0];
    const urlLines = els.urlsInput.value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);

    if (!csvFile && urlLines.length === 0) {
      showFormError("Enter at least one URL or select a CSV file first.");
      return;
    }

    const concurrency = Math.min(10, Math.max(1, Number(els.concurrencyInput.value) || 5));
    const timeoutSec = Math.min(120, Math.max(5, Number(els.timeoutInput.value) || 30));
    const timeoutMs = timeoutSec * 1000;

    els.generateBtn.disabled = true;
    els.generateBtn.textContent = "Starting…";

    try {
      let res;
      if (csvFile) {
        const fd = new FormData();
        fd.append("file", csvFile);
        fd.append("concurrency", String(concurrency));
        fd.append("timeout_ms", String(timeoutMs));
        res = await fetch("/api/archive/csv", { method: "POST", body: fd });
      } else {
        res = await fetch("/api/archive", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            urls: urlLines,
            date: els.dateInput.value.trim(),
            concurrency,
            timeout_ms: timeoutMs,
          }),
        });
      }

      const payload = await res.json();
      if (!res.ok) {
        throw new Error(payload.detail || "Could not start archiving job.");
      }

      els.generateBtn.textContent = "Generating…";
      await pollJob(payload.job_id);
    } catch (err) {
      showFormError(err.message || "Unexpected error starting job.");
      els.generateBtn.disabled = false;
      els.generateBtn.textContent = "Generate PDFs";
    }
  });

  els.refreshPdfsBtn.addEventListener("click", refreshPdfList);

  els.openFolderBtn.addEventListener("click", async () => {
    try {
      const res = await fetch("/api/open-output-folder", { method: "POST" });
      if (!res.ok) {
        const err = await res.json();
        showLibraryStatus(err.detail || "Could not open folder.");
      }
    } catch (err) {
      showLibraryStatus(`Could not open folder: ${err.message}`);
    }
  });

  els.selectAllCheckbox.addEventListener("change", () => {
    const checkboxes = els.pdfsTbody.querySelectorAll('input[type="checkbox"]');
    if (els.selectAllCheckbox.checked) {
      for (const cb of checkboxes) {
        cb.checked = true;
      }
      // Rebuild selectedPaths from row links
      els.pdfsTbody.querySelectorAll("a[href]").forEach((link) => {
        const url = new URL(link.href, window.location.origin);
        const path = url.searchParams.get("path");
        if (path) selectedPaths.add(path);
      });
    } else {
      selectedPaths.clear();
      for (const cb of checkboxes) {
        cb.checked = false;
      }
    }
    updateSelectionButtons();
  });

  els.downloadSelectedBtn.addEventListener("click", () => {
    const paths = Array.from(selectedPaths);
    if (!paths.length) return;
    const form = document.createElement("form");
    form.method = "POST";
    form.action = "/api/pdfs/download-selected";
    for (const path of paths) {
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "paths";
      input.value = path;
      form.appendChild(input);
    }
    document.body.appendChild(form);
    form.submit();
    form.remove();
  });

  // Delete All PDFs via accessible native <dialog>
  els.deleteAllBtn.addEventListener("click", () => {
    els.deleteCountLabel.textContent = String(currentPdfCount);
    els.deleteDialog.showModal();
  });

  els.deleteDialog.addEventListener("close", async () => {
    const choice = els.deleteDialog.returnValue;
    els.deleteDialog.returnValue = "";
    if (choice !== "confirm") return;

    els.deleteAllBtn.disabled = true;
    try {
      const res = await fetch("/api/pdfs", { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Delete failed.");
      selectedPaths.clear();
      showLibraryStatus(`Deleted ${data.deleted_count} PDF file(s) from the server/output folder.`);
      await refreshPdfList();
    } catch (err) {
      showLibraryStatus(`Error deleting PDFs: ${err.message}`);
      els.deleteAllBtn.disabled = false;
    }
  });

  // Initial load
  refreshBrowserStatus();
  refreshPdfList();
})();
