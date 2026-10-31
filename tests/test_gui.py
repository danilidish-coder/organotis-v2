import os
import sys
import time
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from organotis import config as cfgmod
from organotis.approval import PreviewModel
from organotis.mover import execute
from organotis.planner import build_plan
from organotis.scanner import build_report, scan

from helpers import make_docx, make_pdf, write

if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("δεν υπάρχει οθόνη (χρησιμοποίησε xvfb-run)", allow_module_level=True)

from organotis import gui  # noqa: E402


def _new_tk():
    """Τα Windows runners του CI αποτυγχάνουν σπάνια να φορτώσουν το init.tcl· δοκιμάζουμε ξανά."""
    last = None
    for _ in range(4):
        try:
            return tk.Tk()
        except tk.TclError as exc:
            last = exc
            time.sleep(1)
    raise last


@pytest.fixture
def app(tmp_path):
    root = _new_tk()
    cfgp = tmp_path / "cfg" / "c.json"
    a = gui.App(root, cfgp)
    # Τα παράθυρα διαλόγου είναι «modal»: στις δοκιμές απαντάμε με κώδικα. Προεπιλογή: «όχι, μόνο αυτή τη φορά».
    a.ask_rule_choice = lambda question, conds: None
    yield a
    root.destroy()


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "data"
    make_docx(d / "scan1.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")
    write(d / "a.pdf", b"dupdup"); write(d / "old" / "b.pdf", b"dupdup")
    write(d / "IMG_20230514_1.jpg", b"jpg")
    return d


@pytest.fixture
def mixed(tmp_path):
    d = tmp_path / "mixed"
    make_docx(d / "alpha.docx", "Εκκαθαριστικό ΑΦΜ 2023 2023")        # σίγουρη
    make_docx(d / "bank_statement.docx", "IBAN 2022 2022")             # να τη δεις (1 λέξη)
    write(d / "IMG_20230514_1.jpg", b"1")                              # σίγουρη
    write(d / "tool.exe", b"MZ")                                       # να τη δεις
    return d


def do_scan(app, d):
    res = scan(d, app.cfg)
    rep = build_report(res)
    app.load_plan(build_plan(res, rep, app.cfg), res, rep)  # όπως η πραγματική σάρωση: χωρίς παλιές αλλαγές «μίας φοράς»


def plan_of(app, d):
    res = scan(d, app.cfg)
    return build_plan(res, build_report(res), app.cfg)


def row(app, name):
    return next(i for i in app.tree.get_children() if app.tree.set(i, "src").endswith(name))


def names(app):
    return sorted(Path(app.tree.set(i, "src")).name for i in app.tree.get_children())


def pump(app, until, timeout=10):
    end = time.time() + timeout
    while time.time() < end and not until():
        app.root.update()
        time.sleep(0.01)
    assert until(), "το παράθυρο δεν ολοκλήρωσε εγκαίρως"


def yes(app, monkeypatch):
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    shown = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    return shown


def snap(root: Path):
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))


# ---------- πίνακας, βεβαιότητα, έγκριση ----------

def test_table_shows_confidence_status_and_visible_reason(app, mixed):
    do_scan(app, mixed)
    ids = app.tree.get_children()
    assert len(ids) == 4
    conf = {Path(app.tree.set(i, "src")).name: app.tree.set(i, "conf") for i in ids}
    assert conf == {"alpha.docx": "Σίγουρη", "IMG_20230514_1.jpg": "Σίγουρη",
                    "bank_statement.docx": "Να τη δεις", "tool.exe": "Να τη δεις"}
    assert {app.tree.set(i, "status") for i in ids} == {"Σε αναμονή"}  # τίποτα δεν είναι εγκεκριμένο εξαρχής
    assert all(app.tree.set(i, "reason").startswith("[") for i in ids)
    app.tree.selection_set(row(app, "bank_statement.docx")); app.root.update()
    detail = app.detail.get("1.0", "end")
    assert "Βεβαιότητα: ΝΑ ΤΗ ΔΕΙΣ, επειδή βρέθηκε μόνο 1 λέξη" in detail and "Λόγος" in detail
    assert str(app.btn_go.cget("state")) == "disabled"  # πρέπει πρώτα να εγκρίνεις κάτι


