#!/usr/bin/env python3
"""
Tanzania TIE Textbooks Downloader — Form 1–4, All Subjects
===========================================================
Downloads all official Tanzania Institute of Education (TIE) textbooks
for O-Level secondary school (Forms 1–4).

IMPORTANT
---------
Run this script from your LOCAL machine (home / office internet).
Tanzanian government servers block cloud/data-centre IP ranges.
If you see "403 host_not_allowed", switch to a regular network or VPN.

Install dependencies
--------------------
    pip install requests beautifulsoup4 lxml reportlab tqdm
    pip install weasyprint        # optional – best PDF quality (preserves images)

Usage examples
--------------
    python download_tie_books.py                     # download everything
    python download_tie_books.py --check             # scan availability, no download
    python download_tie_books.py --forms 1 2         # Forms 1 & 2 only
    python download_tie_books.py --core-only         # 9 compulsory subjects only
    python download_tie_books.py --subjects Biology Chemistry Physics
    python download_tie_books.py --output ~/Desktop/TIE_Books
    python download_tie_books.py --selftest          # verify script integrity
    python download_tie_books.py --list              # print full subject list
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import logging
import argparse
import tempfile
import textwrap
from pathlib import Path
from typing import Optional
from urllib.parse import quote

# ── required third-party ────────────────────────────────────────────────────
try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    sys.exit("ERROR: 'requests' not installed.  Run:  pip install requests")

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

try:
    from tqdm import tqdm as _tqdm
    _TQDM = True
except ImportError:
    _TQDM = False

def _tqdm_wrap(iterable, **kw):
    return _tqdm(iterable, **kw) if _TQDM else iter(iterable)

# ── optional PDF backends ────────────────────────────────────────────────────
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
    from reportlab.lib.enums import TA_LEFT
    _REPORTLAB = True
except ImportError:
    _REPORTLAB = False

try:
    import weasyprint as _weasyprint
    _WEASYPRINT = True
except ImportError:
    _WEASYPRINT = False

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tie_dl")

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
TIE_BASE    = "https://ol.tie.go.tz/uploaded_files/books"
REQ_TIMEOUT = 45
PAGE_DELAY  = 0.35   # seconds between page requests inside one book
BOOK_DELAY  = 1.2    # seconds between books (respectful to server)

FORM_WORD = {1: "One", 2: "Two", 3: "Three", 4: "Four"}

CORE_SUBJECTS = frozenset({
    "Basic Mathematics", "English Language", "Kiswahili",
    "Biology", "Chemistry", "Physics",
    "History", "Geography", "Civics",
})

# ─────────────────────────────────────────────────────────────────────────────
# COMPLETE TEXTBOOK CATALOGUE
#
#   subject_name → { form_int → [list of base-path variants] }
#
# Paths are appended to TIE_BASE.
# The leading "//" matches what the TIE server actually publishes.
# Both "//" (old style) and "/" (new 2025 style) variants are listed.
# ─────────────────────────────────────────────────────────────────────────────
CATALOGUE: dict[str, dict[int, list[str]]] = {

    # ── CORE / COMPULSORY ───────────────────────────────────────────────────
    "Basic Mathematics": {
        1: ["//secondary/Form_One/Mathematics_Form_One"],
        2: ["//secondary/Form_Two/Mathematics_Form_Two"],
        3: ["//secondary/Form_Three/Mathematics_Form_Three",
            "/secondary/Form_Three/Mathematics/2025"],
        4: ["//secondary/Form_Four/Mathematics_Form_Four",
            "/secondary/Form_Four/Mathematics/2025"],
    },
    "English Language": {
        1: ["//secondary/Form_One/English_Form_One"],
        2: ["//secondary/Form_Two/English_Form_Two"],
        3: ["//secondary/Form_Three/English_Form_Three",
            "/secondary/Form_Three/English/2025"],
        4: ["//secondary/Form_Four/English_Form_Four",
            "/secondary/Form_Four/English/2025"],
    },
    "Kiswahili": {
        1: ["//secondary/Form_One/Kiswahili_Form_One"],
        2: ["//secondary/Form_Two/Kiswahili_Form_Two"],
        3: ["//secondary/Form_Three/Kiswahili_Form_Three",
            "/secondary/Form_Three/Kiswahili/2025"],
        4: ["//secondary/Form_Four/Kiswahili_Form_Four",
            "/secondary/Form_Four/Kiswahili/2025"],
    },
    "Biology": {
        1: ["//secondary/Form_One/Biology_Form_One"],
        2: ["//secondary/Form_Two/Biology_Form_Two"],
        3: ["/secondary/Form_Three/Biology/2025",
            "//secondary/Form_Three/Biology_Form_Three"],
        4: ["/secondary/Form_Four/Biology/2025",
            "//secondary/Form_Four/Biology_Form_Four"],
    },
    "Chemistry": {
        1: ["//secondary/Form_One/Chemistry_Form_One"],
        2: ["//secondary/Form_Two/Chemistry_Form_Two"],
        3: ["//secondary/Form_Three/Chemistry_Form_Three",
            "/secondary/Form_Three/Chemistry/2025"],
        4: ["//secondary/Form_Four/Chemistry_Form_Four",
            "/secondary/Form_Four/Chemistry/2025"],
    },
    "Physics": {
        1: ["//secondary/Form_One/Physics_Form_One"],
        2: ["//secondary/Form_Two/Physics_Form_Two"],
        3: ["//secondary/Form_Three/Physics_Form_Three",
            "/secondary/Form_Three/Physics/2025"],
        4: ["//secondary/Form_Four/Physics_Form_Four",
            "/secondary/Form_Four/Physics/2025"],
    },
    "History": {
        1: ["//secondary/Form_One/History",
            "//secondary/Form_One/History_Form_One"],
        2: ["//secondary/Form_Two/History",
            "//secondary/Form_Two/History_Form_Two"],
        3: ["//secondary/Form_Three/History",
            "//secondary/Form_Three/History_Form_Three",
            "/secondary/Form_Three/History/2025"],
        4: ["//secondary/Form_Four/History",
            "//secondary/Form_Four/History_Form_Four",
            "/secondary/Form_Four/History/2025"],
    },
    "Geography": {
        1: ["//secondary/Form_One/Geography_Form_One"],
        2: ["//secondary/Form_Two/Geography_Form_Two"],
        3: ["//secondary/Form_Three/Geography_Form_Three",
            "/secondary/Form_Three/Geography/2025"],
        4: ["//secondary/Form_Four/Geography_Form_Four",
            "/secondary/Form_Four/Geography/2025"],
    },
    "Civics": {
        1: ["//secondary/Form_One/Civics_Form_One"],
        2: ["//secondary/Form_Two/Civics_Form_Two"],
        3: ["//secondary/Form_Three/Civics_Form_Three",
            "/secondary/Form_Three/Civics/2025"],
        4: ["//secondary/Form_Four/Civics_Form_Four",
            "/secondary/Form_Four/Civics/2025"],
    },

    # ── OPTIONAL / ELECTIVE ─────────────────────────────────────────────────
    "Agriculture": {
        1: ["//secondary/Form_One/Agriculture_Form_One"],
        2: ["//secondary/Form_Two/Agriculture_Form_Two"],
        3: ["//secondary/Form_Three/Agriculture_Form_Three",
            "/secondary/Form_Three/Agriculture/2025"],
        4: ["//secondary/Form_Four/Agriculture_Form_Four",
            "/secondary/Form_Four/Agriculture/2025"],
    },
    "Book-keeping": {
        1: ["//secondary/Form_One/Book-keeping_Form_One"],
        2: ["//secondary/Form_Two/Book-keeping_Form_Two"],
        3: ["//secondary/Form_Three/Book-keeping_Form_Three"],
        4: ["//secondary/Form_Four/Book-keeping_Form_Four"],
    },
    "Commerce": {
        1: ["//secondary/Form_One/Commerce_Form_One"],
        2: ["//secondary/Form_Two/Commerce_Form_Two"],
        3: ["//secondary/Form_Three/Commerce_Form_Three"],
        4: ["//secondary/Form_Four/Commerce_Form_Four"],
    },
    "Computer Studies": {
        1: ["//secondary/Form_One/Computer_Studies_Form_One",
            "//secondary/Form_One/ICT_Form_One"],
        2: ["//secondary/Form_Two/Computer_Studies_Form_Two",
            "//secondary/Form_Two/ICT_Form_Two"],
        3: ["//secondary/Form_Three/Computer_Studies_Form_Three"],
        4: ["//secondary/Form_Four/Computer_Studies_Form_Four"],
    },
    "Fine Arts": {
        1: ["//secondary/Form_One/Fine_Arts_Form_One"],
        2: ["//secondary/Form_Two/Fine_Arts_Form_Two"],
        3: ["//secondary/Form_Three/Fine_Arts_Form_Three"],
        4: ["//secondary/Form_Four/Fine_Arts_Form_Four"],
    },
    "Home Economics": {
        1: ["//secondary/Form_One/Home_Economics_Form_One"],
        2: ["//secondary/Form_Two/Home_Economics_Form_Two"],
        3: ["//secondary/Form_Three/Home_Economics_Form_Three"],
        4: ["//secondary/Form_Four/Home_Economics_Form_Four"],
    },
    "French": {
        1: ["//secondary/Form_One/French_Form_One"],
        2: ["//secondary/Form_Two/French_Form_Two"],
        3: ["//secondary/Form_Three/French_Form_Three"],
        4: ["//secondary/Form_Four/French_Form_Four"],
    },
    "Music": {
        1: ["//secondary/Form_One/Music_Form_One"],
        2: ["//secondary/Form_Two/Music_Form_Two"],
        3: ["//secondary/Form_Three/Music_Form_Three"],
        4: ["//secondary/Form_Four/Music_Form_Four"],
    },
    "Bible Knowledge": {
        1: ["//secondary/Form_One/Bible_Knowledge_Form_One"],
        2: ["//secondary/Form_Two/Bible_Knowledge_Form_Two"],
        3: ["//secondary/Form_Three/Bible_Knowledge_Form_Three"],
        4: ["//secondary/Form_Four/Bible_Knowledge_Form_Four"],
    },
    "Islamic Knowledge": {
        1: ["//secondary/Form_One/Islamic_Knowledge_Form_One"],
        2: ["//secondary/Form_Two/Islamic_Knowledge_Form_Two"],
        3: ["//secondary/Form_Three/Islamic_Knowledge_Form_Three"],
        4: ["//secondary/Form_Four/Islamic_Knowledge_Form_Four"],
    },
    "Physical Education": {
        1: ["//secondary/Form_One/Physical_Education_Form_One"],
        2: ["//secondary/Form_Two/Physical_Education_Form_Two"],
        3: ["//secondary/Form_Three/Physical_Education_Form_Three"],
        4: ["//secondary/Form_Four/Physical_Education_Form_Four"],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# HTTP SESSION  (with automatic retry)
# ─────────────────────────────────────────────────────────────────────────────
def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9,sw;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    return s

SESSION = _build_session()
_BLOCKED_WARNED = False


def _get(url: str) -> Optional[requests.Response]:
    """GET url, return Response on 200 else None."""
    global _BLOCKED_WARNED
    try:
        r = SESSION.get(url, timeout=REQ_TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            return r
        if r.status_code == 403 and not _BLOCKED_WARNED:
            deny = r.headers.get("x-deny-reason", "")
            if deny == "host_not_allowed":
                _BLOCKED_WARNED = True
                log.error(
                    "\n"
                    "  ╔══════════════════════════════════════════════════╗\n"
                    "  ║  Server says: host_not_allowed (403)             ║\n"
                    "  ║  The TIE server blocks cloud/VPS IP ranges.      ║\n"
                    "  ║  → Run this script from your HOME/OFFICE network ║\n"
                    "  ║    or connect via a Tanzanian VPN.               ║\n"
                    "  ╚══════════════════════════════════════════════════╝\n"
                )
        return None
    except requests.exceptions.ConnectionError:
        return None
    except requests.exceptions.Timeout:
        log.debug("Timeout: %s", url)
        return None
    except Exception as exc:
        log.debug("GET %s — %s: %s", url, type(exc).__name__, exc)
        return None


def _is_pdf(data: bytes) -> bool:
    return len(data) > 4 and data[:4] == b"%PDF"


# ─────────────────────────────────────────────────────────────────────────────
# TEXT EXTRACTION FROM HTML
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text(html: str) -> str:
    """Return clean plain-text from an HTML page."""
    if not _BS4:
        # crude strip if bs4 unavailable
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s{2,}", "\n", text).strip()

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "header",
                     "footer", "aside", "iframe", "noscript"]):
        tag.decompose()

    body = soup.find("body") or soup
    text = body.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# PDF CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def _weasyprint_to_pdf(html: str, base_url: str, out: Path) -> bool:
    if not _WEASYPRINT:
        return False
    try:
        _weasyprint.HTML(string=html, base_url=base_url).write_pdf(str(out))
        return out.exists() and out.stat().st_size > 500
    except Exception as exc:
        log.debug("WeasyPrint: %s", exc)
        return False


def _xml_esc(t: str) -> str:
    """Escape characters that would break ReportLab's XML parser."""
    return (t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _reportlab_to_pdf(pages: list[tuple[int, str]], out: Path, title: str) -> bool:
    """Convert (page_num, html_text) list → PDF via ReportLab (text only)."""
    if not _REPORTLAB:
        return False
    try:
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "BookTitle",
            parent=styles["Heading1"],
            fontSize=14, spaceAfter=6,
        )
        body_style = ParagraphStyle(
            "BookBody",
            parent=styles["Normal"],
            fontSize=8, leading=11,
            leftIndent=0, rightIndent=0,
            spaceAfter=2, alignment=TA_LEFT,
        )
        page_hdr_style = ParagraphStyle(
            "PageHdr",
            parent=styles["Italic"],
            fontSize=7, textColor="grey", spaceAfter=4,
        )

        doc   = SimpleDocTemplate(str(out), pagesize=A4,
                                  leftMargin=18*mm, rightMargin=18*mm,
                                  topMargin=18*mm,  bottomMargin=18*mm)
        story = [Paragraph(_xml_esc(title), title_style), Spacer(1, 6*mm)]

        for page_num, html in _tqdm_wrap(pages,
                                         desc=f"  Building PDF: {title}",
                                         leave=False):
            text = _extract_text(html)
            if not text:
                continue

            story.append(Paragraph(f"— Page {page_num} —", page_hdr_style))
            for para in text.split("\n\n"):
                para = para.strip()
                if not para:
                    continue
                # wrap long lines
                safe = _xml_esc(para)
                try:
                    story.append(Paragraph(safe, body_style))
                except Exception:
                    story.append(Spacer(1, 2))
            story.append(Spacer(1, 3*mm))

        story.append(PageBreak())
        doc.build(story)
        return out.exists() and out.stat().st_size > 500

    except Exception as exc:
        log.debug("ReportLab error: %s", exc)
        return False


