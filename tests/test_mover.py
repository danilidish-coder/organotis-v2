import errno
import os
import subprocess
import sys
from pathlib import Path

import pytest

from organotis import config as cfgmod, manifest as mf, mover
from organotis.approval import ApprovedPlan, PreviewModel, parse_numbers
from organotis.cli import main
from organotis.config import Config
from organotis.planner import MOVE, Proposal, build_plan
from organotis.scanner import Entry, build_report, scan

from helpers import make_docx, write

TESTS = Path(__file__).parent


def plan_for(root, cfg=None):
    cfg = cfg or Config()
    res = scan(root, cfg)
    return build_plan(res, build_report(res), cfg)


def approved_for(root, remove=()):
    m = PreviewModel(plan_for(root))
    m.remove(remove)
    return m.approve()


def files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def contents(root: Path) -> list[bytes]:
    return sorted(p.read_bytes() for p in root.rglob("*") if p.is_file())


@pytest.fixture
def mdir(tmp_path):
    return tmp_path / "manifests"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "data"
    make_docx(root / "scan1.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")
    write(root / "a.pdf", b"dupdup"); write(root / "old" / "b.pdf", b"dupdup")
    write(root / "IMG_20230514_1.jpg", b"jpg")
    write(root / "notes.txt", b"keep me")
    return root


# ---------- έγκριση ----------

def test_cannot_forge_approval(tree):
    plan = plan_for(tree)
    with pytest.raises(TypeError):
        ApprovedPlan(plan.root, tuple(plan.proposals), 0)


def test_execute_requires_approved_plan(tree, mdir):
    with pytest.raises(TypeError):
        mover.execute(plan_for(tree), mdir)
    assert not mdir.exists()




def test_preview_model(tree):
    m = PreviewModel(plan_for(tree))
    assert [r.number for r in m.rows] == list(range(1, len(m.rows) + 1))
    assert all(r.proposal.action in ("move", "review") for r in m.rows)
    n = len(m.rows)
    m.remove([1, 2])
    assert len(m.active) == n - 2
    m.restore([1])
    assert len(m.active) == n - 1
    with pytest.raises(ValueError):
        m.remove([999])


def test_cannot_approve_empty(tree):
    m = PreviewModel(plan_for(tree))
    m.remove([r.number for r in m.rows])
    with pytest.raises(ValueError):
        m.approve()


def test_parse_numbers():
    assert parse_numbers("1, 3, 5-7") == [1, 3, 5, 6, 7]
    assert parse_numbers("") == []
    for bad in ("a", "3-1", "1-"):
        with pytest.raises(ValueError):
            parse_numbers(bad)


# ---------- μετακίνηση ----------

def test_move_everything_nothing_lost_or_deleted(tree, mdir):
    before = contents(tree)
    res = mover.execute(approved_for(tree), mdir)
    assert res.status == "complete" and not res.skipped
    assert contents(tree) == before  # ίδια αρχεία, ίδια περιεχόμενα: τίποτα δεν χάθηκε ή διαγράφηκε
    assert (tree / "Έγγραφα" / "Φορολογικά" / "2023" / "scan1.docx").exists()
    assert (tree / "Εικόνες" / "2023" / "2023-05" / "IMG_20230514_1.jpg").exists()
    assert (tree / "_Προς_έλεγχο" / "Διπλότυπα" / "b.pdf").exists()
    assert (tree / "a.pdf").exists() and (tree / "notes.txt").exists()
    assert not (tree / "old" / "b.pdf").exists()


def test_removed_rows_are_not_moved(tree, mdir):
    m = PreviewModel(plan_for(tree))
    row = next(r for r in m.rows if r.proposal.entry.path.name == "scan1.docx")
    m.remove([row.number])
    mover.execute(m.approve(), mdir)
    assert (tree / "scan1.docx").exists()
    assert not (tree / "Έγγραφα").exists()
    assert (tree / "Εικόνες").exists()


