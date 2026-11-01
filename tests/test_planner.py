import os
import stat
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

from organotis import config as cfgmod
from organotis.cli import main
from organotis.config import Config
from organotis.extract import MAX_XML_BYTES, extract
from organotis.formatting import format_plan
from organotis.planner import KEEP, MOVE, REVIEW, build_plan, find_topic, find_year, normalize
from organotis.scanner import build_report, scan

from helpers import make_docx, make_pdf, make_xlsx, write

TODAY = date(2026, 10, 31)


def plan_for(root: Path, cfg: Config | None = None, **kw):
    cfg = cfg or Config()
    res = scan(root, cfg)
    return build_plan(res, build_report(res), cfg, today=TODAY, **kw)


NO_SAME = ("duplicates", "general")  # χωρίς την εργασία «same_names»


def prop(plan, name):
    return next(p for p in plan.proposals if p.entry.path.name == name)


def snapshot(root: Path):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in root.rglob("*"))


# ---------- κανόνες θέματος / έτους ----------

def test_normalize_ignores_accents_case_final_sigma():
    assert normalize("ΔΕΗ Λογαριασμός") == normalize("δεη λογαριασμος") == "δεη λογαριασμοσ"


def test_topic_matches_with_accents_and_reports_where():
    topic, why = find_topic(Config(), "Εκκαθαριστικό 2023", "ΑΦΜ 123")
    assert topic == "Φορολογικά"
    assert "«εκκαθαριστικο» (στο όνομα)" in why and "«αφμ» (στο κείμενο)" in why


def test_topic_needs_whole_word():
    assert find_topic(Config(), "μεδεηση", "")[0] is None  # «δεη» μέσα σε άλλη λέξη


def test_topic_tie_is_ambiguous_and_no_proposal():
    topic, why = find_topic(Config(), "τράπεζα ασφάλιση", "")
    assert topic is None and "ισοβαθμία" in why


def test_topic_most_keywords_wins():
    topic, _ = find_topic(Config(), "", "ΔΕΗ λογαριασμός ρεύμα και μία τράπεζα")
    assert topic == "Λογαριασμοί"


def test_year_from_name_over_content():
    y, why = find_year("Δήλωση 2023", "2019 2019 2019", 2027)
    assert y == 2023 and "όνομα" in why


def test_year_most_frequent_in_content():
    y, why = find_year("έγγραφο", "2022 2023 2023 2023", 2027)
    assert y == 2023 and "3 φορές" in why


def test_year_tie_and_out_of_range_give_none():
    assert find_year("x", "2022 2023", 2027)[0] is None
    assert find_year("x", "1850 2099 12345", 2027)[0] is None
    assert find_year("a 2020 b 2021", "", 2027)[0] is None


# ---------- ανάγνωση περιεχομένου ----------

def test_extract_docx_xlsx_pdf(tmp_path):
    d = make_docx(tmp_path / "a.docx", "Εκκαθαριστικό σημείωμα 2023")
    x = make_xlsx(tmp_path / "b.xlsx", "Λογαριασμοί", ["ΔΕΗ", "ρεύμα"])
    f = make_pdf(tmp_path / "c.pdf", "IBAN statement 2022")
    assert "Εκκαθαριστικό" in extract(d, "word", d.stat().st_size).text
    xt = extract(x, "excel", x.stat().st_size).text
    assert "Λογαριασμοί" in xt and "ΔΕΗ" in xt
    assert "IBAN" in extract(f, "pdf", f.stat().st_size).text


def test_extract_never_raises_on_bad_files(tmp_path):
    bad = write(tmp_path / "bad.docx", b"not a zip")
    r = extract(bad, "word", bad.stat().st_size)
    assert r.text == "" and "διαβάζεται μόνο το όνομα" in r.note
    badpdf = write(tmp_path / "bad.pdf", b"garbage")
    assert extract(badpdf, "pdf", 7).note
    assert extract(tmp_path / "missing.pdf", "pdf", 1).note


def test_extract_legacy_formats_name_only(tmp_path):
    old = write(tmp_path / "old.doc", b"\xd0\xcf")
    r = extract(old, "word", 2)
    assert r.text == "" and ".doc" in r.note


