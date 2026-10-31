"""Ανάγνωση κειμένου από Word (.docx), PDF και Excel (.xlsx) μόνο για πρόταση.

Δεν εκτελεί τίποτα (ούτε μακροεντολές): τα .docx/.xlsx είναι αρχεία zip με XML που
διαβάζονται ως κείμενο. Υπάρχουν όρια μεγέθους ώστε ένα κακό αρχείο να μη
γεμίζει τη μνήμη. Αν κάτι δεν διαβάζεται, επιστρέφεται λόγος σε απλά ελληνικά.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 50 * 1024 * 1024      # μεγαλύτερα αρχεία: μόνο όνομα
MAX_XML_BYTES = 5 * 1024 * 1024        # όριο αποσυμπιεσμένου XML ανά μέρος
MAX_TEXT_CHARS = 30_000                # όριο κειμένου που κρατιέται
MAX_PDF_PAGES = 5


@dataclass(frozen=True)
class Extracted:
    text: str = ""
    note: str = ""  # γιατί δεν διαβάστηκε (κενό = εντάξει)


def _read_part(z: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = z.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        raise ValueError("πολύ μεγάλο περιεχόμενο")
    return z.read(info)


def _xml_text(data: bytes, tags: tuple[str, ...]) -> str:
    # Το iterparse δεν επιλύει εξωτερικές οντότητες· παίρνουμε μόνο τα κείμενα.
    out: list[str] = []
    for _, el in ET.iterparse(io.BytesIO(data)):
        local = el.tag.rsplit("}", 1)[-1]
        if local in tags and el.text:
            out.append(el.text)
        el.clear()
    return " ".join(out)


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        data = _read_part(z, "word/document.xml")
        if data is None:
            raise ValueError("δεν είναι έγκυρο έγγραφο Word")
        return _xml_text(data, ("t",))


def _xlsx(path: Path) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(path) as z:
        wb = _read_part(z, "xl/workbook.xml")
        if wb is None:
            raise ValueError("δεν είναι έγκυρο αρχείο Excel")
        for _, el in ET.iterparse(io.BytesIO(wb)):
            if el.tag.rsplit("}", 1)[-1] == "sheet" and el.get("name"):
                parts.append(el.get("name", ""))
        ss = _read_part(z, "xl/sharedStrings.xml")
        if ss is not None:
            parts.append(_xml_text(ss, ("t",)))
    return " ".join(parts)


def _pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValueError("λείπει η βιβλιοθήκη ανάγνωσης PDF") from exc
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)  # χωρίς μηνύματα στην οθόνη
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        raise ValueError("το PDF είναι κρυπτογραφημένο")
    chunks: list[str] = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        chunks.append(page.extract_text() or "")
        if sum(map(len, chunks)) > MAX_TEXT_CHARS:
            break
    return "\n".join(chunks)


def extract(path: Path, kind: str, size: int) -> Extracted:
    """Κείμενο για πρόταση. Ποτέ δεν σηκώνει εξαίρεση."""
    ext = path.suffix.lower()
    if kind not in ("word", "pdf", "excel"):
        return Extracted()
    if ext in (".doc", ".xls"):
        return Extracted(note=f"παλιά μορφή ({ext}): διαβάζεται μόνο το όνομα")
    if size > MAX_FILE_BYTES:
        return Extracted(note="πολύ μεγάλο αρχείο: διαβάζεται μόνο το όνομα")
    try:
        if ext == ".docx":
            text = _docx(path)
        elif ext == ".xlsx":
            text = _xlsx(path)
        elif ext == ".pdf":
            text = _pdf(path)
        else:
            return Extracted()
    except Exception as exc:  # κλειδωμένο, χαλασμένο, κρυπτογραφημένο, κ.λπ.
        if isinstance(exc, ValueError) and type(exc) is ValueError:
            reason = str(exc)  # δικά μας μηνύματα, ήδη σε απλά ελληνικά
        elif isinstance(exc, OSError):
            reason = f"δεν διαβάστηκε ({exc.strerror or 'σφάλμα'})"
        else:
            reason = "το αρχείο είναι χαλασμένο ή δεν είναι έγκυρης μορφής"
        return Extracted(note=f"δεν διαβάστηκε το περιεχόμενο ({reason}): διαβάζεται μόνο το όνομα")
    text = re.sub(r"\s+", " ", text).strip()[:MAX_TEXT_CHARS]
    if not text:
        return Extracted(note="δεν βρέθηκε κείμενο (π.χ. σκαναρισμένο): διαβάζεται μόνο το όνομα")
    return Extracted(text=text)