def test_approve_all_sure_button_approves_only_sure_rows(app, mixed):
    do_scan(app, mixed)
    assert "(2)" in app.btn_sure.cget("text")
    app.approve_all_sure()
    status = {Path(app.tree.set(i, "src")).name: app.tree.set(i, "status") for i in app.tree.get_children()}
    assert status == {"alpha.docx": "Εγκεκριμένη", "IMG_20230514_1.jpg": "Εγκεκριμένη",
                      "bank_statement.docx": "Σε αναμονή", "tool.exe": "Σε αναμονή"}
    assert "(2)" in app.btn_go.cget("text") and str(app.btn_go.cget("state")) == "normal"
    assert "2 σίγουρες" in app.status.get() or "Εγκρίθηκαν 2" in app.status.get()


def test_approve_selected_and_restore_do_not_auto_approve(app, mixed):
    do_scan(app, mixed)
    exe = row(app, "tool.exe")
    app.tree.selection_set(exe); app.approve_selected_rows()
    assert app.tree.set(row(app, "tool.exe"), "status") == "Εγκεκριμένη"
    app.tree.selection_set(row(app, "tool.exe")); app.remove_selected()
    assert app.tree.set(row(app, "tool.exe"), "status") == "Αφαιρέθηκε"
    app.tree.selection_set(row(app, "tool.exe")); app.restore_selected()
    assert app.tree.set(row(app, "tool.exe"), "status") == "Σε αναμονή"


def test_decline_confirmation_moves_nothing(app, tree, monkeypatch):
    do_scan(app, tree); app.approve_all_sure()
    before = snap(tree)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.confirm_and_move(); app.root.update()
    assert snap(tree) == before


def test_only_approved_rows_move_and_check_rows_stay(app, mixed, monkeypatch):
    do_scan(app, mixed); app.approve_all_sure()
    shown = yes(app, monkeypatch)
    app.confirm_and_move()
    pump(app, lambda: bool(shown))
    assert (mixed / "Έγγραφα" / "Φορολογικά" / "2023" / "alpha.docx").exists()
    assert (mixed / "Εικόνες" / "2023" / "2023-05" / "IMG_20230514_1.jpg").exists()
    assert (mixed / "bank_statement.docx").exists() and (mixed / "tool.exe").exists()  # «να τη δεις»: δεν κουνήθηκαν
    assert list((app.config_path.parent / "manifests").glob("*.jsonl"))
    assert not app.busy and app.model is None
    assert "Μετακινήθηκαν 2" in shown[0][1]


def test_confirmation_mentions_manually_approved_check_rows(app, mixed, monkeypatch):
    do_scan(app, mixed); app.approve_all_sure()
    app.tree.selection_set(row(app, "tool.exe")); app.approve_selected_rows()
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda t, msg, **k: (asked.append(msg), False)[1])
    app.confirm_and_move()
    assert "3 αρχεία" in asked[0] and "1 ήταν «να τα δεις»" in asked[0]


def test_scan_button_runs_in_background(app, tree):
    app.folder.set(str(tree))
    app.start_scan()
    pump(app, lambda: app.model is not None)
    assert len(app.model.rows) == 3 and not app.busy and app.scan_result is not None


# ---------- μάθηση από τις διορθώσεις ----------

def test_removing_a_row_offers_rule_and_saves_it_locally(app, mixed):
    do_scan(app, mixed)
    asked = []

    def choose(question, conds):
        asked.append((question, [c.label for c in conds]))
        return next(c for c in conds if c.type == "name_word")

    app.ask_rule_choice = choose
    app.tree.selection_set(row(app, "bank_statement.docx")); app.remove_selected()
    assert "bank_statement.docx" in asked[0][0] and any("«bank»" in l for l in asked[0][1])
    saved = cfgmod.load(app.config_path).learned_rules       # στον δίσκο, όχι μόνο στη μνήμη
    assert len(saved) == 1 and saved[0]["then"] == {"type": "keep"} and saved[0]["id"] == 1
    assert "bank_statement.docx" not in names(app)            # ξαναχτίστηκε η πρόταση: τώρα «μένει»
    assert "Νέος κανόνας" in app.status.get()