def test_extract_xml_size_guard(tmp_path):
    p = tmp_path / "big.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"<a>" + b" " * (MAX_XML_BYTES + 10) + b"</a>")
    r = extract(p, "word", p.stat().st_size)
    assert r.text == "" and r.note


# ---------- πρόταση δομής ----------

def test_document_goes_to_topic_and_year_with_visible_reason(tmp_path):
    make_docx(tmp_path / "scan1.docx", "Εκκαθαριστικό σημείωμα ΑΦΜ 2023 έτος 2023")
    plan = plan_for(tmp_path)
    p = prop(plan, "scan1.docx")
    assert p.action == MOVE
    assert p.dest == tmp_path / "Έγγραφα" / "Φορολογικά" / "2023" / "scan1.docx"
    assert "εκκαθαριστικο" in p.reason and "2023" in p.reason and "Έγγραφα/Φορολογικά/2023" in p.reason


def test_pdf_content_used(tmp_path):
    make_pdf(tmp_path / "doc.pdf", "IBAN statement 2022 2022")
    p = prop(plan_for(tmp_path), "doc.pdf")
    assert p.action == MOVE and p.dest.parent == tmp_path / "Έγγραφα" / "Τράπεζα" / "2022"


def test_topic_without_year_has_no_year_folder(tmp_path):
    make_xlsx(tmp_path / "k.xlsx", "Λογαριασμοί", ["ΔΕΗ"])
    p = prop(plan_for(tmp_path), "k.xlsx")
    assert p.dest.parent == tmp_path / "Έγγραφα" / "Λογαριασμοί"
    assert "χωρίς έτος" in p.reason


def test_no_topic_stays_with_reason(tmp_path):
    make_docx(tmp_path / "random.docx", "κάτι εντελώς άσχετο 2023")
    p = prop(plan_for(tmp_path), "random.docx")
    assert p.action == KEEP and p.dest is None and "Μένει στη θέση του" in p.reason


def test_names_in_documents_never_create_person_folders(tmp_path):
    make_docx(tmp_path / "x.docx", "Γιάννης Παπαδόπουλος Γιάννης Παπαδόπουλος 2023")
    make_docx(tmp_path / "y.docx", "Μαρία Νικολάου ΔΕΗ λογαριασμός ρεύμα 2024")
    plan = plan_for(tmp_path)
    assert prop(plan, "x.docx").action == KEEP
    for p in plan.proposals:
        if p.dest:
            assert "Γιάννης" not in str(p.dest) and "Μαρία" not in str(p.dest)


def test_unreadable_content_falls_back_to_name(tmp_path):
    write(tmp_path / "ΔΕΗ Ιανουαρίου 2024.docx", b"corrupt")
    p = prop(plan_for(tmp_path), "ΔΕΗ Ιανουαρίου 2024.docx")
    assert p.action == MOVE
    assert p.dest.parent == tmp_path / "Έγγραφα" / "Λογαριασμοί" / "2024"
    assert "δεν διαβάστηκε το περιεχόμενο" in p.reason


def test_already_in_place_is_kept(tmp_path):
    f = make_docx(tmp_path / "Έγγραφα" / "Φορολογικά" / "2023" / "e.docx", "εκκαθαριστικό 2023")
    p = prop(plan_for(tmp_path), "e.docx")
    assert p.action == KEEP and "ήδη" in p.reason and f.exists()


def test_duplicates_go_to_review_keeper_is_shallowest(tmp_path):
    write(tmp_path / "top.pdf", b"same-bytes")
    write(tmp_path / "deep" / "er" / "copy.pdf", b"same-bytes")
    write(tmp_path / "mid" / "copy2.pdf", b"same-bytes")
    plan = plan_for(tmp_path)
    assert prop(plan, "top.pdf").action != REVIEW
    for n in ("copy.pdf", "copy2.pdf"):
        p = prop(plan, n)
        assert p.action == REVIEW
        assert p.dest.parent == tmp_path / "_Προς_έλεγχο" / "Διπλότυπα"
        assert "top.pdf" in p.reason and "δεν διαγράφεται" in p.reason


