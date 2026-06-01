#!/usr/bin/env python3
"""
Tanzania TIE Textbooks Downloader — Form 1–4, All Subjects
===========================================================
Downloads all official Tanzania Institute of Education (TIE) textbooks
for Ordinary Level (O-Level) secondary school, Forms 1–4.

IMPORTANT: Run this script from your LOCAL machine (not a cloud server).
The Tanzanian government servers block cloud/data-centre IP ranges.

Requirements:
    pip install requests beautifulsoup4 lxml fpdf2 tqdm

Optional (better PDF quality — install at least one):
    pip install weasyprint          # best quality HTML→PDF
    pip install Pillow              # needed by fpdf2 for image embedding

Usage:
    python download_tie_books.py              # download everything
    python download_tie_books.py --check      # check availability only
    python download_tie_books.py --forms 1 2  # specific forms only
    python download_tie_books.py --subjects "Biology" "Chemistry"
    python download_tie_books.py --output /path/to/save
"""

import os
import re
import sys
import json
import time
import logging
import argparse
import warnings
from pathlib import Path
from urllib.parse import quote, urljoin
from typing import Optional

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

warnings.filterwarnings("ignore")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# BASE URLS
# ─────────────────────────────────────────────────────────────────────────────
TIE_BASE = "https://ol.tie.go.tz/uploaded_files/books"
TIE_ALT  = "https://tiebooks.tie.go.tz"
MAKTABA  = "https://maktaba.tetea.org"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,sw;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
})

