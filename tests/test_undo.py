import os
import subprocess
import sys
from pathlib import Path

import pytest

from organotis import config as cfgmod, manifest as mf, mover
from organotis.approval import PreviewModel
from organotis.cli import main
from organotis.config import Config
from organotis.planner import build_plan
from organotis.scanner import build_report, scan
from organotis.undo import list_runs, pending_ops, undo

from helpers import make_docx, write

TESTS = Path(__file__).parent


def plan_for(root):
    cfg = Config()
    res = scan(root, cfg)
    return build_plan(res, build_report(res), cfg)


def do_move(root, mdir, remove=()):
    m = PreviewModel(plan_for(root))
    m.remove(remove)
    return mover.execute(m.approve(), mdir)


def snap(root: Path):
    """Όλα τα αρχεία και οι φάκελοι με τα περιεχόμενα: για σύγκριση «πριν = μετά»."""
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None)
                  for p in root.rglob("*"))


@pytest.fixture
def mdir(tmp_path):
    return tmp_path / "manifests"


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "data"
    make_docx(root / "scan1.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")
    write(root / "a.pdf", b"dupdup"); write(root / "old" / "b.pdf", b"dupdup")
    write(root / "IMG_20230514_1.jpg", b"jpg")
    write(root / "setup.exe", b"MZ")
    write(root / "notes.txt", b"keep me")
    return root


def test_undo_restores_everything_exactly(tree, mdir):
    before = snap(tree)
    run = do_move(tree, mdir)
    assert snap(tree) != before
    res = undo(run.manifest_path)
    assert res.status == "complete" and res.restored == run.moved and not res.skipped
    assert snap(tree) == before  # ίδια αρχεία, ίδια περιεχόμενα και ίδιοι φάκελοι


def test_undo_removes_only_folders_the_run_created(tree, mdir):
    (tree / "Εικόνες").mkdir()  # υπήρχε ήδη πριν την εκτέλεση
    before = snap(tree)
    run = do_move(tree, mdir)
    undo(run.manifest_path)
    assert (tree / "Εικόνες").is_dir()
    assert not (tree / "Έγγραφα").exists() and not (tree / "_Προς_έλεγχο").exists()
    assert snap(tree) == before


def test_created_folder_with_user_file_is_kept(tree, mdir):
    run = do_move(tree, mdir)
    keep = write(tree / "Εικόνες" / "2023" / "mine.txt", b"user file")
    res = undo(run.manifest_path)
    assert keep.read_bytes() == b"user file"
    assert (tree / "Εικόνες" / "2023").is_dir()
    assert (tree / "IMG_20230514_1.jpg").exists() and res.status == "complete"


def test_undo_is_idempotent(tree, mdir):
    before = snap(tree)
    run = do_move(tree, mdir)
    undo(run.manifest_path)
    again = undo(run.manifest_path)
    assert again.restored == 0 and not again.skipped and snap(tree) == before


def test_survives_real_restart_via_cli_processes(tree, tmp_path):
    """Μετακίνηση σε μία διεργασία, αναίρεση σε άλλη, με μόνο το manifest στον δίσκο."""
    # UTF-8 ρητά: σε Windows με αγγλική κωδικοσελίδα (π.χ. στο CI) τα ελληνικά σε pipe θα γίνονταν «?».
    env = {**os.environ, cfgmod.ENV_CONFIG: str(tmp_path / "cfg" / "c.json"), "PYTHONUTF8": "1"}

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "organotis", *args], env=env,
                              capture_output=True, text=True, encoding="utf-8")

    before = snap(tree)
    r1 = cli("move", str(tree), "--yes")
    assert r1.returncode == 0, r1.stderr
    assert snap(tree) != before
    assert "εφαρμόστηκε" in cli("undo").stdout
    r3 = cli("undo", "--last", "--yes")
    assert r3.returncode == 0, r3.stderr + r3.stdout
    assert snap(tree) == before
    assert "αναιρέθηκε" in cli("undo").stdout


