"""PDF acquisition, verbatim text blocks, and manual source review."""
import hashlib
import json
import urllib.request
from . import common
from .common import digest, save_json


def source_file(cfg, doc_id, url):
    import fitz
    base = common.ROOT / cfg["output_directory"] / "sources"
    cache_name = f"{doc_id}-{digest(url)}"
    path, meta = base / f"{cache_name}.pdf", base / f"{cache_name}.json"
    if path.exists():
        info = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        if info.get("url") != url or info.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"Cached PDF provenance mismatch: {path}; inspect this file before rerunning")
    else:
        if not url.startswith("https://"):
            # file: only supports local fixture testing, not remote downloads.
            if not url.startswith("file://"):
                raise ValueError("Document must be an HTTPS PDF URL")
        base.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TransplateClean/1.0)"})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                data = response.read()
        except Exception as exc:
            raise RuntimeError(f"Cannot download {doc_id}; check connection or URL: {exc}") from exc
        if not data.startswith(b"%PDF-"):
            raise ValueError(f"URL did not return a PDF: {doc_id}")
        path.write_bytes(data)
        save_json(meta, {"url": url, "sha256": hashlib.sha256(data).hexdigest()})
    with fitz.open(path) as pdf:
        if pdf.page_count == 0 or pdf.needs_pass:
            raise ValueError(f"PDF is empty or password-protected: {path}")
    return path


def extract_blocks(doc_id, path):
    import fitz
    blocks = []
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with fitz.open(path) as pdf:
        for page_no, page in enumerate(pdf, 1):
            # Preserve full source PDF separately. Source IDs are geometric text blocks.
            pieces = sorted(page.get_text("blocks"), key=lambda b: (round(b[1], 1), round(b[0], 1)))
            index = 0
            for b in pieces:
                if b[6] != 0 or not b[4].strip():
                    continue
                index += 1
                text = b[4]  # Keep PyMuPDF's original characters and line breaks, without whitespace rewriting.
                # Avoid footer furniture but keep it inspectable in blocks.csv.
                eligible = (b[1] < page.rect.height * 0.92 and text.strip() not in ("•", "·")
                            and not " ".join(text.split()).startswith("O E C D D I G I T A L F O R S M E S"))
                blocks.append({"document_id": doc_id, "pdf_sha256": sha, "page": page_no,
                               "unit_id": f"{doc_id}:p{page_no}:b{index}", "english": text,
                               "eligible": eligible, "bbox": [round(x, 1) for x in b[:4]]})
    # The France PDF contains a small amount of banner text baked into images.
    # Preserve these visually verified words in the source archive, not in experiment inputs.
    additions_path = common.ROOT / "data" / "france_graphic_text.json"
    if doc_id == "france" and additions_path.exists():
        additions = json.loads(additions_path.read_text(encoding="utf-8"))
        if additions["pdf_sha256"] == sha:
            for page, entries in additions["pages"].items():
                for index, entry in enumerate(entries, 1):
                    blocks.append({"document_id": doc_id, "pdf_sha256": sha, "page": int(page),
                                   "unit_id": f"{doc_id}:p{page}:graphic{index}",
                                   "english": entry["text"], "eligible": False, "bbox": entry["bbox"]})
            blocks.sort(key=lambda b: (b["page"], b["bbox"][1], b["bbox"][0]))
    if not any(b["eligible"] for b in blocks):
        raise ValueError(f"No eligible text blocks extracted: {doc_id}")
    return blocks


def review_extraction(cfg, document_id):
    """Export a visual/manual comparison; no programmatic extractor proves reading order."""
    import fitz
    path = source_file(cfg, document_id, cfg["documents"][document_id])
    blocks = extract_blocks(document_id, path)
    review_dir = common.ROOT / cfg["output_directory"] / "extraction_review" / document_id
    review_dir.mkdir(parents=True, exist_ok=True)
    report = ["MANUAL SOURCE CHECK REQUIRED: Compare each numbered block against its rendered PDF page.",
              "Check COMPLETE text, exact spelling/punctuation/math, and READING ORDER.",
              "Vector-drawn or image-based words may not be available to PyMuPDF; flag missing text.",
              "'Included' controls experimental input, NOT whether the text was extracted.",
              "graphic IDs are visual transcriptions from data/france_graphic_text.json (not PyMuPDF).", ""]
    with fitz.open(path) as pdf:
        for n, page in enumerate(pdf, 1):
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(str(review_dir / f"page-{n}.png"))
            report.append(f"\n======== PAGE {n} ========\n")
            for b in (b for b in blocks if b["page"] == n):
                origin = "visual transcription" if ":graphic" in b["unit_id"] else "PyMuPDF"
                report.append(f"[{b['unit_id']}] {'Included' if b['eligible'] else 'Excluded'} ({origin}) bbox={b['bbox']}\n{b['english']}\n")
    result = review_dir / "extracted-in-reading-order.txt"
    result.write_text("\n".join(report), encoding="utf-8")
    print(f"EXTRACTION NOT YET VERIFIED: compare {result} against page-*.png in {review_dir}")
    print("Do not assume completeness or reading order until the visual comparison is complete.")