def _save_html_bundle(pages: list[tuple[int, str]],
                      out: Path, title: str) -> bool:
    """Last resort: combine all pages into a single HTML file."""
    html_out = out.with_suffix(".html")
    parts = [
        "<!DOCTYPE html><html><head>",
        f'<meta charset="utf-8"><title>{title}</title>',
        "<style>body{font-family:sans-serif;max-width:900px;margin:auto;padding:20px}"
        "hr{border:1px solid #ccc;margin:20px 0}</style>",
        "</head><body>",
        f"<h1>{title}</h1>",
    ]
    for n, html in pages:
        parts.append(f'<hr/><p style="color:grey;font-size:11px">— Page {n} —</p>')
        if _BS4:
            try:
                soup = BeautifulSoup(html, "lxml")
                body = soup.find("body")
                parts.append(str(body) if body else html)
                continue
            except Exception:
                pass
        parts.append(html)
    parts.append("</body></html>")

    html_out.write_text("\n".join(parts), encoding="utf-8")
    log.info("  Saved HTML bundle: %s", html_out.name)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOAD STRATEGIES
# ─────────────────────────────────────────────────────────────────────────────

def _try_direct_pdf(base_path: str, subject: str, form: int) -> Optional[bytes]:
    """Strategy 1 — probe common PDF filename patterns."""
    fw   = FORM_WORD[form]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", subject).strip("_")

    candidates = [
        f"{slug}_Form_{fw}.pdf",
        f"{slug}.pdf",
        f"{subject.replace(' ','_')}_Form_{fw}.pdf",
        f"{subject.replace(' ','_')}.pdf",
        f"Form_{fw}_{slug}.pdf",
        f"{slug}_Students_Book_Form_{fw}.pdf",
        f"{slug}_Student_Book_Form_{fw}.pdf",
        f"TIE_{slug}_Form_{fw}.pdf",
        f"{slug}_Secondary_Schools_Form_{fw}.pdf",
        f"Secondary_Schools_{slug}_Form_{fw}.pdf",
    ]

    for name in candidates:
        url = f"{TIE_BASE}{base_path}/{quote(name)}"
        r = _get(url)
        if r and _is_pdf(r.content):
            return r.content

    return None


