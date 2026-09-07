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
