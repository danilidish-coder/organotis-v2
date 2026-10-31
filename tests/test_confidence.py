"""Λειτουργία 2 της v0.12: κάθε πρόταση είναι «σίγουρη» ή «να τη δεις», με κανόνα σε απλά ελληνικά."""
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from organotis import config as cfgmod, mover
from organotis.approval import PreviewModel
from organotis.cli import main
from organotis.config import Config
from organotis.formatting import format_plan, format_preview
from organotis.planner import CHECK, KEEP, MOVE, REVIEW, SURE, build_plan
from organotis.scanner import build_report, scan

from helpers import make_docx, make_pdf, write

TODAY = date(2026, 10, 31)


def plan_for(root, cfg=None):
    cfg = cfg or Config()
    res = scan(root, cfg)
    return build_plan(res, build_report(res), cfg, today=TODAY)


def prop(plan, name):
    return next(p for p in plan.proposals if p.entry.path.name == name)


def snap(root: Path):
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))


# ---------- κανόνες βεβαιότητας ----------

def test_document_with_two_topic_words_and_clear_year_is_sure(tmp_path):
    make_docx(tmp_path / "a.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")
    p = prop(plan_for(tmp_path), "a.docx")
    assert p.action == MOVE and p.confidence == SURE
    assert "2 διαφορετικές λέξεις του θέματος" in p.confidence_why and "το έτος είναι σαφές" in p.confidence_why


def test_single_topic_word_needs_a_look(tmp_path):
    make_pdf(tmp_path / "a.pdf", "IBAN statement 2022 2022")
    p = prop(plan_for(tmp_path), "a.pdf")
    assert p.confidence == CHECK and "μόνο 1 λέξη του θέματος («iban»)" in p.confidence_why


def test_words_of_another_topic_need_a_look(tmp_path):
    make_docx(tmp_path / "a.docx", "Εκκαθαριστικό ΑΦΜ και ΔΕΗ 2023 2023")
    p = prop(plan_for(tmp_path), "a.docx")
    assert p.dest.parent.parent.name == "Φορολογικά"
    assert p.confidence == CHECK and "άλλου θέματος" in p.confidence_why


def test_ambiguous_year_needs_a_look(tmp_path):
    make_docx(tmp_path / "a.docx", "Εκκαθαριστικό ΑΦΜ 2022 2023")
    p = prop(plan_for(tmp_path), "a.docx")
    assert p.confidence == CHECK and "το έτος είναι ασαφές" in p.confidence_why


def test_no_year_at_all_can_still_be_sure(tmp_path):
    make_docx(tmp_path / "a.docx", "Εκκαθαριστικό ΑΦΜ")
    p = prop(plan_for(tmp_path), "a.docx")
    assert p.confidence == SURE and "δεν βρέθηκε έτος" in p.confidence_why


def test_several_problems_are_all_listed(tmp_path):
    make_docx(tmp_path / "a.docx", "Εκκαθαριστικό ΑΦΜ και ΔΕΗ 2022 2023")
    why = prop(plan_for(tmp_path), "a.docx").confidence_why
    assert why.count(";") == 1 and "άλλου θέματος" in why and "το έτος είναι ασαφές" in why


def test_image_date_in_name_is_sure_but_file_time_is_not(tmp_path):
    write(tmp_path / "IMG_20230514_1.jpg", b"1")
    other = write(tmp_path / "holiday.png", b"2")
    ts = datetime(2021, 8, 3, 12).timestamp(); os.utime(other, (ts, ts))
    plan = plan_for(tmp_path)
    assert prop(plan, "IMG_20230514_1.jpg").confidence == SURE
    p = prop(plan, "holiday.png")
    assert p.confidence == CHECK and "ώρα τροποποίησης" in p.confidence_why and "αντιγράφεται" in p.confidence_why


def test_exe_is_never_sure(tmp_path):
    write(tmp_path / "tool.exe", b"MZ")
    p = prop(plan_for(tmp_path), "tool.exe")
    assert p.action == MOVE and p.confidence == CHECK and "δεν εγκρίνεται ποτέ αυτόματα" in p.confidence_why


def test_identical_duplicates_are_sure_because_nothing_is_deleted(tmp_path):
    write(tmp_path / "a.pdf", b"same"); write(tmp_path / "x" / "b.pdf", b"same")
    p = prop(plan_for(tmp_path), "b.pdf")
    assert p.action == REVIEW and p.confidence == SURE and "SHA-256" in p.confidence_why