def _fetch_html_pages(base_path: str) -> list[tuple[int, str]]:
    """Strategy 2 — download page1.html … pageN.html from the online reader."""
    pages: list[tuple[int, str]] = []

    # Sequential pages
    for n in range(1, 3000):
        url = f"{TIE_BASE}{base_path}/files/basic-html/page{n}.html"
        r = _get(url)
        if r is None:
            break
        pages.append((n, r.text))
        time.sleep(PAGE_DELAY)

    # Fallback: parse index.html for page links
    if not pages and _BS4:
        idx = _get(f"{TIE_BASE}{base_path}/files/basic-html/index.html")
        if idx:
            soup = BeautifulSoup(idx.text, "lxml")
            hrefs = sorted(
                {a["href"] for a in soup.find_all("a", href=re.compile(r"page\d+\.html"))},
                key=lambda h: int(re.search(r"(\d+)", h).group(1)),
            )
            for href in hrefs:
                r = _get(f"{TIE_BASE}{base_path}/files/basic-html/{href}")
                if r:
                    n = int(re.search(r"(\d+)", href).group(1))
                    pages.append((n, r.text))
                    time.sleep(PAGE_DELAY)

    return pages


def _fetch_single_html(base_path: str) -> Optional[tuple[str, str]]:
    """Strategy 3 — book as one large HTML file."""
    seg = base_path.rstrip("/").split("/")[-1]
    for name in [f"{seg}.html", "index.html", "book.html", "content.html"]:
        url = f"{TIE_BASE}{base_path}/{quote(name)}"
        r = _get(url)
        if r and len(r.text) > 3_000:
            return (url, r.text)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STATUS CODES
