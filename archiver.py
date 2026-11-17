#!/usr/bin/env python3
"""
Webpage to PDF Archiver (Reader Mode & Multi-threaded Concurrency)
Extracts ONLY the site logo, headline/title, main hero image, and clean article body,
formats them into a pristine reader-mode layout, and generates a PDF.
Supports concurrent processing (e.g. 5 pages at a time).
"""

import argparse
import asyncio
import csv
import html
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("news_archiver")


def sanitize_filename(text: str, max_length: int = 100) -> str:
    """
    Sanitize text to create safe and clean filenames across all operating systems.
    """
    if not text:
        return "untitled"
    
    # Remove unicode replacement char and normalize
    text = text.replace("\ufffd", "").strip()
    # Replace illegal filesystem characters: / \ : * ? " < > | \n \r \t
    text = re.sub(r'[\\/*?:"<>|\n\r\t]', "_", text)
    # Replace multiple consecutive spaces or underscores with a single underscore
    text = re.sub(r"[\s_]+", "_", text)
    # Strip leading/trailing underscores, dots, or hyphens
    text = text.strip("._-")
    
    if len(text) > max_length:
        text = text[:max_length].rstrip("._- ")
        
    return text or "untitled"


def extract_base_url(url: str) -> str:
    """
    Extract the domain (base URL) from a given URL.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc or parsed.path.split("/")[0]
    netloc = netloc.split(":")[0]
    return sanitize_filename(netloc, max_length=50)


def parse_entries_from_rows(rows: list[list[str]]) -> list[dict]:
    """
    Parses CSV rows (list of lists) into [{'url': '...', 'date': '...'}, ...].
    Supports CSVs with or without headers.
    """
    rows = [row for row in rows if row and any(cell.strip() for cell in row)]
    if not rows:
        return []

    # Check if first row is a header
    first_row = [cell.strip().lower() for cell in rows[0]]
    url_col_idx = None
    date_col_idx = None
    known_url_headers = ["url", "urls", "link", "links", "news_url", "page_url", "article_url", "website", "source_url"]
    known_date_headers = ["date", "dates", "published_date", "publish_date", "news_date", "post_date", "entry_date", "pub_date", "pubdate"]

    for idx, col in enumerate(first_row):
        if col in known_url_headers:
            url_col_idx = idx
        elif col in known_date_headers:
            date_col_idx = idx

    found_header = (url_col_idx is not None or date_col_idx is not None)

    if url_col_idx is None:
        if len(first_row) > 1:
            if "http" in first_row[1] or "." in first_row[1]:
                url_col_idx = 1
                date_col_idx = 0
            else:
                url_col_idx = 0
                date_col_idx = 1
        else:
            url_col_idx = 0

    start_row = 1 if found_header else 0
    entries = []

    for row in rows[start_row:]:
        if len(row) > url_col_idx:
            raw_url = row[url_col_idx].strip()
            if not raw_url:
                continue
            if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
                raw_url = "https://" + raw_url

            raw_date = ""
            if date_col_idx is not None and len(row) > date_col_idx:
                raw_date = row[date_col_idx].strip()

            entries.append({"url": raw_url, "date": raw_date})

    return entries


def parse_entries_from_csv_text(csv_text: str) -> list[dict]:
    """
    Parses CSV content from a string into [{'url': '...', 'date': '...'}, ...].
    """
    if not csv_text:
        return []
    reader = csv.reader(csv_text.splitlines())
    return parse_entries_from_rows(list(reader))


def read_entries_from_csv(csv_path: Path) -> list[dict]:
    """
    Reads entries (URL and optional date) from a CSV file.
    Supports CSVs with headers or raw list of URLs.
    Returns a list of dicts: [{'url': '...', 'date': '...'}, ...]
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    with open(csv_path, mode="r", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.reader(f)
        rows = list(reader)

    entries = parse_entries_from_rows(rows)
    if not entries:
        logger.warning(f"CSV file {csv_path} is empty.")
    return entries


def read_urls_from_csv(csv_path: Path) -> list[str]:
    """
    Reads URLs from a CSV file (backward-compatible).
    """
    entries = read_entries_from_csv(csv_path)
    return [e["url"] for e in entries]


# JavaScript snippet to extract only the 4 target components
EXTRACTOR_JS = """
() => {
    function getAbsoluteUrl(url) {
        if (!url) return '';
        try {
            return new URL(url, document.baseURI).href;
        } catch (e) {
            return url;
        }
    }

    // 1. Extract Title
    let title = '';
    const ogTitle = document.querySelector('meta[property="og:title"], meta[name="twitter:title"]');
    if (ogTitle && ogTitle.content) {
        title = ogTitle.content.trim();
    }
    if (!title) {
        const h1 = document.querySelector('article h1, main h1, h1, [role="heading"][aria-level="1"]');
        if (h1 && h1.innerText) {
            title = h1.innerText.trim();
        }
    }
    if (!title) {
        title = document.title ? document.title.trim() : '';
    }

    // 2. Extract Site Name / Logo
    let siteName = '';
    const ogSite = document.querySelector('meta[property="og:site_name"]');
    if (ogSite && ogSite.content) {
        siteName = ogSite.content.trim();
    }
    if (!siteName) {
        siteName = window.location.hostname.replace(/^www\\./, '');
    }

    let logoUrl = '';
    // Look for header logo images
    const logoSelectors = [
        'header img[class*="logo" i]',
        'header img[id*="logo" i]',
        'header img[alt*="logo" i]',
        'img[class*="logo" i]',
        'img[id*="logo" i]',
        'img[alt*="logo" i]',
        'header a[class*="brand" i] img',
        'a[class*="navbar-brand" i] img',
        '.header-logo img',
        'header img'
    ];

    for (const sel of logoSelectors) {
        const el = document.querySelector(sel);
        if (el && el.src && !el.src.includes('data:image/svg+xml') && el.naturalWidth !== 1) {
            logoUrl = getAbsoluteUrl(el.src);
            break;
        }
    }

    // Fallback to high-res touch icon or favicon if logo image not found
    if (!logoUrl) {
        const iconEl = document.querySelector('link[rel="apple-touch-icon"], link[rel="icon"][sizes="192x192"], link[rel="icon"][sizes="32x32"], link[rel="icon"]');
        if (iconEl && iconEl.href) {
            logoUrl = getAbsoluteUrl(iconEl.href);
        }
    }

    // 3. Extract Main / Hero Image
    let mainImageUrl = '';
    const ogImg = document.querySelector('meta[property="og:image"], meta[name="twitter:image"], meta[property="og:image:secure_url"]');
    if (ogImg && ogImg.content) {
        mainImageUrl = getAbsoluteUrl(ogImg.content);
    }

    if (!mainImageUrl) {
        // Look inside article hero figure
        const heroImg = document.querySelector('article figure img, .featured-image img, .post-thumbnail img, article img');
        if (heroImg && heroImg.src) {
            mainImageUrl = getAbsoluteUrl(heroImg.src);
        }
    }

    // 4. Extract Published Date / Author
    let publishedDate = '';
    const dateMeta = document.querySelector('meta[property="article:published_time"], meta[name="pubdate"], meta[name="publish-date"], meta[name="date"], meta[property="og:updated_time"]');
    if (dateMeta && dateMeta.content) {
        publishedDate = dateMeta.content.trim();
    }
    
    if (!publishedDate) {
        const dateSelectors = [
            'article time', 'time',
            '.publish-date', '.post-date', '.news-date', '.pub', '.report-date',
            '.date', '.published-date', '.entry-date', '[class*="publish" i]', '[class*="post-time" i]'
        ];
        for (const sel of dateSelectors) {
            const el = document.querySelector(sel);
            if (el) {
                const dt = el.getAttribute('datetime') || el.innerText || '';
                if (dt.trim() && dt.trim().length > 5 && dt.trim().length < 80 && !/^(প্রিন্ট|print)$/i.test(dt.trim())) {
                    publishedDate = dt.trim();
                    break;
                }
            }
        }
    }

    if (!publishedDate) {
        // Look for elements with publish keywords or ISO timestamps in text
        const dateNodes = Array.from(document.querySelectorAll('h5, h6, span, div, p, em, small')).filter(el => {
            const txt = (el.innerText || '').trim();
            return txt.length > 5 && txt.length < 80 && (
                /^\d{4}-\d{2}-\d{2}/.test(txt) ||
                /^(Publish|প্রকাশ|আপলোড সময়|আপডেট সময়)\s*:/i.test(txt)
            );
        });
        if (dateNodes.length > 0) {
            publishedDate = dateNodes[0].innerText.trim();
        }
    }

    if (publishedDate) {
        // Clean up common prefixes and whitespace
        publishedDate = publishedDate.replace(/^(Publish|প্রকাশ|আপলোড সময়|আপডেট সময়)\s*:\s*/i, '').trim();
    }

    // 5. Look for Print View URL or button ("প্রিন্ট", "print", "মুদ্রণ", etc.)
    let printUrl = '';
    const printCandidates = Array.from(document.querySelectorAll('a, button, [role="button"], [onclick]')).filter(el => {
        const text = (el.innerText || '').trim();
        const title = el.getAttribute('title') || '';
        const aria = el.getAttribute('aria-label') || '';
        const href = el.getAttribute('href') || '';
        const onclick = el.getAttribute('onclick') || '';
        const cls = typeof el.className === 'string' ? el.className : '';

        const matchText = /^(প্রিন্ট|প্রিন্ট করুন|মুদ্রণ|print|print article)$/i.test(text) || /\b(print|প্রিন্ট|মুদ্রণ)\b/i.test(title + ' ' + aria);
        const matchHref = /(print\.php|\/print\/|\/print$|\?print=|\?view=print|template=print)/i.test(href) && !href.startsWith('javascript:');
        const matchOnclick = /window\.open\([^)]*print/i.test(onclick);
        const matchCls = /print-btn|btn-print|print_btn|fa-print|icon-print/i.test(cls);

        return matchText || matchHref || matchOnclick || matchCls;
    });

    for (const el of printCandidates) {
        let href = el.getAttribute('href') || '';
        const onclick = el.getAttribute('onclick') || '';

        // If onclick has window.open('...') extract the URL
        if (onclick) {
            const match = onclick.match(/window\.open\(['"]([^'"]+)['"]/i);
            if (match && match[1]) {
                href = match[1];
            }
        }

        if (href && !href.startsWith('javascript:') && href !== '#' && !href.endsWith('#print')) {
            printUrl = getAbsoluteUrl(href);
            break;
        }
    }

    // 6. Extract Clean Article Content
    // Priority container selectors (including designated print containers, Elementor, WordPress, and standard CMS containers)
    const contentSelectors = [
        '#toPrint',
        '#printArea',
        '#printableArea',
        '#print_content',
        '.print-area',
        '.print-content',
        '.printable',
        '.elementor-widget-theme-post-content',
        '.elementor-post__content',
        '.wp-block-post-content',
        '.single-details',
        '.entry-content',
        '.article-body',
        '.post-content',
        '.story-content',
        '.news-content',
        '.details-content',
        '.content-details',
        '.article-details',
        '.news-details',
        '.single-content',
        '.post-body',
        '.story-body',
        '#article-body',
        '#story-content',
        '#content-body',
        'article',
        '[role="article"]',
        'main'
    ];

    const candidates = [];
    for (const sel of contentSelectors) {
        const elements = document.querySelectorAll(sel);
        for (const el of elements) {
            // Ignore containers inside headers, footers, navigation, tickers, marquees, or sidebars
            if (el.closest('header, footer, nav, aside, [class*="header" i], [class*="ticker" i], [class*="marquee" i], [class*="menu" i]')) {
                continue;
            }
            const text = el.innerText ? el.innerText.trim() : '';
            if (text.length > 80) {
                candidates.push({
                    element: el,
                    textLen: text.length,
                    pCount: el.querySelectorAll('p').length
                });
            }
        }
    }

    // Pick candidate with largest text content length
    candidates.sort((a, b) => b.textLen - a.textLen);
    let contentEl = candidates.length > 0 ? candidates[0].element : null;

    // Fallback container: largest text content block excluding headers/footers/nav/sidebars
    if (!contentEl) {
        let maxTextLen = 0;
        document.querySelectorAll('div, section, article, td').forEach(el => {
            if (el.closest('header, footer, nav, aside, [class*="header" i], [class*="ticker" i], [class*="marquee" i], [class*="menu" i]')) return;
            const text = el.innerText ? el.innerText.trim() : '';
            if (text.length > maxTextLen) {
                if (el !== document.body && el.tagName.toLowerCase() !== 'html') {
                    maxTextLen = text.length;
                    contentEl = el;
                }
            }
        });
    }

    let articleHtml = '';
    let extractedText = '';

    if (contentEl) {
        const clone = contentEl.cloneNode(true);

        // Targeted junk removal (precise selectors to avoid removing content containers)
        const junkSelectors = [
            'script', 'style', 'noscript', 'iframe', 'svg', 'button', 'form',
            'nav', 'header', 'footer', 'aside',
            '.ad', '.ads', '.advertisement', '.ad-banner', '[class*="ad-" i]', '[id*="ad-" i]',
            '.social-share', '.post-share', '.share-box', '.share-buttons', '.share-float',
            '.share-float-c', '.spc-social-colors', '.spc-social-bg',
            '.related-posts', '.related-news', '.popular-posts', '.recommended',
            '.comment-list', '.comments-area', '.newsletter', '.subscription',
            '.tag-cloud', '.tags', '.breadcrumb', '.author-bio', '.sidebar'
        ];

        junkSelectors.forEach(sel => {
            try {
                clone.querySelectorAll(sel).forEach(el => el.remove());
            } catch (e) {}
        });

        const totalText = clone.innerText ? clone.innerText.trim() : '';

        // Check if standard <p> tags cover the article content
        const pElements = Array.from(clone.querySelectorAll('p'));
        const pTextLen = pElements.reduce((acc, p) => acc + (p.innerText ? p.innerText.trim().length : 0), 0);

        if (pTextLen >= 200 || (pElements.length >= 2 && pTextLen >= totalText.length * 0.4)) {
            const allowed = clone.querySelectorAll('p, h2, h3, h4, blockquote, figure');
            const cleanNodes = [];
            const textParts = [];
            allowed.forEach(node => {
                const text = node.innerText ? node.innerText.trim() : '';
                if (node.tagName.toLowerCase() === 'figure') {
                    const img = node.querySelector('img');
                    if (img && img.src && !img.src.includes('data:image/svg+xml')) {
                        img.src = getAbsoluteUrl(img.src);
                        cleanNodes.push(node.outerHTML);
                    }
                } else if (text.length > 10) {
                    cleanNodes.push(node.outerHTML);
                    textParts.push(text);
                }
            });
            if (cleanNodes.length > 0 && textParts.join(' ').length >= 80) {
                articleHtml = cleanNodes.join('\\n');
                extractedText = textParts.join(' ');
            }
        }

        // If not enough text in <p> tags (e.g. notunshomoy where text is in <div><span> or banglascoop with <br>)
        if (!articleHtml && totalText.length >= 40) {
            // Replace <br> with newlines
            clone.querySelectorAll('br').forEach(br => br.replaceWith(document.createTextNode('\\n')));

            // Find leaf block / text-holding elements
            const blocks = Array.from(clone.querySelectorAll('div, p, span, h2, h3, h4, blockquote')).filter(el => {
                const text = el.innerText ? el.innerText.trim() : '';
                if (text.length < 15) return false;
                // Exclude elements that have nested div/p children with substantial text
                const childBlocks = Array.from(el.children).filter(c => ['DIV', 'P'].includes(c.tagName) && (c.innerText || '').trim().length > 15);
                return childBlocks.length === 0;
            });

            const paragraphs = [];
            const textPieces = [];
            const seen = new Set();

            blocks.forEach(b => {
                const text = b.innerText ? b.innerText.trim() : '';
                if (text.length > 15 && !seen.has(text)) {
                    seen.add(text);
                    const lines = text.split(/\\n+/).map(l => l.trim()).filter(l => l.length > 15);
                    lines.forEach(line => {
                        if (!seen.has(line) || line === text) {
                            seen.add(line);
                            if (line !== title && !line.startsWith('##')) {
                                paragraphs.push(`<p>${line}</p>`);
                                textPieces.push(line);
                            }
                        }
                    });
                }
            });

            if (paragraphs.length > 0 && textPieces.join(' ').length >= 40) {
                articleHtml = paragraphs.join('\\n');
                extractedText = textPieces.join(' ');
            } else {
                const rawLines = totalText.split(/\\n+/).map(l => l.trim()).filter(l => l.length > 15 && l !== title && !l.startsWith('##'));
                articleHtml = rawLines.map(l => `<p>${l}</p>`).join('\\n');
                extractedText = rawLines.join(' ');
            }
        }
    }

    const hasBodyText = Boolean(extractedText && extractedText.trim().length >= 40);

    return {
        title: title,
        siteName: siteName,
        logoUrl: logoUrl,
        mainImageUrl: mainImageUrl,
        publishedDate: publishedDate,
        printUrl: printUrl,
        articleHtml: articleHtml,
        hasBodyText: hasBodyText,
        textLength: extractedText ? extractedText.trim().length : 0
    };
}
"""


def build_reader_html(data: dict, source_url: str, fallback_date: str = "") -> str:
    """
    Builds a clean, publication-grade HTML template containing:
    Logo, Site Name, Source URL, Title, Hero Image, Article Body, and Archival Citation.
    """
    title = html.escape(data.get("title") or "Untitled Article")
    site_name = html.escape(data.get("siteName") or extract_base_url(source_url))
    logo_url = data.get("logoUrl") or ""
    main_image = data.get("mainImageUrl") or ""
    published_date = html.escape(data.get("publishedDate") or fallback_date or "")
    article_html = data.get("articleHtml") or ""
    escaped_source_url = html.escape(source_url)

    logo_markup = f'<img src="{html.escape(logo_url)}" alt="{site_name} Logo" class="site-logo" />' if logo_url else ""
    hero_image_markup = f'<div class="hero-image-container"><img src="{html.escape(main_image)}" alt="Main Image" class="hero-image" /></div>' if main_image else ""
    date_markup = f'<span class="meta-item published-date">📅 <strong>Date:</strong> {published_date}</span>' if published_date else ""
    header_date_markup = f'<span class="site-date">📅 {published_date}</span>' if published_date else ""

    return f"""<!DOCTYPE html>
<html lang="bn, en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        @page {{
            size: A4;
            margin: 14mm 14mm 14mm 14mm;
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans Bengali", "Kalpurush", "SolaimanLipi", "Nirmala UI", "Vrinda", Helvetica, Arial, sans-serif;
            color: #1a202c;
            background-color: #ffffff;
            line-height: 1.75;
            font-size: 12.5pt;
            -webkit-font-smoothing: antialiased;
        }}

        .reader-container {{
            max-width: 100%;
            margin: 0 auto;
        }}

        /* Header Bar: Site Logo + Domain + Published Date + Source URL */
        .site-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            padding-bottom: 12px;
            margin-bottom: 18px;
            border-bottom: 2px solid #e2e8f0;
        }}

        .site-brand {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex-shrink: 0;
            max-width: 45%;
        }}

        .brand-details {{
            display: flex;
            flex-direction: column;
            gap: 3px;
            min-width: 0;
        }}

        .site-logo {{
            max-height: 44px;
            max-width: 160px;
            object-fit: contain;
            border-radius: 4px;
            display: block;
        }}

        .site-name {{
            font-size: 14pt;
            font-weight: 700;
            color: #1a202c;
            letter-spacing: -0.2px;
            line-height: 1.2;
        }}

        .site-date {{
            font-size: 8.5pt;
            color: #718096;
            font-weight: 500;
            line-height: 1.3;
        }}

        .source-pill {{
            font-size: 8.5pt;
            color: #4a5568;
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            padding: 5px 10px;
            border-radius: 6px;
            max-width: 55%;
            word-break: break-all;
            line-height: 1.4;
            text-align: right;
        }}

        .source-pill a {{
            color: #2b6cb0;
            text-decoration: none;
        }}

        /* Article Title */
        .article-title {{
            font-size: 21pt;
            font-weight: 800;
            line-height: 1.32;
            color: #1a202c;
            margin-bottom: 14px;
            letter-spacing: -0.3px;
        }}

        /* Meta Information */
        .article-meta {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 12px 20px;
            font-size: 9.5pt;
            color: #4a5568;
            margin-bottom: 20px;
            padding: 10px 14px;
            background-color: #f8fafc;
            border: 1px solid #edf2f7;
            border-radius: 6px;
            word-break: break-all;
        }}

        .meta-item a {{
            color: #2b6cb0;
            text-decoration: none;
            word-break: break-all;
        }}

        /* Hero / Main Image */
        .hero-image-container {{
            width: 100%;
            margin-bottom: 22px;
            text-align: center;
            border-radius: 8px;
            overflow: hidden;
            background: #f8fafc;
        }}

        .hero-image {{
            width: 100%;
            max-height: 480px;
            object-fit: cover;
            border-radius: 6px;
            display: block;
        }}

        /* Article Content Typography */
        .article-body {{
            font-size: 12.5pt;
            color: #2d3748;
            line-height: 1.8;
            text-align: justify;
        }}

        .article-body p {{
            margin-bottom: 16px;
        }}

        .article-body h2 {{
            font-size: 16pt;
            font-weight: 700;
            margin-top: 24px;
            margin-bottom: 12px;
            color: #1a202c;
        }}

        .article-body h3 {{
            font-size: 14pt;
            font-weight: 600;
            margin-top: 20px;
            margin-bottom: 10px;
            color: #2d3748;
        }}

        .article-body blockquote {{
            margin: 18px 0;
            padding: 10px 18px;
            background-color: #f7fafc;
            border-left: 4px solid #3182ce;
            font-style: italic;
            color: #4a5568;
        }}

        .article-body figure {{
            margin: 20px 0;
            text-align: center;
        }}

        .article-body figure img {{
            max-width: 100%;
            height: auto;
            border-radius: 6px;
        }}

        /* Archival Footer */
        .reader-footer {{
            margin-top: 30px;
            padding-top: 14px;
            border-top: 1px solid #e2e8f0;
            font-size: 8.5pt;
            color: #718096;
            word-break: break-all;
        }}

        .reader-footer a {{
            color: #3182ce;
            text-decoration: none;
        }}

        /* Print formatting */
        @media print {{
            body {{
                font-size: 11pt;
                line-height: 1.6;
            }}
            .article-title {{
                font-size: 17pt;
            }}
            .hero-image {{
                max-height: 380px;
            }}
            .site-header {{
                page-break-after: avoid;
            }}
            .article-title, .hero-image-container {{
                page-break-inside: avoid;
            }}
        }}
    </style>
</head>
<body>
    <div class="reader-container">
        <!-- 1. Header with Site Logo, Site Name, Published Date & Top Source -->
        <header class="site-header">
            <div class="site-brand">
                {logo_markup}
                <div class="brand-details">
                    <span class="site-name">{site_name}</span>
                    {header_date_markup}
                </div>
            </div>
            <div class="source-pill">
                <strong>Source:</strong> <a href="{escaped_source_url}">{escaped_source_url}</a>
            </div>
        </header>

        <!-- 2. Headline / Title -->
        <h1 class="article-title">{title}</h1>

        <!-- 3. Meta Information Bar with Full URL -->
        <div class="article-meta">
            {date_markup}
            <span class="meta-item">🌐 <strong>Portal:</strong> {site_name}</span>
            <span class="meta-item">🔗 <strong>Link:</strong> <a href="{escaped_source_url}">{escaped_source_url}</a></span>
        </div>

        <!-- 4. Main Featured Image -->
        {hero_image_markup}

        <!-- 5. Clean Article Body -->
        <article class="article-body">
            {article_html}
        </article>

        <!-- 6. Archival Citation Footer -->
        <footer class="reader-footer">
            <div>Archived Page • Source: <a href="{escaped_source_url}">{escaped_source_url}</a></div>
        </footer>
    </div>
</body>
</html>
"""


async def process_single_url(
    browser,
    url: str,
    index: int,
    total: int,
    output_dir: Path,
    date: str = "",
    timeout_ms: int = 30000,
    semaphore: asyncio.Semaphore = None,
) -> dict:
    """
    Processes a single URL inside a concurrency semaphore:
    1. Loads target URL and extracts metadata + components.
    2. If a print view / "প্রিন্ট" button or URL exists on the site:
       - Navigates directly to print mode to extract the clean article body.
       - Guarantees the original source URL and site logo remain in the header.
    3. If article body text is found:
       - Builds clean reader-mode HTML and renders PDF directly.
    4. If NO article body text is found:
       - Captures and renders the full webpage to PDF directly.
    5. Saves output PDF into date-based subfolder in output_dir if date is present.
    """
    async with semaphore:
        start_time = time.time()
        base_url = extract_base_url(url)
        logger.info(f"[{index}/{total}] 🚀 Starting: {url} (Domain: {base_url})")

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            is_mobile=False,
            has_touch=False,
            device_scale_factor=1,
        )
        page = await context.new_page()

        try:
            # 1. Fetch live page
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(1500)
            except PlaywrightTimeoutError:
                logger.warning(f"[{index}/{total}] ⚠️ Timeout loading {url}, attempting extraction from partial DOM...")
            except Exception as e:
                err_msg = f"Failed to load URL {url}: {e}"
                logger.error(f"[{index}/{total}] ❌ {err_msg}")
                await context.close()
                return {"url": url, "success": False, "detail": err_msg, "filename": "", "rel_path": ""}

            # 2. Extract target components from the main page
            data = await page.evaluate(EXTRACTOR_JS)

            # 3. If a print view / "প্রিন্ট" mode button or URL is found, navigate to that mode
            print_url = data.get("printUrl")
            used_print_mode = False
            if print_url:
                try:
                    logger.info(f"[{index}/{total}] 🖨️ Found Print / প্রিন্ট button/URL ({print_url}). Loading Print Mode...")
                    print_page = await context.new_page()
                    await print_page.goto(print_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    await print_page.wait_for_timeout(1000)
                    print_data = await print_page.evaluate(EXTRACTOR_JS)
                    if print_data.get("hasBodyText") and print_data.get("textLength", 0) > 50:
                        # Retain original site logo, hero image, and title if print page lacks them
                        if not print_data.get("logoUrl") and data.get("logoUrl"):
                            print_data["logoUrl"] = data["logoUrl"]
                        if not print_data.get("mainImageUrl") and data.get("mainImageUrl"):
                            print_data["mainImageUrl"] = data["mainImageUrl"]
                        if not print_data.get("title") or print_data.get("title", "").lower().startswith("print"):
                            if data.get("title"):
                                print_data["title"] = data["title"]
                        data = print_data
                        used_print_mode = True
                    await print_page.close()
                except Exception as e:
                    logger.warning(f"[{index}/{total}] ⚠️ Could not fetch print URL {print_url}: {e}")

            title = data.get("title") or "webpage"
            # Strip "Print | " prefixes if present from print pages
            title = re.sub(r'^(print\s*\|\s*|প্রিন্ট\s*\|\s*)', '', title, flags=re.IGNORECASE).strip()
            clean_title = sanitize_filename(title, max_length=120)
            filename = f"{base_url}_{clean_title}.pdf"

            # Determine target output directory (create date subfolder if date provided)
            if date:
                clean_date = sanitize_filename(date, max_length=50)
                target_dir = output_dir / clean_date
            else:
                target_dir = output_dir

            target_dir.mkdir(parents=True, exist_ok=True)
            output_pdf_path = target_dir / filename

            # Handle duplicate filename collisions
            counter = 1
            while output_pdf_path.exists():
                filename = f"{base_url}_{clean_title}_{counter}.pdf"
                output_pdf_path = target_dir / filename
                counter += 1

            has_body_text = data.get("hasBodyText", False) and bool(data.get("articleHtml", "").strip())

            if has_body_text:
                # 4a. Generate Clean Reader HTML (always passing the original source URL and fallback date)
                reader_html = build_reader_html(data, url, fallback_date=date)

                # 5a. Load reader HTML in a fresh clean page to render PDF
                render_page = await context.new_page()
                await render_page.set_content(reader_html, wait_until="networkidle")

                # Explicitly wait for all images on the page to finish loading/decoding
                try:
                    await render_page.evaluate("""async () => {
                        const images = Array.from(document.querySelectorAll('img'));
                        await Promise.all(images.map(img => {
                            if (img.complete && img.naturalWidth > 0) return Promise.resolve();
                            return new Promise(resolve => {
                                img.addEventListener('load', resolve);
                                img.addEventListener('error', resolve);
                                setTimeout(resolve, 3000);
                            });
                        }));
                    }""")
                except Exception:
                    pass

                await render_page.wait_for_timeout(500)

                # Render pristine reader mode PDF
                await render_page.pdf(
                    path=str(output_pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "14mm", "bottom": "14mm", "left": "12mm", "right": "12mm"},
                    scale=0.9,
                )
                mode_label = "Print Mode" if used_print_mode else "Reader Mode"
            else:
                # 4b. Fallback: Archive the entire webpage directly
                logger.info(f"[{index}/{total}] ℹ️ No article body text found for {url}. Archiving full webpage.")
                await page.emulate_media(media="screen")
                await page.wait_for_timeout(1000)
                await page.pdf(
                    path=str(output_pdf_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"},
                )
                mode_label = "Full Webpage"

            elapsed = round(time.time() - start_time, 2)
            try:
                rel_path = output_pdf_path.relative_to(output_dir)
            except ValueError:
                rel_path = Path(output_pdf_path.name)
            rel_path_str = str(rel_path).replace("\\", "/")
            logger.info(f"[{index}/{total}] ✅ Saved PDF ({elapsed}s) [{mode_label}]: {rel_path_str}")
            await context.close()
            return {"url": url, "success": True, "detail": str(output_pdf_path), "filename": filename, "rel_path": rel_path_str, "mode": mode_label, "elapsed": elapsed}

        except Exception as e:
            err_msg = f"Failed to generate PDF for {url}: {e}"
            logger.error(f"[{index}/{total}] ❌ {err_msg}")
            try:
                await context.close()
            except Exception:
                pass
            return {"url": url, "success": False, "detail": err_msg, "filename": "", "rel_path": ""}


async def run_archiver(args):
    csv_path = Path(args.csv).resolve()
    output_dir = Path(args.output).resolve()
    concurrency = max(1, args.concurrency)

    logger.info("=" * 60)
    logger.info("🚀 Webpage to PDF Archiver (Reader Mode & Multi-threaded)")
    logger.info(f"CSV File    : {csv_path}")
    logger.info(f"Output Dir  : {output_dir}")
    logger.info(f"Concurrency : {concurrency} workers")
    logger.info(f"Timeout     : {args.timeout} ms")
    logger.info("=" * 60)

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        entries = read_entries_from_csv(csv_path)
    except Exception as e:
        logger.error(f"Error reading CSV: {e}")
        sys.exit(1)

    if not entries:
        logger.warning("No URLs found to process.")
        return

    if args.limit and args.limit > 0:
        entries = entries[:args.limit]
        logger.info(f"Processing limit applied: first {len(entries)} item(s)")

    logger.info(f"Queued {len(entries)} URL(s) for processing with {concurrency} parallel workers...\n")

    semaphore = asyncio.Semaphore(concurrency)

    start_total = time.time()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=args.headless)

        tasks = [
            process_single_url(
                browser=browser,
                url=item["url"],
                date=item.get("date", ""),
                index=idx,
                total=len(entries),
                output_dir=output_dir,
                timeout_ms=args.timeout,
                semaphore=semaphore,
            )
            for idx, item in enumerate(entries, 1)
        ]

        results = await asyncio.gather(*tasks)
        await browser.close()

    total_time = round(time.time() - start_total, 2)
    success_count = sum(1 for r in results if r["success"])
    fail_count = len(results) - success_count

    logger.info("\n" + "=" * 60)
    logger.info("✨ PROCESSING SUMMARY")
    logger.info(f"Total Processed : {len(results)}")
    logger.info(f"Successful      : {success_count}")
    logger.info(f"Failed          : {fail_count}")
    logger.info(f"Total Time Taken: {total_time}s")
    logger.info(f"Output Path     : {output_dir}")
    logger.info("=" * 60)

    if fail_count > 0:
        logger.warning("The following URLs failed to process:")
        for r in results:
            if not r["success"]:
                logger.warning(f" - {r['url']}: {r['detail']}")


def main():
    parser = argparse.ArgumentParser(description="Webpage to PDF Archiver (Reader Mode & Multi-threaded)")
    parser.add_argument(
        "--csv",
        type=str,
        default="data.csv",
        help="Path to the CSV file containing URLs (default: data.csv)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="output",
        help="Directory to save generated PDFs (default: output)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent pages/workers to run simultaneously (default: 5)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="Page load timeout in milliseconds (default: 30000)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional: Limit to processing only the first N URLs from the CSV (e.g. --limit 5)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode (default: True)",
    )

    args = parser.parse_args()
    asyncio.run(run_archiver(args))


if __name__ == "__main__":
    main()