def test_declining_the_rule_saves_nothing(app, mixed):
    do_scan(app, mixed)
    app.tree.selection_set(row(app, "bank_statement.docx")); app.remove_selected()
    assert not app.config_path.exists() or cfgmod.load(app.config_path).learned_rules == []
    assert app.tree.set(row(app, "bank_statement.docx"), "status") == "Αφαιρέθηκε"


def test_skip_all_stops_further_questions(app, mixed):
    do_scan(app, mixed)
    calls = []
    app.ask_rule_choice = lambda q, c: (calls.append(q), gui.SKIP_ALL)[1]
    app.tree.selection_set([row(app, "bank_statement.docx"), row(app, "alpha.docx")]); app.remove_selected()
    assert len(calls) == 1
    assert not app.config_path.exists() or cfgmod.load(app.config_path).learned_rules == []


def test_no_question_when_name_has_no_useful_word(app, tree):
    do_scan(app, tree)  # «scan1.docx», «IMG_…»: λέξεις που δεν αξίζουν κανόνα
    calls = []
    app.ask_rule_choice = lambda q, c: (calls.append(q), None)[1]
    app.tree.selection_set(row(app, "scan1.docx")); app.remove_selected()
    assert calls == []


def test_duplicate_rule_is_not_saved_twice(app, mixed):
    do_scan(app, mixed)
    cond = gui.learning.Condition("name_word", "bank", "x")
    assert app.add_rule(cond, {"type": "keep"}) is True
    assert app.add_rule(cond, {"type": "keep"}) is False
    assert len(cfgmod.load(app.config_path).learned_rules) == 1


def test_change_topic_with_rule_moves_file_to_new_topic_and_persists(app, mixed, monkeypatch):
    do_scan(app, mixed)
    app.ask_topic = lambda: "Αυτοκίνητο"
    app.ask_rule_choice = lambda q, conds: next(c for c in conds if c.type == "name_word")
    app.tree.selection_set(row(app, "bank_statement.docx")); app.change_topic()
    r = cfgmod.load(app.config_path).learned_rules
    assert len(r) == 1 and r[0]["then"] == {"type": "topic", "value": "Αυτοκίνητο"}
    i = row(app, "bank_statement.docx")
    assert "Αυτοκίνητο" in app.tree.set(i, "dest") and app.tree.set(i, "conf") == "Σίγουρη"
    assert "κανόνας σου #1" in app.tree.set(i, "reason")
    app.approve_all_sure()
    shown = yes(app, monkeypatch); app.confirm_and_move(); pump(app, lambda: bool(shown))
    assert (mixed / "Έγγραφα" / "Αυτοκίνητο" / "2022" / "bank_statement.docx").exists()


def test_change_topic_one_time_is_not_saved_and_next_scan_forgets_it(app, mixed):
    do_scan(app, mixed)
    app.ask_topic = lambda: "Υγεία"
    app.tree.selection_set(row(app, "bank_statement.docx")); app.change_topic()   # ask_rule_choice → None
    assert not app.config_path.exists() or cfgmod.load(app.config_path).learned_rules == []
    i = row(app, "bank_statement.docx")
    assert "Υγεία" in app.tree.set(i, "dest") and "μόνο για αυτή τη φορά" in app.tree.set(i, "reason")
    app.folder.set(str(mixed)); app.start_scan()   # το πραγματικό κουμπί: νέα σάρωση = νέα αρχή
    pump(app, lambda: app.model is not None and not app.busy and app.overrides == [])
    assert "Υγεία" not in app.tree.set(row(app, "bank_statement.docx"), "dest")


def test_change_topic_keeps_other_approvals(app, mixed):
    do_scan(app, mixed); app.approve_all_sure()
    app.ask_topic = lambda: "Υγεία"
    app.tree.selection_set(row(app, "bank_statement.docx")); app.change_topic()
    assert app.tree.set(row(app, "alpha.docx"), "status") == "Εγκεκριμένη"  # η κατάσταση δεν χάθηκε από το ξαναχτίσιμο


