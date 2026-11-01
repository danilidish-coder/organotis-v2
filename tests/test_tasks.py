"""Εργασίες (duplicates, same_names, general) και προστατευμένοι φάκελοι."""
from datetime import date
from pathlib import Path

import pytest

from organotis import config as cfgmod
from organotis.cli import main
from organotis.config import Config
from organotis.planner import KEEP, MOVE, REVIEW, TASKS, build_plan
from organotis.scanner import build_report, scan

from helpers import make_docx, write

TODAY = date(2026, 10, 31)


def plan_for(root: Path, cfg: Config | None = None, **kw):
    cfg = cfg or Config()
    res = scan(root, cfg)
    return build_plan(res, build_report(res), cfg, today=TODAY, **kw)


def prop(plan, rel):
    return next(p for p in plan.proposals if p.entry.path.name == rel)


def test_default_tasks_constant():
    assert TASKS == ("duplicates", "same_names", "general")


def test_protected_folder_keeps_file_even_if_it_would_move(tmp_path):
    make_docx(tmp_path / "Φάκελος ΔΕΗ" / "υπο" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα")
    make_docx(tmp_path / "ΔΕΗ 2024.docx", "ΔΕΗ ρεύμα 2024")
    # Χωρίς προστασία το αρχείο θα μετακινούνταν.
    assert prop(plan_for(tmp_path), "ΔΕΗ 2023.docx").action == MOVE
    cfg = Config(protected_folders=["φακελοσ δεη"])  # χωρίς τόνους, πεζά, ς→σ
    p = prop(plan_for(tmp_path, cfg), "ΔΕΗ 2023.docx")
    assert p.action == KEEP
    assert "Ανήκει στον ενιαίο φάκελο «Φάκελος ΔΕΗ», που δεν σπάει" in p.reason
    assert prop(plan_for(tmp_path, cfg), "ΔΕΗ 2024.docx").action == MOVE


def test_protected_folder_also_protects_duplicates(tmp_path):
    write(tmp_path / "Ενιαίος" / "deep" / "a.txt", b"same")
    write(tmp_path / "b.txt", b"same")
    write(tmp_path / "Ενιαίος" / "c.txt", b"same")
    plan = plan_for(tmp_path, Config(protected_folders=["ενιαιος"]))
    assert prop(plan, "a.txt").action == KEEP
    assert prop(plan, "c.txt").action == KEEP
    assert prop(plan, "b.txt").action != REVIEW


def test_root_name_itself_is_not_an_ancestor(tmp_path):
    root = tmp_path / "Ενιαίος"
    make_docx(root / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα")
    plan = plan_for(root, Config(protected_folders=["Ενιαίος"]))
    assert prop(plan, "ΔΕΗ 2023.docx").action == MOVE


def test_only_duplicates(tmp_path):
    write(tmp_path / "a" / "x.txt", b"same")
    write(tmp_path / "x.txt", b"same")
    write(tmp_path / "b" / "y.txt", b"same")
    make_docx(tmp_path / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα")
    plan = plan_for(tmp_path, tasks=("duplicates",))
    assert len(plan.by_action(REVIEW)) == 2
    assert all("Διπλότυπα" in str(p.dest) for p in plan.by_action(REVIEW))
    assert prop(plan, "ΔΕΗ 2023.docx").action == KEEP
    assert plan.by_action(MOVE) == []


def test_without_general_document_with_keyword_stays(tmp_path):
    make_docx(tmp_path / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα")
    plan = plan_for(tmp_path, tasks=("duplicates", "same_names"))
    p = prop(plan, "ΔΕΗ 2023.docx")
    assert p.action == KEEP and "η εργασία δεν είναι επιλεγμένη" in p.reason


def test_without_duplicates_no_duplicate_proposal(tmp_path):
    write(tmp_path / "a" / "x.txt", b"same")
    write(tmp_path / "b" / "y.txt", b"same")
    plan = plan_for(tmp_path, tasks=("general",))
    assert plan.by_action(REVIEW) == []


def test_same_names_moves_deeper_file_to_review(tmp_path):
    make_docx(tmp_path / "a" / "Σημείωση.docx", "ένα")
    make_docx(tmp_path / "b" / "c" / "Σημείωση.docx", "δύο διαφορετικό")
    plan = plan_for(tmp_path, tasks=("same_names",))
    shallow = next(p for p in plan.proposals if p.entry.path.parent.name == "a")
    deep = next(p for p in plan.proposals if p.entry.path.parent.name == "c")
    assert shallow.action == KEEP
    assert deep.action == REVIEW
    assert deep.dest == tmp_path / "_Προς_έλεγχο" / "Ίδια ονόματα" / "Σημείωση.docx"
    assert "Ίδιο όνομα" in deep.reason


def test_same_names_skips_protected_files(tmp_path):
    make_docx(tmp_path / "Σημείωση.docx", "ένα")
    make_docx(tmp_path / "Ενιαίος" / "Σημείωση.docx", "δύο διαφορετικό")
    plan = plan_for(tmp_path, Config(protected_folders=["Ενιαίος"]), tasks=("same_names",))
    assert plan.by_action(REVIEW) == []


def test_move_cli_same_names_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORGANOTIS_CONFIG", str(tmp_path / "cfg" / "config.json"))
    root = tmp_path / "r"
    make_docx(root / "a" / "N.docx", "ένα")
    make_docx(root / "b" / "c" / "N.docx", "δύο διαφορετικό")
    assert main(["move", str(root), "--tasks", "same_names", "--yes"]) == 0
    assert (root / "_Προς_έλεγχο" / "Ίδια ονόματα" / "N.docx").exists()
    assert (root / "a" / "N.docx").exists()


@pytest.mark.parametrize("bad", ["x", "/", "a/b", "", 5, [1], ["ok", ""], [".."]])
def test_bad_protected_folders_is_error(bad):
    cfg = Config(protected_folders=bad)
    with pytest.raises(cfgmod.ConfigError):
        cfg.validate()


def test_protected_folders_default_empty_and_schema_unchanged():
    assert Config().protected_folders == [] and cfgmod.SCHEMA_VERSION == 1
    Config(protected_folders=["Ενιαίος"]).validate()


def test_bad_protected_folders_in_file_is_error(tmp_path):
    f = tmp_path / "c.json"
    f.write_text('{"protected_folders": "oops"}', encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load(f)


@pytest.mark.parametrize("cmd", ["plan", "move"])
def test_unknown_task_gives_code_2(tmp_path, monkeypatch, capsys, cmd):
    monkeypatch.setenv("ORGANOTIS_CONFIG", str(tmp_path / "cfg.json"))
    write(tmp_path / "r" / "a.txt")
    extra = ["--yes"] if cmd == "move" else []
    code = main([cmd, str(tmp_path / "r"), "--tasks", "duplicates,foo", *extra])
    err = capsys.readouterr().err
    assert code == 2 and "Άγνωστη εργασία" in err and "«foo»" in err


def test_build_plan_rejects_unknown_task(tmp_path):
    write(tmp_path / "a.txt")
    with pytest.raises(ValueError):
        plan_for(tmp_path, tasks=("nope",))


def test_plan_cli_tasks_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORGANOTIS_CONFIG", str(tmp_path / "cfg.json"))
    make_docx(tmp_path / "r" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα")
    assert main(["plan", str(tmp_path / "r"), "--tasks", "duplicates"]) == 0
    capsys.readouterr()