def test_same_name_collision_gets_suffix(tmp_path):
    make_docx(tmp_path / "a" / "ΔΕΗ 2023.docx", "ρεύμα ΔΕΗ")
    make_docx(tmp_path / "b" / "ΔΕΗ 2023.docx", "λογαριασμός ΔΕΗ ρεύμα")
    plan = plan_for(tmp_path, tasks=NO_SAME)
    dests = sorted(p.dest.name for p in plan.proposals)
    assert dests == ["ΔΕΗ 2023 (2).docx", "ΔΕΗ 2023.docx"]
    assert len({str(p.dest).casefold() for p in plan.proposals}) == 2
    assert any("Μετονομάζεται" in p.reason for p in plan.proposals)


def test_collision_with_existing_file_at_destination(tmp_path):
    make_docx(tmp_path / "Έγγραφα" / "Λογαριασμοί" / "2023" / "ΔΕΗ 2023.docx", "παλιό ΔΕΗ ρεύμα")
    make_docx(tmp_path / "ΔΕΗ 2023.docx", "νέο ΔΕΗ ρεύμα 2023")
    p = [x for x in plan_for(tmp_path, tasks=NO_SAME).proposals if x.entry.path.parent == tmp_path][0]
    assert p.action == MOVE and p.dest.name == "ΔΕΗ 2023 (2).docx"


def test_loose_exe_by_name_and_location_only(tmp_path):
    marker = tmp_path / "ran.txt"
    exe = write(tmp_path / "tool.exe", f"#!/bin/sh\ntouch {marker}\n".encode())
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    p = prop(plan_for(tmp_path), "tool.exe")
    assert p.action == MOVE and p.dest == tmp_path / "Προγράμματα" / "tool.exe"
    assert "δεν ανοίγεται" in p.reason
    assert not marker.exists()


def test_exe_inside_application_folder_stays(tmp_path):
    marker = tmp_path / "ran.txt"
    exe = write(tmp_path / "App" / "app.exe", f"#!/bin/sh\ntouch {marker}\n".encode())
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    write(tmp_path / "App" / "lib.dll", b"dll")
    p = prop(plan_for(tmp_path), "app.exe")
    assert p.action == KEEP and "φάκελο εφαρμογής" in p.reason
    assert not marker.exists()


def test_exe_folder_with_subfolders_stays(tmp_path):
    write(tmp_path / "App" / "app.exe", b"MZ")
    write(tmp_path / "App" / "data" / "x.exe", b"MZ2")
    assert prop(plan_for(tmp_path), "app.exe").action == KEEP


def test_image_date_from_name_then_mtime(tmp_path):
    write(tmp_path / "IMG_20230514_101010.jpg", b"1")
    other = write(tmp_path / "holiday.png", b"2")
    ts = datetime(2021, 8, 3, 12).timestamp()
    os.utime(other, (ts, ts))
    plan = plan_for(tmp_path)
    a, b = prop(plan, "IMG_20230514_101010.jpg"), prop(plan, "holiday.png")
    assert a.dest.parent == tmp_path / "Εικόνες" / "2023" / "2023-05" and "όνομα" in a.reason
    assert b.dest.parent == tmp_path / "Εικόνες" / "2021" / "2021-08" and "τροποποίησης" in b.reason


def test_invalid_date_in_image_name_falls_back(tmp_path):
    f = write(tmp_path / "IMG_20231345.jpg", b"1")
    ts = datetime(2020, 2, 2).timestamp()
    os.utime(f, (ts, ts))
    assert prop(plan_for(tmp_path), "IMG_20231345.jpg").dest.parent.name == "2020-02"


def test_unknown_type_untouched(tmp_path):
    write(tmp_path / "notes.txt", b"n")
    assert prop(plan_for(tmp_path), "notes.txt").action == KEEP


def test_plan_changes_nothing_and_is_deterministic(tmp_path):
    make_docx(tmp_path / "a.docx", "εκκαθαριστικό 2023")
    write(tmp_path / "d1.pdf", b"dup"); write(tmp_path / "s" / "d2.pdf", b"dup")
    write(tmp_path / "x.exe", b"MZ"); write(tmp_path / "p.jpg", b"i")
    before = snapshot(tmp_path)
    t1 = format_plan(plan_for(tmp_path)); t2 = format_plan(plan_for(tmp_path))
    assert snapshot(tmp_path) == before and t1 == t2
    assert "Μόνο πρόταση" in t1 and "Λόγος [" in t1


