import os
import sys
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from organotis import config as cfgmod
from organotis.planner import build_plan
from organotis.scanner import build_report, scan

from helpers import make_docx, write

if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("δεν υπάρχει οθόνη (χρησιμοποίησε xvfb-run)", allow_module_level=True)

from organotis import gui  # noqa: E402


@pytest.fixture
def app(tmp_path):
    root = tk.Tk()
    cfgp = tmp_path / "cfg" / "c.json"
    a = gui.App(root, cfgp)
    yield a
    root.destroy()


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "data"
    make_docx(d / "scan1.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")
    write(d / "a.pdf", b"dupdup"); write(d / "old" / "b.pdf", b"dupdup")
    write(d / "IMG_20230514_1.jpg", b"jpg")
    return d


def plan_of(app, d):
    res = scan(d, app.cfg)
    return build_plan(res, build_report(res), app.cfg)


def pump(app, until, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not until():
        app.root.update()
        time.sleep(0.01)
    assert until(), "το παράθυρο δεν ολοκλήρωσε εγκαίρως"


def test_table_shows_rows_with_visible_reason(app, tree):
    app.load_plan(plan_of(app, tree))
    ids = app.tree.get_children()
    assert len(ids) == 3
    reasons = [app.tree.set(i, "reason") for i in ids]
    assert all(r.startswith("[") and len(r) > 30 for r in reasons)
    app.tree.selection_set(ids[0]); app.root.update()
    assert "Λόγος" in app.detail.get("1.0", "end")


def test_remove_and_restore_rows(app, tree):
    app.load_plan(plan_of(app, tree))
    ids = app.tree.get_children()
    app.tree.selection_set(ids[:2]); app.remove_selected()
    assert len(app.model.active) == 1
    assert "removed" in app.tree.item(ids[0], "tags")
    assert "(1)" in app.btn_go.cget("text")
    app.tree.selection_set(ids[0]); app.restore_selected()
    assert len(app.model.active) == 2


def test_decline_confirmation_moves_nothing(app, tree, monkeypatch):
    app.load_plan(plan_of(app, tree))
    before = sorted(p.relative_to(tree) for p in tree.rglob("*"))
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.confirm_and_move()
    app.root.update()
    assert sorted(p.relative_to(tree) for p in tree.rglob("*")) == before


def test_approve_moves_only_kept_rows(app, tree, monkeypatch):
    app.load_plan(plan_of(app, tree))
    ids = app.tree.get_children()
    scan_row = next(i for i in ids if app.tree.set(i, "src") == "scan1.docx")
    app.tree.selection_set(scan_row); app.remove_selected()
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    shown = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app.confirm_and_move()
    pump(app, lambda: bool(shown))
    assert (tree / "scan1.docx").exists()
    assert (tree / "Εικόνες" / "2023" / "2023-05" / "IMG_20230514_1.jpg").exists()
    assert list((app.config_path.parent / "manifests").glob("*.jsonl"))
    assert not app.busy and app.model is None


def test_scan_button_runs_in_background(app, tree):
    app.folder.set(str(tree))
    app.start_scan()
    pump(app, lambda: app.model is not None)
    assert len(app.model.rows) == 3 and not app.busy


def test_undo_dialog_restores_after_confirmation(app, tree, monkeypatch):
    from organotis.mover import execute
    from organotis.approval import PreviewModel
    before = sorted((str(p.relative_to(tree)), p.read_bytes() if p.is_file() else None) for p in tree.rglob("*"))
    execute(PreviewModel(plan_of(app, tree)).approve(), app.cfg.manifest_path(app.config_path))
    app.open_undo()
    assert app.undo_list.size() == 1 and "εφαρμόστηκε" in app.undo_list.get(0)
    app.undo_list.selection_set(0)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    shown = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app.confirm_and_undo()
    pump(app, lambda: bool(shown))
    after = sorted((str(p.relative_to(tree)), p.read_bytes() if p.is_file() else None) for p in tree.rglob("*"))
    assert after == before and not app.busy
    assert "Επέστρεψαν 3" in shown[0][1]


def test_undo_dialog_decline_changes_nothing(app, tree, monkeypatch):
    from organotis.mover import execute
    from organotis.approval import PreviewModel
    execute(PreviewModel(plan_of(app, tree)).approve(), app.cfg.manifest_path(app.config_path))
    moved = sorted(p.relative_to(tree) for p in tree.rglob("*"))
    app.open_undo(); app.undo_list.selection_set(0)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.confirm_and_undo(); app.root.update()
    assert sorted(p.relative_to(tree) for p in tree.rglob("*")) == moved


def test_task_checkboxes_drive_the_plan(app, tree):
    assert app.selected_tasks() == ("duplicates", "same_names", "general")
    app.task_vars["general"].set(False)
    app.folder.set(str(tree))
    app.start_scan()
    pump(app, lambda: app.model is not None)
    assert all(r.proposal.rule == "διπλότυπο" or r.proposal.action != "move"
               for r in app.model.rows)
    assert len(app.model.rows) == 1  # μόνο το διπλότυπο


def test_no_task_selected_does_not_scan(app, tree, monkeypatch):
    for v in app.task_vars.values():
        v.set(False)
    warned = []
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **k: warned.append(a))
    app.folder.set(str(tree))
    app.start_scan()
    assert warned and not app.busy


def test_protected_folder_add_saves_config_and_remove(app, tmp_path):
    assert app.add_protected_name("Ενιαίος")
    assert not app.add_protected_name("ΕΝΙΑΙΟΣ")  # ίδιο όνομα (χωρίς τόνους/πεζά)
    assert cfgmod.load(app.config_path).protected_folders == ["Ενιαίος"]
    assert app.prot_list.get(0) == "Ενιαίος"
    app.prot_list.selection_set(0)
    app.remove_protected()
    assert cfgmod.load(app.config_path).protected_folders == []


def test_protected_folder_bad_name_shows_error_and_keeps_config(app, monkeypatch):
    errs = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: errs.append(a))
    assert not app.add_protected_name("a/b")
    assert errs and app.cfg.protected_folders == []