def test_manifest_contents(tree, mdir):
    res = mover.execute(approved_for(tree), mdir)
    m = mf.read(res.manifest_path)
    assert m.ended and m.status == "complete" and m.root == tree
    assert len(m.ops) == res.moved == 3 and all(o.state == "done" for o in m.ops)
    assert all(o.reason and o.rule for o in m.ops)
    assert ["Έγγραφα"] in m.dirs and ["Έγγραφα", "Φορολογικά"] in m.dirs
    # σχετικές διαδρομές: καμία σταθερή διαδρομή μέσα στο manifest εκτός από το root της κεφαλίδας
    text = res.manifest_path.read_text(encoding="utf-8")
    assert text.count(str(tree)) == 1


def test_manifest_survives_reload_from_disk(tree, mdir):
    res = mover.execute(approved_for(tree), mdir)
    again = mf.read(res.manifest_path)
    assert [mf.from_parts(tree, o.dest).exists() for o in again.ops] == [True] * 3


def test_same_names_never_overwrite(tmp_path, mdir):
    root = tmp_path / "r"
    make_docx(root / "a" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα α")
    make_docx(root / "b" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα β λογαριασμός")
    before = contents(root)
    mover.execute(approved_for(root), mdir)
    out = sorted(p.name for p in (root / "Έγγραφα" / "Λογαριασμοί" / "2023").iterdir())
    assert out == ["ΔΕΗ 2023 (2).docx", "ΔΕΗ 2023.docx"]
    assert contents(root) == before


def test_destination_taken_after_plan_is_skipped_not_overwritten(tree, mdir):
    approved = approved_for(tree)
    target = tree / "Έγγραφα" / "Φορολογικά" / "2023" / "scan1.docx"
    write(target, b"someone else's file")
    res = mover.execute(approved, mdir)
    assert target.read_bytes() == b"someone else's file"
    assert (tree / "scan1.docx").exists()
    assert any("ήδη αρχείο" in why for _, why in res.skipped)


def test_locked_file_is_skipped_and_run_continues(tree, mdir, monkeypatch):
    real = mover._rename

    def locked(src, dest):
        if Path(src).name == "scan1.docx":
            raise PermissionError(13, "Permission denied")
        real(src, dest)

    monkeypatch.setattr(mover, "_rename", locked)
    res = mover.execute(approved_for(tree), mdir)
    assert (tree / "scan1.docx").exists()
    assert (tree / "Εικόνες" / "2023" / "2023-05" / "IMG_20230514_1.jpg").exists()  # συνεχίστηκε
    assert len(res.skipped) == 1 and "κλειδωμένο" in res.skipped[0][1]
    m = mf.read(res.manifest_path)
    assert sorted(o.state for o in m.ops) == ["done", "done", "skipped"]


def test_file_changed_after_scan_is_skipped(tree, mdir):
    approved = approved_for(tree)
    f = tree / "IMG_20230514_1.jpg"
    f.write_bytes(b"changed content, different size")
    res = mover.execute(approved, mdir)
    assert f.exists() and any("άλλαξε" in why for _, why in res.skipped)


def test_missing_source_is_skipped(tree, mdir):
    approved = approved_for(tree)
    (tree / "IMG_20230514_1.jpg").unlink()
    res = mover.execute(approved, mdir)
    assert any("δεν υπάρχει" in why for _, why in res.skipped)
    assert res.moved == 2


def test_destination_outside_root_rejected(tmp_path, mdir):
    root = tmp_path / "r"; f = write(root / "x.pdf", b"1")
    st = f.stat()
    p = Proposal(Entry(f, 1, "pdf", ".pdf", st.st_mtime_ns), MOVE, tmp_path / "outside" / "x.pdf", "t", "t")
    m = PreviewModel(type("P", (), {"root": root, "proposals": [p]})())
    res = mover.execute(m.approve(), mdir)
    assert res.moved == 0 and "έξω" in res.skipped[0][1]
    assert f.exists() and not (tmp_path / "outside").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_source_turned_symlink_is_skipped(tree, mdir):
    approved = approved_for(tree)
    f = tree / "IMG_20230514_1.jpg"; f.unlink(); f.symlink_to(tree / "notes.txt")
    res = mover.execute(approved, mdir)
    assert any("σύνδεσμος" in why for _, why in res.skipped)
    assert f.is_symlink()


def test_stop_midway_leaves_consistent_manifest(tree, mdir):
    calls = []
    res = mover.execute(approved_for(tree), mdir, should_stop=lambda: len(calls) >= 1,
                        progress=lambda i, n, p: calls.append(i))
    assert res.status == "stopped" and res.moved == 1
    m = mf.read(res.manifest_path)
    assert m.ended and m.status == "stopped" and len(m.ops) == 1 and m.ops[0].state == "done"


def test_loose_exe_moved_but_never_executed(tmp_path, mdir):
    root = tmp_path / "r"; marker = tmp_path / "ran.txt"
    exe = write(root / "tool.exe", f"#!/bin/sh\ntouch {marker}\n".encode())
    exe.chmod(0o755)
    mover.execute(approved_for(root), mdir)
    assert (root / "Προγράμματα" / "tool.exe").exists() and not marker.exists()


def test_after_move_plan_is_stable(tree, mdir):
    mover.execute(approved_for(tree), mdir)
    plan = plan_for(tree)
    assert not [p for p in plan.proposals if p.action != "keep"]


# ---------- cross-device ----------

def test_cross_device_copy_verified_then_source_removed(tree, mdir, monkeypatch):
    real = mover._rename

    def exdev(src, dest):
        if str(dest).endswith(".organotis-part") or not str(src).endswith("IMG_20230514_1.jpg"):
            return real(src, dest)
        raise OSError(errno.EXDEV, "cross-device")

    monkeypatch.setattr(mover, "_rename", exdev)
    # το τελικό os.rename του προσωρινού αρχείου είναι πραγματικό· η πρώτη απόπειρα απορρίπτεται με EXDEV
    res = mover.execute(approved_for(tree), mdir)
    d = tree / "Εικόνες" / "2023" / "2023-05"
    assert (d / "IMG_20230514_1.jpg").read_bytes() == b"jpg"
    assert not (tree / "IMG_20230514_1.jpg").exists()
    assert not list(tree.rglob("*.organotis-part")) and not res.skipped


def test_cross_device_failed_verification_keeps_source(tree, mdir, monkeypatch):
    real = mover._rename

    def exdev(src, dest):
        if str(src).endswith("IMG_20230514_1.jpg"):
            raise OSError(errno.EXDEV, "cross-device")
        real(src, dest)

    monkeypatch.setattr(mover, "_rename", exdev)
    monkeypatch.setattr(mover, "_sha256", lambda p: os.urandom(8).hex())
    res = mover.execute(approved_for(tree), mdir)
    assert (tree / "IMG_20230514_1.jpg").read_bytes() == b"jpg"
    assert not list(tree.rglob("*.organotis-part"))
    assert len(res.skipped) == 1


# ---------- διακοπή (πραγματικός θάνατος διεργασίας) και ανάκτηση ----------

def run_crash(root, mdir, mode, nth):
    return subprocess.run([sys.executable, str(TESTS / "crash_runner.py"), str(root), str(mdir), mode, str(nth)],
                          capture_output=True, text=True)


@pytest.mark.parametrize("mode", ["before", "after"])
def test_hard_kill_midway_then_recover(tree, mdir, mode):
    before = contents(tree)
    r = run_crash(tree, mdir, mode, 2)
    assert r.returncode == 9, r.stderr
    assert contents(tree) == before  # κανένα αρχείο δεν χάθηκε ή διπλασιάστηκε

    (path,) = mf.list_manifests(mdir)
    m = mf.read(path)
    assert not m.ended
    assert [o.state for o in m.ops] == ["done", "pending"]

    notes = mover.reconcile(path)
    m = mf.read(path)
    assert m.ended and m.status == "recovered" and len(notes) == 1
    if mode == "after":
        assert m.ops[1].state == "done" and mf.from_parts(tree, m.ops[1].dest).exists()
    else:
        assert m.ops[1].state == "skipped" and mf.from_parts(tree, m.ops[1].src).exists()
    assert contents(tree) == before


def test_next_run_recovers_unfinished_manifest_first(tree, mdir):
    run_crash(tree, mdir, "after", 1)
    res = mover.execute(approved_for(tree), mdir)
    assert any("είχε μετακινηθεί" in n for n in res.recovered_notes)
    assert all(mf.read(p).ended for p in mf.list_manifests(mdir))


def test_truncated_last_line_is_tolerated(tree, mdir):
    res = mover.execute(approved_for(tree), mdir)
    with open(res.manifest_path, "a", encoding="utf-8") as f:
        f.write('{"t": "intent", "i": 9, "sr')  # μισογραμμένη γραμμή από διακοπή
    m = mf.read(res.manifest_path)
    assert len(m.ops) == 3


def test_corrupt_middle_line_is_an_error(tree, mdir):
    res = mover.execute(approved_for(tree), mdir)
    lines = res.manifest_path.read_text(encoding="utf-8").split("\n")
    lines[2] = "{broken"
    res.manifest_path.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ValueError):
        mf.read(res.manifest_path)


def test_reconcile_conflict_when_in_both_places(tree, mdir):
    res = mover.execute(approved_for(tree), mdir)
    op = mf.read(res.manifest_path).ops[0]
    # προσομοίωση: intent χωρίς done και αρχείο και στις δύο θέσεις
    mf.append(res.manifest_path, {"t": "intent", "i": 99, "src": op.src, "dest": op.dest,
                                  "size": 1, "mtime_ns": 0, "action": "move", "rule": "r", "reason": "x"})
    write(tree.joinpath(*op.src), b"again")
    path = res.manifest_path
    lines = [l for l in path.read_text(encoding="utf-8").split("\n") if '"t": "end"' not in l]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    notes = mover.reconcile(path)
    assert any("και στις δύο" in n for n in notes)
    assert mf.read(path).ops[-1].state == "conflict"
    assert tree.joinpath(*op.src).read_bytes() == b"again"  # δεν αγγίχτηκε


# ---------- CLI ----------

def test_cli_move_yes_with_remove(monkeypatch, tree, tmp_path, capsys):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    plan = plan_for(tree)
    n = next(r.number for r in PreviewModel(plan).rows if r.proposal.entry.path.name == "scan1.docx")
    assert main(["move", str(tree), "--yes", "--remove", str(n)]) == 0
    out = capsys.readouterr().out
    assert "Προεπισκόπηση" in out and "Manifest" in out
    assert (tree / "scan1.docx").exists() and (tree / "Εικόνες").exists()
    assert list((tmp_path / "cfg" / "manifests").glob("*.jsonl"))


def test_cli_move_requires_approval_when_not_interactive(monkeypatch, tree, tmp_path, capsys):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    before = files(tree)
    assert main(["move", str(tree)]) == 1  # το stdin των δοκιμών δεν είναι τερματικό
    assert files(tree) == before
    assert "έγκριση" in capsys.readouterr().err


def test_cli_bad_remove_numbers(monkeypatch, tree, tmp_path):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    before = files(tree)
    assert main(["move", str(tree), "--yes", "--remove", "999"]) == 2
    assert main(["move", str(tree), "--yes", "--remove", "xx"]) == 2
    assert files(tree) == before


def test_cli_move_interactive_decline(monkeypatch, tree, tmp_path):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(["", "όχι"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    before = files(tree)
    assert main(["move", str(tree)]) == 1
    assert files(tree) == before


def test_cli_move_interactive_remove_then_confirm(monkeypatch, tree, tmp_path):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    n = len(PreviewModel(plan_for(tree)).rows)
    answers = iter([f"1-{n - 1}", "", "ΝΑΙ"])  # κρατάμε μόνο την τελευταία γραμμή
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert main(["move", str(tree)]) == 0
    moved = [p for p in tree.rglob("*") if p.is_file() and p.parent != tree and "old" not in p.parts]
    assert len(moved) == 1