# ─────────────────────────────────────────────────────────────────────────────
ST_PDF       = "downloaded_pdf"
ST_TEXT_PDF  = "downloaded_text_pdf"
ST_HTML      = "saved_html_bundle"
ST_SKIP      = "skipped_exists"
ST_FAIL      = "not_found"
ST_BLOCKED   = "blocked_403"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN BOOK DOWNLOADER
# ─────────────────────────────────────────────────────────────────────────────

def download_book(subject: str, form: int, paths: list[str],
                  out_dir: Path, check_only: bool = False) -> str:
    fw       = FORM_WORD[form]
    safe     = re.sub(r"[^A-Za-z0-9]+", "_", subject).strip("_")
    pdf_path = out_dir / f"{safe}_Form_{fw}.pdf"
    htm_path = pdf_path.with_suffix(".html")

    if pdf_path.exists():
        log.info("  ↷ Exists: %s", pdf_path.name)
        return ST_SKIP
    if htm_path.exists():
        log.info("  ↷ Exists (html): %s", htm_path.name)
        return ST_SKIP

    label = f"{subject} — Form {form}"
    blocked = False

    for base_path in paths:

        if check_only:
            for probe in [
                f"{TIE_BASE}{base_path}/files/basic-html/page1.html",
                f"{TIE_BASE}{base_path}/files/basic-html/index.html",
            ]:
                r = _get(probe)
                if r:
                    return ST_HTML      # reused as "available" in check mode
            # probe 403
            try:
                r2 = SESSION.get(
                    f"{TIE_BASE}{base_path}/files/basic-html/page1.html",
                    timeout=10)
                if r2.status_code == 403:
                    blocked = True
            except Exception:
                pass
            continue   # try next path variant

        # ── Strategy 1: direct PDF ───────────────────────────────────────
        pdf_bytes = _try_direct_pdf(base_path, subject, form)
        if pdf_bytes:
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(pdf_bytes)
            log.info("  ✔ Direct PDF (%d KB): %s",
                     len(pdf_bytes) // 1024, pdf_path.name)
            return ST_PDF

        # ── Strategy 2: paginated HTML reader ────────────────────────────
        pages = _fetch_html_pages(base_path)
        if pages:
            out_dir.mkdir(parents=True, exist_ok=True)
            log.info("  Fetched %d HTML pages for %s", len(pages), label)

            # build combined HTML for WeasyPrint
            combined_html = (
                "<!DOCTYPE html><html><head>"
                f'<meta charset="utf-8"><title>{label}</title></head><body>'
                + "".join(h for _, h in pages)
                + "</body></html>"
            )
            base_for_css = f"{TIE_BASE}{base_path}/files/basic-html/"

            if _weasyprint_to_pdf(combined_html, base_for_css, pdf_path):
                log.info("  ✔ WeasyPrint PDF (%d pages): %s",
                         len(pages), pdf_path.name)
                return ST_PDF

            if _reportlab_to_pdf(pages, pdf_path, label):
                log.info("  ✔ ReportLab text-PDF (%d pages): %s",
                         len(pages), pdf_path.name)
                return ST_TEXT_PDF

            if _save_html_bundle(pages, htm_path, label):
                return ST_HTML

        # ── Strategy 3: single HTML ───────────────────────────────────────
        result = _fetch_single_html(base_path)
        if result:
            src_url, html_text = result
            out_dir.mkdir(parents=True, exist_ok=True)
            if _weasyprint_to_pdf(html_text, src_url, pdf_path):
                log.info("  ✔ WeasyPrint PDF (single HTML): %s", pdf_path.name)
                return ST_PDF
            if _reportlab_to_pdf([(1, html_text)], pdf_path, label):
                log.info("  ✔ ReportLab PDF (single HTML): %s", pdf_path.name)
                return ST_TEXT_PDF

        # detect 403 blocking
        try:
            r403 = SESSION.get(
                f"{TIE_BASE}{base_path}/files/basic-html/page1.html",
                timeout=10)
            if r403.status_code == 403:
                blocked = True
        except Exception:
            pass

    if blocked:
        return ST_BLOCKED

    log.warning("  ✗ Not found: %s", label)
    return ST_FAIL


# ─────────────────────────────────────────────────────────────────────────────
# SELF-TEST
# ─────────────────────────────────────────────────────────────────────────────

def run_selftest() -> None:
    ok = True
    print("\n┌─ Self-test ─────────────────────────────────────────────┐")

    # 1. Catalogue integrity
    issues: list[str] = []
    for subj, forms in CATALOGUE.items():
        for fn, paths in forms.items():
            if fn not in (1, 2, 3, 4):
                issues.append(f"Bad form number {fn} in '{subj}'")
            if not paths or not isinstance(paths, list):
                issues.append(f"Empty/bad paths for '{subj}' Form {fn}")
            for p in paths:
                if not p.startswith("/"):
                    issues.append(f"Path doesn't start with '/': {p!r}")
    total_entries = sum(len(f) for f in CATALOGUE.values())
    if issues:
        for i in issues:
            print(f"│  FAIL: {i}")
        ok = False
    else:
        print(f"│  ✔ Catalogue: {len(CATALOGUE)} subjects, {total_entries} entries")

    # 2. Core subjects present
    missing = CORE_SUBJECTS - set(CATALOGUE.keys())
    if missing:
        print(f"│  WARN: Core subjects missing: {missing}")
    else:
        print(f"│  ✔ Core subjects: all {len(CORE_SUBJECTS)} present")

    # 3. PDF backends
    if _WEASYPRINT:
        print("│  ✔ WeasyPrint available (best PDF quality)")
    else:
        print("│  ·  WeasyPrint not installed  (pip install weasyprint)")
    if _REPORTLAB:
        print("│  ✔ ReportLab available (text-only PDF fallback)")
    else:
        print("│  ·  ReportLab not installed  (pip install reportlab)")

    # 4. ReportLab write test
    if _REPORTLAB:
        try:
            tmp = Path(tempfile.mktemp(suffix=".pdf"))
            result = _reportlab_to_pdf(
                [(1, "<html><body><h1>Test</h1><p>Tanzania TIE Textbook Downloader works.</p></body></html>")],
                tmp, "Self-test Book",
            )
            if result and tmp.exists() and tmp.stat().st_size > 200:
                print(f"│  ✔ ReportLab write test: {tmp.stat().st_size} bytes")
                tmp.unlink(missing_ok=True)
            else:
                print("│  WARN: ReportLab write produced no output")
                ok = False
        except Exception as exc:
            print(f"│  WARN: ReportLab write test error: {exc}")

    # 5. HTML extraction
    sample_html = "<html><body><p>Habari ya Tanzania.</p></body></html>"
    text = _extract_text(sample_html)
    if "Habari" in text:
        print("│  ✔ HTML text extraction OK")
    else:
        print("│  WARN: HTML text extraction returned empty string")

    # 6. Session / HTTP
    print(f"│  ✔ HTTP session built (timeout={REQ_TIMEOUT}s, retries=3)")

    # 7. URL pattern sample
    sample = f"{TIE_BASE}//secondary/Form_One/Biology_Form_One/files/basic-html/page1.html"
    print(f"│  ✔ Sample URL: {sample}")

    print("└─────────────────────────────────────────────────────────┘")
    if ok:
        print("  Self-test PASSED ✔\n")
    else:
        print("  Self-test completed with warnings.\n")


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner(mode, forms, n_subj, total, out_root):
    W = 65
    print()
    print("━" * W)
    print("  Tanzania TIE Textbooks Downloader — Form 1–4")
    print("━" * W)
    print(f"  Mode          : {mode}")
    print(f"  Forms         : {forms}")
    print(f"  Subjects      : {n_subj}")
    print(f"  Books total   : {total}")
    print(f"  Output dir    : {out_root.resolve()}")
    pdf_be = ("WeasyPrint" if _WEASYPRINT
              else "ReportLab" if _REPORTLAB
              else "HTML-bundle (no PDF library installed)")
    print(f"  PDF backend   : {pdf_be}")
    print("━" * W)
    print()


def _print_summary(res, check_only, total, out_root):
    W = 65
    print()
    print("━" * W)
    print("  SUMMARY")
    print("━" * W)
    if not check_only:
        n_pdf  = len(res[ST_PDF]) + len(res[ST_TEXT_PDF])
        n_html = len(res[ST_HTML])
        n_skip = len(res[ST_SKIP])
        n_fail = len(res[ST_FAIL])
        n_blk  = len(res[ST_BLOCKED])
        print(f"  ✔ PDFs saved          : {n_pdf:>4}")
        print(f"  ✔ HTML bundles saved  : {n_html:>4}")
        print(f"  ↷ Skipped (exists)    : {n_skip:>4}")
        print(f"  ✗ Not found           : {n_fail:>4}")
        if n_blk:
            print(f"  ✗ Blocked (403)       : {n_blk:>4}")
            print()
            print("  *** Server blocked this IP (host_not_allowed). ***")
            print("  *** Run from a HOME or OFFICE network instead.  ***")
    else:
        n_avail = len(res[ST_HTML]) + len(res[ST_PDF])
        n_blk   = len(res[ST_BLOCKED])
        n_fail  = len(res[ST_FAIL])
        print(f"  ✔ Available           : {n_avail:>4}")
        print(f"  ✗ Not accessible      : {n_fail:>4}")
        if n_blk:
            print(f"  ✗ Blocked (403)       : {n_blk:>4}")

    if res[ST_FAIL]:
        print()
        print("  Not found (first 20):")
        for item in res[ST_FAIL][:20]:
            print(f"    - {item}")
        if len(res[ST_FAIL]) > 20:
            print(f"    … and {len(res[ST_FAIL])-20} more (see report JSON)")

    out_root.mkdir(parents=True, exist_ok=True)
    rpt = out_root / "download_report.json"
    rpt.write_text(
        json.dumps({k: sorted(v) for k, v in res.items()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print()
    print(f"  Report saved  : {rpt}")
    print("━" * W)
    print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="download_tie_books.py",
        description="Download Tanzania TIE secondary school textbooks (Form 1–4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--forms", nargs="+", type=int, choices=[1,2,3,4],
                   default=[1,2,3,4], metavar="N",
                   help="Forms to download: 1 2 3 4  (default: all)")
    p.add_argument("--subjects", nargs="+", metavar="SUBJECT",
                   help="Subject names (default: all; use --list for names)")
    p.add_argument("--core-only", action="store_true",
                   help="Download the 9 core/compulsory subjects only")
    p.add_argument("--output", default="tanzania_textbooks", metavar="DIR",
                   help="Output root directory  (default: tanzania_textbooks)")
    p.add_argument("--check", action="store_true",
                   help="Availability scan only — do not write any files")
    p.add_argument("--delay", type=float, default=BOOK_DELAY, metavar="SEC",
                   help=f"Seconds between books  (default: {BOOK_DELAY})")
    p.add_argument("--selftest", action="store_true",
                   help="Validate script integrity and exit")
    p.add_argument("--list", action="store_true",
                   help="Print all subjects and exit")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Debug-level logging")
    return p


def main() -> None:
    args = _build_parser().parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.selftest:
        run_selftest()
        return

    if args.list:
        print(f"\n{'Subject':<26} Core   Forms")
        print("─" * 45)
        for subj in sorted(CATALOGUE):
            fms  = sorted(CATALOGUE[subj].keys())
            core = " ✔" if subj in CORE_SUBJECTS else "  "
            print(f"  {subj:<24}{core}    {fms[0]}–{fms[-1]}")
        print()
        return

    out_root = Path(args.output)

    # ── build subject list ───────────────────────────────────────────────────
    if args.subjects:
        lower = {k.lower(): k for k in CATALOGUE}
        selected = {}
        for s in args.subjects:
            key = lower.get(s.lower())
            if key:
                selected[key] = CATALOGUE[key]
            else:
                print(f"WARNING: Subject not recognised: '{s}'")
                print(f"         Use --list to see all available subject names.")
        if not selected:
            sys.exit("No valid subjects selected.")
    elif args.core_only:
        selected = {k: v for k, v in CATALOGUE.items() if k in CORE_SUBJECTS}
    else:
        selected = CATALOGUE

    total = sum(
        1 for forms in selected.values() for f in forms if f in args.forms
    )

    mode = "availability check" if args.check else "download"
    _print_banner(mode, args.forms, len(selected), total, out_root)

    results: dict[str, list[str]] = {
        ST_PDF:      [],
        ST_TEXT_PDF: [],
        ST_HTML:     [],
        ST_SKIP:     [],
        ST_FAIL:     [],
        ST_BLOCKED:  [],
    }

    idx = 0
    for subject, forms in selected.items():
        for form_num in sorted(forms.keys()):
            if form_num not in args.forms:
                continue
            idx += 1
            fw      = FORM_WORD[form_num]
            out_dir = out_root / f"Form_{fw}"
            log.info("[%3d/%d]  %s — Form %d", idx, total, subject, form_num)

            status = download_book(
                subject, form_num, forms[form_num],
                out_dir, check_only=args.check,
            )
            results[status].append(f"{subject} — Form {form_num}")

            if not args.check:
                time.sleep(args.delay)

    _print_summary(results, args.check, total, out_root)


if __name__ == "__main__":
    main()