def test_change_topic_needs_one_document(app, mixed, monkeypatch):
    do_scan(app, mixed)
    info = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: info.append(a[1]))
    asked = []
    app.ask_topic = lambda: (asked.append(1), "X")[1]
    app.tree.selection_set(row(app, "tool.exe")); app.change_topic()
    app.tree.selection_set([row(app, "alpha.docx"), row(app, "bank_statement.docx")]); app.change_topic()
    app.tree.selection_set(()); app.change_topic()
    assert asked == [] and len(info) == 3 and "μόνο στα έγγραφα" in info[0]


def test_rules_window_lists_and_deletes_rule(app, mixed, monkeypatch):
    do_scan(app, mixed)
    app.add_rule(gui.learning.Condition("name_word", "bank", "x"), {"type": "keep"})
    app.replan()
    assert "bank_statement.docx" not in names(app)
    app.open_rules()
    assert "#1: Όταν το όνομα του αρχείου περιέχει τη λέξη «bank»" in app.rules_list.get(0)
    app.rules_list.selection_set(0)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
    app.delete_selected_rule()
    assert cfgmod.load(app.config_path).learned_rules == []
    assert "bank_statement.docx" in names(app)       # ξανάγινε αυτόματη πρόταση
    assert "δεν έχει μάθει" in app.rules_list.get(0)


def test_rules_window_decline_keeps_rule(app, mixed, monkeypatch):
    do_scan(app, mixed)
    app.add_rule(gui.learning.Condition("name_word", "bank", "x"), {"type": "keep"})
    app.open_rules(); app.rules_list.selection_set(0)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.delete_selected_rule()
    assert len(cfgmod.load(app.config_path).learned_rules) == 1


def test_learned_rule_is_still_there_after_restarting_the_program(app, mixed, tmp_path):
    do_scan(app, mixed)
    app.add_rule(gui.learning.Condition("name_word", "bank", "x"), {"type": "keep"})
    root2 = _new_tk()                      # «νέα εκτέλεση»: καινούργιο παράθυρο, ίδιο αρχείο ρυθμίσεων
    try:
        app2 = gui.App(root2, app.config_path)
        do_scan(app2, mixed)
        assert "bank_statement.docx" not in names(app2) and len(app2.cfg.learned_rules) == 1
    finally:
        root2.destroy()


# ---------- αναίρεση (και μετά από «επανεκκίνηση») ----------

def test_undo_after_learning_flow_restores_everything_even_in_a_new_window(app, mixed, monkeypatch):
    before = snap(mixed)
    do_scan(app, mixed)
    app.ask_topic = lambda: "Υγεία"
    app.ask_rule_choice = lambda q, conds: next(c for c in conds if c.type == "name_word")
    app.tree.selection_set(row(app, "bank_statement.docx")); app.change_topic()
    app.approve_all_sure()
    shown = yes(app, monkeypatch); app.confirm_and_move(); pump(app, lambda: bool(shown))
    assert snap(mixed) != before
    root2 = _new_tk()                      # επανεκκίνηση: το παράθυρο κλείνει και ανοίγει ξανά
    try:
        app2 = gui.App(root2, app.config_path)
        app2.open_undo()
        assert app2.undo_list.size() == 1 and "εφαρμόστηκε" in app2.undo_list.get(0)
        app2.undo_list.selection_set(0)
        monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
        done = []
        monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: done.append(a))
        app2.confirm_and_undo()
        pump(app2, lambda: bool(done))
        assert snap(mixed) == before
    finally:
        root2.destroy()


def test_undo_dialog_restores_after_confirmation(app, tree, monkeypatch):
    before = snap(tree)
    execute(PreviewModel(plan_of(app, tree)).approve(), app.cfg.manifest_path(app.config_path))
    app.open_undo()
    assert app.undo_list.size() == 1 and "εφαρμόστηκε" in app.undo_list.get(0)
    app.undo_list.selection_set(0)
    shown = yes(app, monkeypatch)
    app.confirm_and_undo()
    pump(app, lambda: bool(shown))
    assert snap(tree) == before and not app.busy
    assert "Επέστρεψαν 3" in shown[0][1]


def test_undo_dialog_decline_changes_nothing(app, tree, monkeypatch):
    execute(PreviewModel(plan_of(app, tree)).approve(), app.cfg.manifest_path(app.config_path))
    moved = snap(tree)
    app.open_undo(); app.undo_list.selection_set(0)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.confirm_and_undo(); app.root.update()
    assert snap(tree) == moved
