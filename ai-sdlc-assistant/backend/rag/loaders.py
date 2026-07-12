"""
backend/rag/loaders.py

File-type specific document loaders for the RAG ingestion pipeline.

Each loader reads a raw file and returns either text content or
unstructured elements, which the pipeline then passes to the chunker.

Loaders:
  load_slack_json            — Slack/Teams message JSON → list of message dicts
  load_pdf_with_pypdf        — PDF fallback (no unstructured) → plain text
  get_pdf_image_elements     — PDF OCR: extracts text from embedded images via
                               pypdf + pytesseract → list of NarrativeText elements

Why a separate module?
  Loaders are file-format concerns, not ingestion-flow concerns.
  Pipeline.py should not care HOW a PDF is read — only that it gets chunks.
  This also makes adding a new format simple: add a new loader function here.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_slack_json(filepath: Path) -> list[dict]:
    """
    Read a Slack mock JSON file.
    Format: list of {user, message, timestamp} objects.
    Returns empty list on any error.
    """
    import json
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("loaders: failed to read Slack JSON '%s'", filepath.name)
        return []


def load_pdf_with_pypdf(filepath: Path) -> str:
    """
    Fallback PDF text extraction using pypdf (always available in requirements).
    Used when unstructured[pdf] extras are not installed.

    Extracts text page-by-page and joins with double newlines.
    Returns empty string if extraction fails or file has no text layer.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.error("loaders: pypdf not available — cannot extract '%s'", filepath.name)
        return ""

    try:
        reader = PdfReader(str(filepath))
        page_texts = [
            p.extract_text()
            for p in reader.pages
            if p.extract_text() and p.extract_text().strip()
        ]
        if not page_texts:
            logger.warning(
                "loaders: pypdf extracted no text from '%s' — may be a scanned/image-only PDF",
                filepath.name,
            )
            return ""
        logger.info("loaders: pypdf extracted %d pages from '%s'", len(page_texts), filepath.name)
        return "\n\n".join(page_texts)
    except Exception:
        logger.exception("loaders: pypdf failed for '%s'", filepath.name)
        return ""


def extract_pdf_images(filepath: Path, images_dir: Path) -> list[dict]:
    """
    Extract embedded images from a PDF: save each to `images_dir` as PNG and OCR it.

    Returns a list of records, one per kept image:
        {image_id, image_path, ocr_text, page_number, width, height}
      - image_id    — first 16 hex of the image bytes' SHA256 (stable, dedupes copies)
      - image_path  — relative path "images/{id}.png" (served by the API in Phase II)
      - ocr_text    — text recognised inside the image ("" if OCR unavailable/empty)

    This is what makes images BOTH searchable (ocr_text → embedded chunk) and
    showable (image_path → returned to the UI). Icons/decorative images < 50px
    are skipped. Returns [] silently if pypdf/Pillow are missing.

    OCR uses Tesseract (installed in the Docker image). If the Tesseract binary is
    absent (e.g. local Windows without it), images are still saved and indexed by
    page/document — only the OCR text is empty.
    """
    try:
        import hashlib
        import io

        from pypdf import PdfReader
        from PIL import Image as PILImage
    except ImportError as e:
        logger.debug("loaders: image extraction skipped — missing dependency: %s", e)
        return []

    try:
        import pytesseract
        _ocr_available = True
    except ImportError:
        _ocr_available = False

    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    try:
        reader = PdfReader(str(filepath))
    except Exception:
        logger.exception("loaders: cannot open PDF for image extraction: %s", filepath.name)
        return []

    for page_idx, page in enumerate(reader.pages):
        page_num = page_idx + 1
        try:
            page_images = list(page.images)
        except Exception:
            continue

        for img_obj in page_images:
            try:
                data = img_obj.data
                if not data:
                    continue
                pil_img = PILImage.open(io.BytesIO(data))
                w, h = pil_img.size
                if w < 50 or h < 50:
                    continue  # skip icons / decorative images

                image_id  = hashlib.sha256(data).hexdigest()[:16]
                out_path  = images_dir / f"{image_id}.png"
                if not out_path.exists():
                    pil_img.convert("RGB").save(out_path, "PNG")

                ocr_text = ""
                if _ocr_available:
                    try:
                        ocr_text = pytesseract.image_to_string(
                            pil_img.convert("L"), config="--psm 6"
                        ).strip()
                    except pytesseract.TesseractNotFoundError:
                        _ocr_available = False  # stop retrying this run
                        logger.warning(
                            "loaders: Tesseract binary not found — images saved without OCR text. "
                            "It is installed in the Docker image; install locally to OCR outside Docker."
                        )
                    except Exception:
                        ocr_text = ""

                records.append({
                    "image_id":    image_id,
                    "image_path":  f"images/{out_path.name}",
                    "ocr_text":    ocr_text,
                    "page_number": page_num,
                    "width":       w,
                    "height":      h,
                })
            except Exception:
                continue  # one bad image never stops the page

    logger.info("loaders: extracted %d image(s) from '%s' (OCR=%s)",
                len(records), filepath.name, _ocr_available)
    return records