def test_renamed_because_of_collision_downgrades_to_check(tmp_path):
    make_docx(tmp_path / "a" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα λογαριασμός α")
    make_docx(tmp_path / "b" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα λογαριασμός β")
    plan = plan_for(tmp_path)
    confs = sorted((p.dest.name, p.confidence) for p in plan.proposals)
    assert confs == [("ΔΕΗ 2023 (2).docx", CHECK), ("ΔΕΗ 2023.docx", SURE)]
    renamed = [p for p in plan.proposals if p.dest.name.endswith("(2).docx")][0]
    assert "μετονομάζεται" in renamed.confidence_why


def test_every_actionable_proposal_explains_its_confidence_in_plain_greek(tmp_path):
    make_docx(tmp_path / "a.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023"); make_pdf(tmp_path / "b.pdf", "IBAN 2022 2022")
    write(tmp_path / "c.jpg", b"x"); write(tmp_path / "d.exe", b"MZ")
    write(tmp_path / "e1.bin", b"dup"); write(tmp_path / "s" / "e2.bin", b"dup")
    for p in plan_for(tmp_path).proposals:
        if p.action != KEEP:
            assert p.confidence in (SURE, CHECK) and len(p.confidence_why) > 20, p
            assert not any(ch.isdigit() and "%" in p.confidence_why for ch in p.confidence_why)  # όχι ποσοστά/σκορ


def test_plan_and_preview_show_confidence_and_reason(tmp_path):
    make_docx(tmp_path / "a.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023"); make_pdf(tmp_path / "b.pdf", "IBAN 2022 2022")
    plan = plan_for(tmp_path)
    text = format_plan(plan)
    assert "Βεβαιότητα: ΣΙΓΟΥΡΗ, επειδή" in text and "Βεβαιότητα: ΝΑ ΤΗ ΔΕΙΣ, επειδή" in text
    pv = format_preview(PreviewModel(plan))
    assert "[ΣΙΓΟΥΡΗ]" in pv and "[ΝΑ ΤΗ ΔΕΙΣ]" in pv and "1 σίγουρες, 1 «να τις δεις»" in pv


# ---------- μοντέλο έγκρισης ----------

@pytest.fixture
def mixed(tmp_path):
    root = tmp_path / "data"
    make_docx(root / "tax.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")      # σίγουρη
    make_docx(root / "bank.docx", "IBAN 2022 2022")                    # να τη δεις (1 λέξη)
    write(root / "IMG_20230514_1.jpg", b"1")                           # σίγουρη
    write(root / "tool.exe", b"MZ")                                    # να τη δεις
    return root


def names(rows):
    return sorted(r.proposal.entry.path.name for r in rows)


def test_model_starts_with_nothing_approved(mixed):
    m = PreviewModel(plan_for(mixed))
    assert len(m.rows) == 4 and not m.approved_rows
    assert names(m.sure_rows) == ["IMG_20230514_1.jpg", "tax.docx"]
    assert names(m.check_rows) == ["bank.docx", "tool.exe"]
    with pytest.raises(ValueError):
        m.approve_selected()  # τίποτα εγκεκριμένο: δεν μετακινείται τίποτα


def test_approve_all_sure_approves_only_sure(mixed):
    m = PreviewModel(plan_for(mixed))
    assert m.approve_all_sure() == 2
    assert names(m.approved_rows) == ["IMG_20230514_1.jpg", "tax.docx"]
    assert {m.status(r.number) for r in m.check_rows} == {"pending"}
    approved = m.approve_selected()
    assert sorted(p.entry.path.name for p in approved.proposals) == ["IMG_20230514_1.jpg", "tax.docx"]


def test_approve_all_sure_skips_removed_rows(mixed):
    m = PreviewModel(plan_for(mixed))
    tax = next(r.number for r in m.rows if r.proposal.entry.path.name == "tax.docx")
    m.remove([tax])
    assert m.approve_all_sure() == 1 and m.status(tax) == "removed"


def test_manual_approval_of_a_check_row_and_state_changes(mixed):
    m = PreviewModel(plan_for(mixed))
    m.approve_all_sure()
    bank = next(r.number for r in m.rows if r.proposal.entry.path.name == "bank.docx")
    m.approve_numbers([bank]); assert m.status(bank) == "approved"
    m.remove([bank]); assert m.status(bank) == "removed" and bank not in m.approved
    m.restore([bank]); assert m.status(bank) == "pending"      # η επαναφορά ΔΕΝ εγκρίνει
    m.approve_numbers([bank]); m.unapprove([bank]); assert m.status(bank) == "pending"
    m.remove([bank]); m.approve_numbers([bank]); assert m.status(bank) == "approved"  # η έγκριση ξαναβάζει τη γραμμή


def test_legacy_approve_still_takes_all_active_rows(mixed):
    m = PreviewModel(plan_for(mixed))
    m.remove([1])
    assert len(m.approve().proposals) == 3


def test_state_survives_replan_by_file_path(mixed):
    m = PreviewModel(plan_for(mixed))
    m.approve_all_sure(); m.remove([next(r.number for r in m.rows if r.proposal.entry.path.name == "tool.exe")])
    state = m.export_state()
    m2 = PreviewModel(plan_for(mixed)); m2.import_state(state)
    assert names(m2.approved_rows) == names(m.approved_rows)
    assert names([r for r in m2.rows if r.number in m2.removed]) == ["tool.exe"]


def test_bad_numbers_rejected(mixed):
    m = PreviewModel(plan_for(mixed))
    for fn in (m.approve_numbers, m.unapprove, m.remove, m.restore):
        with pytest.raises(ValueError):
            fn([999])


# ---------- μετακίνηση μόνο των εγκεκριμένων + αναίρεση ----------

def test_only_approved_rows_move_and_undo_restores(mixed, tmp_path):
    from organotis.undo import undo
    before = snap(mixed)
    m = PreviewModel(plan_for(mixed)); m.approve_all_sure()
    run = mover.execute(m.approve_selected(), tmp_path / "mf")
    assert run.moved == 2
    assert (mixed / "tool.exe").exists() and (mixed / "bank.docx").exists()  # οι «να τη δεις» δεν κουνήθηκαν
    assert (mixed / "Έγγραφα" / "Φορολογικά" / "2023" / "tax.docx").exists()
    assert undo(run.manifest_path).restored == 2 and snap(mixed) == before


def test_manifest_records_confidence(mixed, tmp_path):
    from organotis import manifest as mf
    m = PreviewModel(plan_for(mixed)); m.approve_all_sure()
    run = mover.execute(m.approve_selected(), tmp_path / "mf")
    assert '"confidence": "sure"' in run.manifest_path.read_text(encoding="utf-8")


# ---------- γραμμή εντολών και επανεκκίνηση ----------

def cli(env, *args):
    return subprocess.run([sys.executable, "-m", "organotis", *args], env=env,
                          capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    return {**os.environ, cfgmod.ENV_CONFIG: str(tmp_path / "cfg" / "c.json"), "PYTHONUTF8": "1"}


def test_cli_only_sure_moves_only_sure_then_undo_after_restart(mixed, env):
    before = snap(mixed)
    r = cli(env, "move", str(mixed), "--only-sure", "--yes")
    assert r.returncode == 0, r.stderr
    assert "ΣΙΓΟΥΡΗ" in r.stdout and "ΝΑ ΤΗ ΔΕΙΣ" in r.stdout and "--only-sure" in r.stdout
    assert (mixed / "tool.exe").exists() and (mixed / "bank.docx").exists()
    assert not (mixed / "tax.docx").exists() and snap(mixed) != before
    # «επανεκκίνηση»: η αναίρεση τρέχει σε άλλη διεργασία, με μόνο το manifest στον δίσκο
    u = cli(env, "undo", "--last", "--yes")
    assert u.returncode == 0, u.stdout + u.stderr
    assert snap(mixed) == before


def test_cli_only_sure_with_nothing_sure_moves_nothing(tmp_path, env):
    root = tmp_path / "d"; write(root / "tool.exe", b"MZ")
    before = snap(root)
    r = cli(env, "move", str(root), "--only-sure", "--yes")
    assert r.returncode == 2 and snap(root) == before  # «Δεν έμεινε καμία γραμμή»


def test_cli_without_only_sure_still_moves_everything(mixed, env):
    r = cli(env, "move", str(mixed), "--yes")
    assert r.returncode == 0 and (mixed / "Προγράμματα" / "tool.exe").exists()