def test_every_proposal_has_rule_and_reason(tmp_path):
    make_docx(tmp_path / "a.docx", "εκκαθαριστικό 2023"); write(tmp_path / "n.txt", b"n")
    write(tmp_path / "x.exe", b"MZ"); write(tmp_path / "p.jpg", b"i")
    for p in plan_for(tmp_path).proposals:
        assert p.rule and len(p.reason) > 20


def test_destinations_stay_inside_root(tmp_path):
    root = tmp_path / "root"
    make_docx(root / "a.docx", "εκκαθαριστικό 2023"); write(root / "x.exe", b"MZ")
    write(root / "d1.pdf", b"dup"); write(root / "s" / "d2.pdf", b"dup")
    for p in plan_for(root).proposals:
        if p.dest:
            assert root in p.dest.parents


# ---------- ρυθμίσεις ----------

@pytest.mark.parametrize("rules", [
    "x", [{"topic": "A"}], [{"topic": "A/B", "keywords": ["k"]}],
    [{"topic": "A", "keywords": []}], [{"topic": "A", "keywords": [""]}],
    [{"topic": "A", "keywords": ["k"], "extra": 1}],
])
def test_bad_topic_rules_rejected(rules):
    with pytest.raises(cfgmod.ConfigError):
        Config(topic_rules=rules).validate()


def test_custom_topic_rules_and_folder_names(tmp_path):
    cfg = Config(topic_rules=[{"topic": "Αυτοκίνητο", "keywords": ["κτεο"]}],
                 documents_folder="Χαρτιά")
    make_docx(tmp_path / "ΚΤΕΟ 2024.docx", "κτεο")
    p = prop(plan_for(tmp_path, cfg), "ΚΤΕΟ 2024.docx")
    assert p.dest == tmp_path / "Χαρτιά" / "Αυτοκίνητο" / "2024" / "ΚΤΕΟ 2024.docx"


@pytest.mark.parametrize("attr", ["documents_folder", "images_folder", "programs_folder", "duplicates_subfolder"])
def test_bad_folder_names_rejected(attr):
    with pytest.raises(cfgmod.ConfigError):
        Config(**{attr: "a/b"}).validate()


def test_cli_plan(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    data = tmp_path / "data"
    make_docx(data / "a.docx", "εκκαθαριστικό 2023")
    before = snapshot(data)
    assert main(["plan", str(data)]) == 0
    out = capsys.readouterr().out
    assert "Έγγραφα/Φορολογικά/2023".replace("/", os.sep) in out and "Λόγος [" in out
    assert snapshot(data) == before


def test_duplicate_exe_inside_app_folder_is_not_moved_out(tmp_path):
    write(tmp_path / "setup.exe", b"MZ")
    write(tmp_path / "App" / "app.exe", b"MZ")  # ίδιο περιεχόμενο
    write(tmp_path / "App" / "lib.dll", b"d")
    p = prop(plan_for(tmp_path), "app.exe")
    assert p.action == KEEP and "φάκελο εφαρμογής" in p.reason


def test_error_notes_are_plain_greek_and_silent(tmp_path, capsys):
    write(tmp_path / "bad.pdf", b"garbage"); write(tmp_path / "bad.docx", b"zz")
    for n, k in (("bad.pdf", "pdf"), ("bad.docx", "word")):
        note = extract(tmp_path / n, k, 7).note
        assert "χαλασμένο" in note and "Error" not in note and "Zip" not in note
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""


def test_selftest_passes_and_touches_nothing_of_the_user(tmp_path, monkeypatch, capsys):
    from organotis.selftest import run
    monkeypatch.chdir(tmp_path)
    ok, lines = run()
    assert ok, "\n".join(lines)
    assert any("αναίρεση" in l for l in lines) and not list(tmp_path.iterdir())
    assert main(["selftest", "--out", str(tmp_path / "r.txt")]) == 0
    assert "όλα εντάξει" in (tmp_path / "r.txt").read_text(encoding="utf-8")


def test_winconsole_is_noop_off_windows():
    from organotis import winconsole
    assert winconsole.attach_if_needed() is False