def test_same_names_round_trip(tmp_path, mdir):
    root = tmp_path / "r"
    make_docx(root / "a" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα α")
    make_docx(root / "b" / "ΔΕΗ 2023.docx", "ΔΕΗ ρεύμα β λογαριασμός")
    before = snap(root)
    run = do_move(root, mdir)
    undo(run.manifest_path)
    assert snap(root) == before


# ---------- ό,τι δεν είναι ασφαλές παραλείπεται ----------

def test_modified_file_skipped_unless_allowed(tree, mdir):
    run = do_move(tree, mdir)
    img = tree / "Εικόνες" / "2023" / "2023-05" / "IMG_20230514_1.jpg"
    img.write_bytes(b"edited by user, longer")
    res = undo(run.manifest_path)
    assert img.exists() and not (tree / "IMG_20230514_1.jpg").exists()
    assert len(res.skipped) == 1 and "άλλαξε" in res.skipped[0][1] and res.status == "partial"
    res2 = undo(run.manifest_path, allow_modified=True)  # ξανά, με ρητή επιλογή
    assert res2.restored == 1 and (tree / "IMG_20230514_1.jpg").read_bytes() == b"edited by user, longer"


def test_original_spot_taken_is_never_overwritten(tree, mdir):
    run = do_move(tree, mdir)
    write(tree / "scan1.docx", b"a new file with the same name")
    res = undo(run.manifest_path)
    assert (tree / "scan1.docx").read_bytes() == b"a new file with the same name"
    assert (tree / "Έγγραφα" / "Φορολογικά" / "2023" / "scan1.docx").exists()
    assert any("πιαστεί" in why for _, why in res.skipped)


def test_file_removed_by_user_is_skipped(tree, mdir):
    run = do_move(tree, mdir)
    (tree / "Εικόνες" / "2023" / "2023-05" / "IMG_20230514_1.jpg").unlink()
    res = undo(run.manifest_path)
    assert any("δεν βρίσκεται πια" in why for _, why in res.skipped)
    assert res.restored == run.moved - 1


def test_original_folder_deleted_by_user_is_recreated(tree, mdir):
    run = do_move(tree, mdir)
    (tree / "old").rmdir()  # άδειος μετά τη μετακίνηση του διπλότυπου
    res = undo(run.manifest_path)
    assert (tree / "old" / "b.pdf").exists() and res.status == "complete"


def test_locked_file_skipped_then_retry_succeeds(tree, mdir, monkeypatch):
    run = do_move(tree, mdir)
    real = mover._rename

    def locked(src, dest):
        if Path(src).name == "scan1.docx":
            raise PermissionError(13, "denied")
        real(src, dest)

    monkeypatch.setattr(mover, "_rename", locked)
    res = undo(run.manifest_path)
    assert len(res.skipped) == 1 and "κλειδωμένο" in res.skipped[0][1]
    assert (tree / "Εικόνες").exists() is False  # τα υπόλοιπα επέστρεψαν
    monkeypatch.setattr(mover, "_rename", real)
    res2 = undo(run.manifest_path)  # αφού ξεκλειδώσει
    assert res2.restored == 1 and (tree / "scan1.docx").exists() and res2.status == "complete"
    assert [o.undo for o in mf.read(run.manifest_path).ops] == ["undone"] * run.moved


def test_stop_then_resume(tree, mdir):
    before = snap(tree)
    run = do_move(tree, mdir)
    seen = []
    r1 = undo(run.manifest_path, should_stop=lambda: len(seen) >= 1, progress=lambda i, n, o: seen.append(i))
    assert r1.status == "stopped" and r1.restored == 1
    assert list_runs(mdir)[0].label.startswith("αναιρέθηκε μερικώς")
    r2 = undo(run.manifest_path)
    assert r2.restored == run.moved - 1 and snap(tree) == before


# ---------- διακοπή στη μέση της αναίρεσης ----------

@pytest.mark.parametrize("mode", ["before", "after"])
def test_hard_kill_during_undo_then_finish(tree, mdir, mode):
    before = snap(tree)
    run = do_move(tree, mdir)
    moved_state = snap(tree)
    r = subprocess.run([sys.executable, str(TESTS / "crash_undo_runner.py"), str(run.manifest_path), mode, "2"],
                       capture_output=True, text=True)
    assert r.returncode == 9, r.stderr
    # καμία απώλεια: κάθε αρχείο υπάρχει ακριβώς μία φορά
    contents = lambda s: sorted(c for _, c in s if c is not None)
    assert contents(snap(tree)) == contents(before)
    m = mf.read(run.manifest_path)
    assert m.undo_started and not m.undo_ended
    assert any(o.undo == "pending" for o in m.ops)

    res = undo(run.manifest_path)  # η επόμενη αναίρεση ανακτά πρώτα και συνεχίζει
    assert res.recovered_notes
    assert snap(tree) == before
    final = mf.read(run.manifest_path)
    assert final.undo_ended and all(o.undo == "undone" for o in final.ops if o.state == "done")


def test_crashed_move_run_can_still_be_undone(tree, mdir):
    before = snap(tree)
    r = subprocess.run([sys.executable, str(TESTS / "crash_runner.py"), str(tree), str(mdir), "after", "2"],
                       capture_output=True, text=True)
    assert r.returncode == 9
    (path,) = mf.list_manifests(mdir)
    res = undo(path)
    assert res.restored == 2 and snap(tree) == before


# ---------- λίστα εκτελέσεων, δύο εκτελέσεις ----------

def test_two_runs_undo_independently(tree, mdir):
    base = snap(tree)
    a = do_move(tree, mdir, remove=[1])  # η 1η γραμμή μένει εκτός
    after_a = snap(tree)
    b = do_move(tree, mdir)
    assert b.moved >= 1
    undo(b.manifest_path)
    assert snap(tree) == after_a  # η Β αναιρέθηκε, η Α παραμένει
    undo(a.manifest_path)
    assert snap(tree) == base


def test_list_runs_labels_newest_first(tree, mdir):
    a = do_move(tree, mdir)
    write(tree / "p2.jpg", b"p2")
    b = do_move(tree, mdir)
    runs = list_runs(mdir)
    assert [r.run_id for r in runs] == [b.run_id, a.run_id]
    assert runs[0].label == "εφαρμόστηκε"
    undo(b.manifest_path)
    assert list_runs(mdir)[0].label == "αναιρέθηκε" and list_runs(mdir)[0].remaining == 0


def test_corrupt_manifest_does_not_break_listing(tree, mdir):
    run = do_move(tree, mdir)
    (mdir / "00-broken.jsonl").write_text("not json\nmore\n", encoding="utf-8")
    assert [r.run_id for r in list_runs(mdir)] == [run.run_id]


def test_undo_when_root_is_gone(tree, mdir, tmp_path):
    run = do_move(tree, mdir)
    tree.rename(tmp_path / "moved-away")
    with pytest.raises(ValueError, match="δεν υπάρχει πια"):
        undo(run.manifest_path)


def test_nothing_is_ever_deleted_by_undo(tree, mdir):
    run = do_move(tree, mdir)
    write(tree / "Έγγραφα" / "Φορολογικά" / "extra.txt", b"x")
    n_before = sum(1 for p in tree.rglob("*") if p.is_file())
    undo(run.manifest_path)
    assert sum(1 for p in tree.rglob("*") if p.is_file()) == n_before


# ---------- CLI ----------

@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    return tmp_path / "cfg" / "manifests"


def test_cli_undo_list_and_refusal_without_tty(tree, cli_env, capsys):
    assert main(["undo"]) == 0 and "Δεν υπάρχουν" in capsys.readouterr().out
    assert main(["move", str(tree), "--yes"]) == 0
    capsys.readouterr()
    moved = snap(tree)
    assert main(["undo"]) == 0 and "εφαρμόστηκε" in capsys.readouterr().out
    assert main(["undo", "--last"]) == 1  # χωρίς τερματικό και χωρίς --yes
    assert snap(tree) == moved and "έγκριση" in capsys.readouterr().err


def test_cli_undo_interactive_decline_then_accept(tree, cli_env, monkeypatch):
    before = snap(tree)
    assert main(["move", str(tree), "--yes"]) == 0
    moved = snap(tree)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "όχι")
    assert main(["undo", "--last"]) == 1 and snap(tree) == moved
    monkeypatch.setattr("builtins.input", lambda prompt="": "ΝΑΙ")
    assert main(["undo", "--last"]) == 0 and snap(tree) == before


def test_cli_undo_unknown_run_and_nothing_left(tree, cli_env, capsys):
    assert main(["undo", "nope", "--yes"]) == 2
    assert main(["move", str(tree), "--yes"]) == 0
    assert main(["undo", "--last", "--yes"]) == 0
    capsys.readouterr()
    assert main(["undo", "--last", "--yes"]) == 0
    assert "Δεν υπάρχει εκτέλεση" in capsys.readouterr().out


def test_run_ids_sort_chronologically_even_within_one_second(tmp_path, monkeypatch):
    """Δύο εκτελέσεις μέσα στο ίδιο δευτερόλεπτο: το --last πρέπει να βρίσκει τη νεότερη."""
    from datetime import datetime as real_dt
    ids = []
    for micro in (100, 200, 300):
        class Fake(real_dt):
            @classmethod
            def now(cls, tz=None):
                return real_dt(2026, 10, 31, 12, 0, 0, micro)
        monkeypatch.setattr(mf, "datetime", Fake)
        with mf.ManifestWriter(tmp_path / "m", tmp_path, "t") as w:
            ids.append(w.run_id)
    assert sorted(ids) == ids