def _table_to_markdown(table: list[list]) -> str:
    """Convert a pdfplumber table (list of rows, each a list of cells) to markdown pipe format."""
    if not table:
        return ""
    rows = [[str(cell or "").strip().replace("\n", " ") for cell in row] for row in table]
    rows = [r for r in rows if any(c for c in r)]  # drop fully-empty rows
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    header = rows[0] + [""] * (width - len(rows[0]))
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in rows[1:]:
        padded = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(padded[:width]) + " |")
    return "\n".join(lines)


def load_pdf_tables(filepath: Path) -> list[tuple[int, str]]:
    """
    Extract tables from a PDF using pdfplumber (coordinate-based, not OCR).

    Returns list of (page_number, markdown_table_text) tuples.
    Returns [] if pdfplumber is not installed or no tables are found.

    Why pdfplumber over unstructured for tables?
    PDFs store table cells as positioned text fragments — no row/column concept
    in the format. unstructured guesses structure from text order and often garbles
    or skips table cells entirely. pdfplumber reads the actual coordinate grid and
    reconstructs rows/columns mathematically, giving exact cell text every time.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.debug("loaders: pdfplumber not installed — PDF table extraction unavailable")
        return []

    results: list[tuple[int, str]] = []
    try:
        with pdfplumber.open(str(filepath)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                page_num = page_idx + 1
                for table in page.extract_tables() or []:
                    md = _table_to_markdown(table)
                    if md:
                        results.append((page_num, md))
    except Exception:
        logger.exception("loaders: pdfplumber table extraction failed for '%s'", filepath.name)

    if results:
        logger.info("loaders: pdfplumber found %d table(s) in '%s'", len(results), filepath.name)
    return results


def get_pdf_image_elements(filepath: Path) -> list:
    """
    Extract text from image XObjects embedded in a PDF via pypdf + pytesseract OCR.

    Returns a list of NarrativeText elements (same type unstructured uses) so they
    slot naturally into chunk_from_elements() section grouping — each element carries
    the source page_number so it gets placed inside the right section.

    Requirements:
      - pytesseract Python package (in requirements.txt)
      - Pillow Python package (in requirements.txt)
      - Tesseract binary on PATH (Windows: https://github.com/UB-Mannheim/tesseract/wiki)

    Returns [] silently if any dependency is missing — caller continues without OCR.
    """
    try:
        from pypdf import PdfReader
        import pytesseract
        from PIL import Image as PILImage
        import io
        from unstructured.documents.elements import NarrativeText as _NarrText
        from unstructured.documents.elements import ElementMetadata as _ElemMeta
    except ImportError as e:
        logger.debug("loaders: image OCR skipped — missing dependency: %s", e)
        return []

    ocr_elements = []
    try:
        reader = PdfReader(str(filepath))
        for page_idx, page in enumerate(reader.pages):
            page_num = page_idx + 1
            try:
                page_img_list = list(page.images)
            except Exception:
                continue

            for img_obj in page_img_list:
                try:
                    img_data = img_obj.data
                    if not img_data:
                        continue
                    pil_img = PILImage.open(io.BytesIO(img_data)).convert("L")
                    w, h = pil_img.size
                    if w < 50 or h < 50:
                        continue  # skip icons and decorative images
                    ocr_text = pytesseract.image_to_string(pil_img, config="--psm 6").strip()
                    if len(ocr_text) > 30:
                        el = _NarrText(text=ocr_text)
                        el.metadata = _ElemMeta(page_number=page_num)
                        ocr_elements.append(el)
                except Exception:
                    continue  # single image failure never stops the page

    except pytesseract.TesseractNotFoundError:
        logger.warning(
            "loaders: Tesseract binary not found — image OCR skipped for '%s'. "
            "Install from https://github.com/UB-Mannheim/tesseract/wiki and add to PATH.",
            filepath.name,
        )
    except Exception:
        logger.exception("loaders: PDF image OCR failed for '%s'", filepath.name)

    return ocr_elements
