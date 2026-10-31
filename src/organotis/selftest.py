"""Αυτοέλεγχος: τρέχει ολόκληρο τον κύκλο (σάρωση, πρόταση, μετακίνηση, αναίρεση) σε προσωρινό φάκελο.

Χρησιμεύει για να επιβεβαιωθεί ότι το πακεταρισμένο EXE δουλεύει (ότι μπήκαν μέσα η
ανάγνωση PDF, το γραφικό περιβάλλον κ.λπ.). Δεν αγγίζει κανέναν φάκελο του χρήστη.
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from . import __version__
from .approval import PreviewModel
from .config import Config
from .extract import extract
from .mover import execute
from .planner import build_plan
from .scanner import build_report, scan
from .undo import undo


def _docx(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", xml)


def _pdf(p: Path, text: str) -> None:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    p.write_bytes(bytes(out))


def _snapshot(root: Path) -> list:
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None)
                  for p in root.rglob("*"))


def run(require_gui: bool = False) -> tuple[bool, list[str]]:
    """Επιστρέφει (όλα καλά;, γραμμές αναφοράς)."""
    lines: list[str] = [f"Οργανωτής αρχείων {__version__}: αυτοέλεγχος"]
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        lines.append(f"{'ΕΝΤΑΞΕΙ' if passed else 'ΑΠΟΤΥΧΙΑ'}  {name}" + (f"  ({detail})" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="organotis-selftest-") as tmp:
        root = Path(tmp) / "Δοκιμή"
        root.mkdir()
        _docx(root / "scan1.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")
        _pdf(root / "stmt.pdf", "IBAN statement 2022 2022")
        (root / "a.bin").write_bytes(b"dupdup")
        (root / "old").mkdir()
        (root / "old" / "b.bin").write_bytes(b"dupdup")
        (root / "IMG_20230514_1.jpg").write_bytes(b"jpg")
        (root / "setup.exe").write_bytes(b"MZ-not-executed")
        before = _snapshot(root)
        cfg = Config()

        txt = extract(root / "stmt.pdf", "pdf", (root / "stmt.pdf").stat().st_size)
        check("ανάγνωση PDF", "IBAN" in txt.text, txt.note)
        txt = extract(root / "scan1.docx", "word", (root / "scan1.docx").stat().st_size)
        check("ανάγνωση Word", "Εκκαθαριστικό" in txt.text, txt.note)

        res = scan(root, cfg)
        report = build_report(res)
        check("σάρωση και διπλότυπα", report.total == 6 and len(report.duplicate_groups) == 1,
              f"{report.total} αρχεία")
        plan = build_plan(res, report, cfg)
        model = PreviewModel(plan)
        check("πρόταση δομής με λόγους", len(model.rows) == 5 and all(r.proposal.reason for r in model.rows),
              f"{len(model.rows)} προτάσεις")
        run_ = execute(model.approve(), Path(tmp) / "manifests")
        check("μετακίνηση με manifest", run_.moved == 5 and not run_.skipped and _snapshot(root) != before,
              f"{run_.moved} μετακινήσεις")
        res_u = undo(run_.manifest_path)
        check("αναίρεση από το manifest", res_u.restored == 5 and _snapshot(root) == before,
              f"{res_u.restored} επιστροφές")

    try:
        import tkinter
        v = tkinter.TkVersion
        try:
            r = tkinter.Tk()
            r.withdraw()
            r.destroy()
            check("γραφικό περιβάλλον (tkinter)", True, f"Tk {v}")
        except Exception as exc:
            check("γραφικό περιβάλλον (tkinter)", not require_gui, f"δεν άνοιξε παράθυρο: {exc}")
    except ImportError as exc:
        check("γραφικό περιβάλλον (tkinter)", not require_gui, f"λείπει: {exc}")

    lines.append("ΑΠΟΤΕΛΕΣΜΑ: " + ("όλα εντάξει" if ok else "υπάρχουν αποτυχίες"))
    return ok, lines
