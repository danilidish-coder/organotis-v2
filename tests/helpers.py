import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def write(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def make_docx(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:body></w:document>')
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("word/document.xml", xml)
    return p


def make_xlsx(p: Path, sheet: str, strings: list[str]) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    wb = f'<workbook xmlns="{ns}"><sheets><sheet name="{escape(sheet)}" sheetId="1"/></sheets></workbook>'
    ss = f'<sst xmlns="{ns}">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/sharedStrings.xml", ss)
    return p


def make_pdf(p: Path, text: str) -> Path:
    """Ελάχιστο έγκυρο PDF με λατινικό κείμενο."""
    p.parent.mkdir(parents=True, exist_ok=True)
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    p.write_bytes(bytes(out))
    return p