# ─────────────────────────────────────────────────────────────────────────────
# COMPLETE TEXTBOOK CATALOGUE
#
# Each entry:  subject_name → { form_number → [list of base-path variants] }
#
# The script tries every path variant in order and uses the first one that
# returns usable content (HTML pages or a direct PDF).
# ─────────────────────────────────────────────────────────────────────────────
CATALOGUE = {

    # ── CORE / COMPULSORY SUBJECTS ──────────────────────────────────────────
    "Basic Mathematics": {
        1: ["//secondary/Form_One/Mathematics_Form_One",
            "/secondary/Form_One/Mathematics_Form_One"],
        2: ["//secondary/Form_Two/Mathematics_Form_Two",
            "/secondary/Form_Two/Mathematics_Form_Two"],
        3: ["//secondary/Form_Three/Mathematics_Form_Three",
            "/secondary/Form_Three/Mathematics/2025",
            "//secondary/Form_Three/Mathematics"],
        4: ["//secondary/Form_Four/Mathematics_Form_Four",
            "/secondary/Form_Four/Mathematics/2025",
            "//secondary/Form_Four/Mathematics"],
    },

    "English Language": {
        1: ["//secondary/Form_One/English_Form_One",
            "/secondary/Form_One/English_Form_One"],
        2: ["//secondary/Form_Two/English_Form_Two",
            "/secondary/Form_Two/English_Form_Two"],
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

    # ── OPTIONAL SUBJECTS ────────────────────────────────────────────────────
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

FORM_WORD = {1: "One", 2: "Two", 3: "Three", 4: "Four"}


# ─────────────────────────────────────────────────────────────────────────────
# HTTP HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _get(url: str, stream: bool = False, retries: int = 3) -> Optional[requests.Response]:
    """GET with retry/back-off; returns None on persistent failure."""
    for attempt in range(retries):
        try:
            resp = SESSION.get(url, timeout=45, stream=stream, allow_redirects=True)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 404):
                return None          # permanent failure
        except (requests.ConnectionError, requests.Timeout) as exc:
            wait = 2 ** attempt
            log.debug("Retry %d/%d for %s (error: %s) — waiting %ds",
                      attempt + 1, retries, url, exc, wait)
            time.sleep(wait)
    return None


def _is_pdf(content: bytes) -> bool:
    return content[:4] == b"%PDF"


# ─────────────────────────────────────────────────────────────────────────────
# PDF CONVERSION
# ─────────────────────────────────────────────────────────────────────────────

def _try_weasyprint(html: str, output_path: Path) -> bool:
    """Attempt HTML→PDF via WeasyPrint (preserves formatting and images)."""
    try:
        import weasyprint  # type: ignore
        weasyprint.HTML(string=html, base_url=TIE_BASE).write_pdf(str(output_path))
        return True
    except ImportError:
        return False
    except Exception as exc:
        log.debug("WeasyPrint failed: %s", exc)
        return False


def _pages_to_pdf_fpdf(pages: list, output_path: Path, title: str) -> bool:
    """Fallback: extract text from HTML pages and write a plain-text PDF."""
    try:
        from fpdf import FPDF  # type: ignore
    except ImportError:
        log.warning("fpdf2 not installed — cannot convert to PDF. Run: pip install fpdf2")
        return False

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    for page_num, html in tqdm(pages, desc=f"  Building PDF: {title}", leave=False):
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        body = soup.find("body") or soup
        text = body.get_text(separator="\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            continue

        pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.multi_cell(0, 6, f"— Page {page_num} —", align="C")
        pdf.set_font("Helvetica", size=9)

        for line in text.split("\n"):
            if not line.strip():
                pdf.ln(2)
                continue
            try:
                safe = line.encode("latin-1", errors="replace").decode("latin-1")
                pdf.multi_cell(0, 4.5, safe)
            except Exception:
                pass

    try:
        pdf.output(str(output_path))
        return True
    except Exception as exc:
        log.error("fpdf2 output failed: %s", exc)
        return False


def _save_html_archive(pages: list, output_path: Path, title: str) -> bool:
    """Last resort: save pages as a single self-contained HTML file."""
    html_path = output_path.with_suffix(".html")
    combined = [f"<h1>{title}</h1>"]
    for page_num, html in pages:
        soup = BeautifulSoup(html, "lxml")
        body = soup.find("body")
        if body:
            combined.append(f"<!-- ==== PAGE {page_num} ==== -->")
            combined.append(str(body))
    try:
        html_path.write_text("\n".join(combined), encoding="utf-8")
        log.info("  Saved HTML archive: %s", html_path.name)
        return True
    except Exception as exc:
        log.error("HTML archive failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# DOWNLOAD STRATEGIES
# ─────────────────────────────────────────────────────────────────────────────

def _strategy_direct_pdf(base_path: str, subject: str, form: int) -> Optional[bytes]:
    """Strategy 1 — look for a pre-built PDF at predictable paths."""
    fw = FORM_WORD[form]
    slug = subject.replace(" ", "_").replace("-", "_")

    candidates = [
        f"{slug}_Form_{fw}.pdf",
        f"{slug}.pdf",
        f"{subject.replace(' ', '_')}_Form_{fw}.pdf",
        f"Form_{fw}_{slug}.pdf",
        f"{slug}_Students_Book_Form_{fw}.pdf",
        f"{slug}_Student_Book_Form_{fw}.pdf",
        f"TIE_{slug}_Form_{fw}.pdf",
    ]

    for name in candidates:
        url = f"{TIE_BASE}{base_path}/{quote(name)}"
        resp = _get(url)
        if resp and _is_pdf(resp.content):
            return resp.content
    return None


def _strategy_html_pages(base_path: str) -> list:
    """Strategy 2 — download the paginated HTML book reader pages."""
    pages = []
    for page_num in range(1, 1000):
        url = f"{TIE_BASE}{base_path}/files/basic-html/page{page_num}.html"
        resp = _get(url)
        if resp is None:
            break
        pages.append((page_num, resp.text))
        time.sleep(0.3)

    # Also try alternate HTML entry points (page index as index.html)
    if not pages:
        url = f"{TIE_BASE}{base_path}/files/basic-html/index.html"
        resp = _get(url)
        if resp:
            # Parse index to find all pages
            soup = BeautifulSoup(resp.text, "lxml")
            links = [a["href"] for a in soup.find_all("a", href=re.compile(r"page\d+\.html"))]
            for href in sorted(set(links), key=lambda x: int(re.search(r"\d+", x).group())):
                page_url = f"{TIE_BASE}{base_path}/files/basic-html/{href}"
                r = _get(page_url)
                if r:
                    m = re.search(r"page(\d+)", href)
                    n = int(m.group(1)) if m else len(pages) + 1
                    pages.append((n, r.text))
                    time.sleep(0.3)

    return pages


def _strategy_top_html(base_path: str) -> Optional[str]:
    """Strategy 3 — the book may be a single large HTML file."""
    fw_map = {"Form_One": "One", "Form_Two": "Two", "Form_Three": "Three", "Form_Four": "Four"}
    for entry in base_path.split("/"):
        if entry in fw_map:
            break

    # Try common single-file HTML patterns
    segment = base_path.rstrip("/").split("/")[-1]
    for name in [f"{segment}.html", "index.html", "book.html"]:
        url = f"{TIE_BASE}{base_path}/{quote(name)}"
        resp = _get(url)
        if resp and len(resp.text) > 2000:
            return resp.text
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN DOWNLOAD FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def download_book(subject: str, form: int, paths: list, out_dir: Path,
                  check_only: bool = False) -> str:
    """
    Download one textbook.

    Returns one of: "ok_pdf" | "ok_html" | "ok_text_pdf" | "skip" | "fail"
    """
    fw = FORM_WORD[form]
    safe_subj = subject.replace(" ", "_").replace("/", "_")
    pdf_path  = out_dir / f"{safe_subj}_Form_{fw}.pdf"
    html_path = pdf_path.with_suffix(".html")

    if pdf_path.exists():
        log.info("  ↷ Already exists: %s", pdf_path.name)
        return "skip"
    if html_path.exists():
        log.info("  ↷ Already exists (HTML): %s", html_path.name)
        return "skip"

    label = f"{subject} — Form {form}"

    for base_path in paths:
        log.debug("  Trying path: %s", base_path)

        if check_only:
            # Just probe page1.html or a PDF candidate
            probe = _get(f"{TIE_BASE}{base_path}/files/basic-html/page1.html")
            if probe:
                log.info("  ✔ Available (HTML reader): %s", label)
                return "ok_html"
            probe = _get(f"{TIE_BASE}{base_path}/files/basic-html/index.html")
            if probe:
                log.info("  ✔ Available (index): %s", label)
                return "ok_html"
            continue

        # ── Strategy 1: direct PDF ──────────────────────────────────────
        pdf_bytes = _strategy_direct_pdf(base_path, subject, form)
        if pdf_bytes:
            out_dir.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(pdf_bytes)
            size_kb = len(pdf_bytes) // 1024
            log.info("  ✔ PDF direct  (%d KB): %s", size_kb, pdf_path.name)
            return "ok_pdf"

        # ── Strategy 2: paginated HTML reader ──────────────────────────
        pages = _strategy_html_pages(base_path)
        if pages:
            out_dir.mkdir(parents=True, exist_ok=True)
            log.info("  Fetched %d HTML pages for %s", len(pages), label)

            # Try WeasyPrint first (best fidelity)
            combined_html = "\n".join(
                f"<!-- PAGE {n} -->\n{html}" for n, html in pages
            )
            if _try_weasyprint(combined_html, pdf_path):
                log.info("  ✔ PDF via WeasyPrint: %s", pdf_path.name)
                return "ok_pdf"

            # Fallback: fpdf2 text extraction
            if _pages_to_pdf_fpdf(pages, pdf_path, label):
                log.info("  ✔ PDF via fpdf2 (text only): %s", pdf_path.name)
                return "ok_text_pdf"

            # Last resort: HTML archive
            if _save_html_archive(pages, pdf_path, label):
                return "ok_html"

        # ── Strategy 3: single-page HTML ───────────────────────────────
        single_html = _strategy_top_html(base_path)
        if single_html:
            out_dir.mkdir(parents=True, exist_ok=True)
            pages_single = [(1, single_html)]
            if _try_weasyprint(single_html, pdf_path):
                log.info("  ✔ PDF (single-HTML → WeasyPrint): %s", pdf_path.name)
                return "ok_pdf"
            if _pages_to_pdf_fpdf(pages_single, pdf_path, label):
                log.info("  ✔ PDF (single-HTML → fpdf2): %s", pdf_path.name)
                return "ok_text_pdf"

    log.warning("  ✗ Not found: %s", label)
    return "fail"


# ─────────────────────────────────────────────────────────────────────────────
# CLI & MAIN
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Download all Tanzania TIE textbooks for Form 1–4."
    )
    p.add_argument("--forms", nargs="+", type=int, choices=[1, 2, 3, 4],
                   default=[1, 2, 3, 4], metavar="N",
                   help="Which forms to download (default: 1 2 3 4)")
    p.add_argument("--subjects", nargs="+", metavar="SUBJECT",
                   help="Specific subject names (default: all)")
    p.add_argument("--output", default="tanzania_textbooks",
                   help="Output directory (default: tanzania_textbooks)")
    p.add_argument("--check", action="store_true",
                   help="Check availability only — do not download")
    p.add_argument("--delay", type=float, default=1.0,
                   help="Seconds to wait between books (default: 1.0)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Enable debug logging")
    return p.parse_args()


def main():
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    out_root = Path(args.output)

    # Filter catalogue
    subjects_to_run = (
        {k: v for k, v in CATALOGUE.items() if k in args.subjects}
        if args.subjects else CATALOGUE
    )
    if not subjects_to_run:
        log.error("No matching subjects found. Available: %s",
                  ", ".join(CATALOGUE))
        sys.exit(1)

    total = sum(1 for subj, forms in subjects_to_run.items()
                for f in forms if f in args.forms)

    print()
    print("━" * 65)
    print("  Tanzania TIE Textbooks Downloader — Form 1–4")
    print("━" * 65)
    print(f"  Mode     : {'availability check' if args.check else 'download'}")
    print(f"  Forms    : {args.forms}")
    print(f"  Subjects : {len(subjects_to_run)}")
    print(f"  Total    : {total} books")
    print(f"  Output   : {out_root.absolute()}")
    print("━" * 65)
    print()

    results = {"ok_pdf": [], "ok_html": [], "ok_text_pdf": [], "skip": [], "fail": []}

    done = 0
    for subject, forms in subjects_to_run.items():
        for form_num, paths in forms.items():
            if form_num not in args.forms:
                continue
            done += 1
            fw = FORM_WORD[form_num]
            out_dir = out_root / f"Form_{fw}"
            label = f"[{done:>3}/{total}] {subject} — Form {form_num}"
            log.info(label)

            status = download_book(
                subject, form_num, paths, out_dir,
                check_only=args.check
            )
            results[status].append(f"{subject} — Form {form_num}")
            time.sleep(args.delay)

    # ── Summary ─────────────────────────────────────────────────────────────
    print()
    print("━" * 65)
    print("  SUMMARY")
    print("━" * 65)
    if not args.check:
        print(f"  ✔ PDF (direct)     : {len(results['ok_pdf'])}")
        print(f"  ✔ PDF (text only)  : {len(results['ok_text_pdf'])}")
        print(f"  ✔ HTML archive     : {len(results['ok_html'])}")
        print(f"  ↷ Skipped (exists) : {len(results['skip'])}")
        print(f"  ✗ Not found        : {len(results['fail'])}")
    else:
        available = len(results["ok_pdf"]) + len(results["ok_html"]) + len(results["ok_text_pdf"])
        print(f"  ✔ Available        : {available}")
        print(f"  ✗ Not accessible   : {len(results['fail'])}")

    if results["fail"]:
        print()
        print("  Not found / failed:")
        for item in results["fail"]:
            print(f"    - {item}")

    # Save report
    report_path = out_root / "download_report.json"
    out_root.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print()
    print(f"  Report saved: {report_path}")
    print("━" * 65)
    print()


if __name__ == "__main__":
    main()
