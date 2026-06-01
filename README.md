# Tanzania TIE Textbooks Downloader — Form 1–4

Downloads all official **Tanzania Institute of Education (TIE)** textbooks for
Ordinary Level (O-Level) secondary school, **Forms 1–4**, covering every subject.

## Subjects covered

| Type | Subjects |
|------|----------|
| **Core (all students)** | Basic Mathematics, English Language, Kiswahili, Biology, Chemistry, Physics, History, Geography, Civics |
| **Optional** | Agriculture, Book-keeping, Commerce, Computer Studies, Fine Arts, Home Economics, French, Music, Bible Knowledge, Islamic Knowledge, Physical Education |

Total: up to **80 books** (20 subjects × 4 forms).

---

## Requirements

- Python 3.8+
- Run from your **local machine** (Tanzanian government servers block cloud/data-centre IPs)

```bash
pip install -r requirements.txt
```

For best PDF quality (preserves formatting + images), also install WeasyPrint:

```bash
pip install weasyprint
```

---

## Quick Start

```bash
# Download everything (all forms, all subjects)
python download_tie_books.py

# Check what's available without downloading
python download_tie_books.py --check

# Download specific forms only
python download_tie_books.py --forms 1 2

# Download specific subjects only
python download_tie_books.py --subjects "Biology" "Chemistry" "Physics"

# Choose where to save
python download_tie_books.py --output /path/to/my/books

# Combine options
python download_tie_books.py --forms 3 4 --subjects "Basic Mathematics" "English Language"
```

---

## Output structure

```
tanzania_textbooks/
├── Form_One/
│   ├── Basic_Mathematics_Form_One.pdf
│   ├── Biology_Form_One.pdf
│   ├── Chemistry_Form_One.pdf
│   └── ...
├── Form_Two/
│   └── ...
├── Form_Three/
│   └── ...
├── Form_Four/
│   └── ...
└── download_report.json
```

---

## How it works

The script tries three download strategies for each book, in order:

1. **Direct PDF** — looks for a pre-built PDF at common URL patterns on `ol.tie.go.tz`
2. **HTML reader pages** — downloads all paginated HTML pages from the TIE online reader,
   then converts them to PDF using WeasyPrint (best quality) or fpdf2 (text-only fallback)
3. **Single HTML file** — handles books published as one large HTML file

If a book cannot be found, it is logged in `download_report.json` under `"fail"`.

---

## Source

All books are published by the **Tanzania Institute of Education (TIE)**:
- Online library: https://ol.tie.go.tz
- Official site: https://www.tie.go.tz
- Supplementary study aids: https://maktaba.tetea.org (by TETEA)

---

## Notes

- The script respects the server with a 1-second delay between each book request
  (configurable with `--delay`).
- Books already downloaded are skipped on subsequent runs.
- The TIE library marks books "FOR ONLINE READING ONLY"; downloading is intended
  for personal educational use only.
