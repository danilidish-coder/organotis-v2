"""Μάθηση από τις διορθώσεις, από άκρη σε άκρη: γραμμή εντολών, αρχείο ρυθμίσεων, επανεκκίνηση, αναίρεση."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from organotis import config as cfgmod
from organotis.cli import main
from organotis.approval import PreviewModel

from helpers import make_docx, write

TAX = "Εκκαθαριστικό σημείωμα ΑΦΜ 2023 2023"


def snap(root: Path):
    return sorted((str(p.relative_to(root)), p.read_bytes() if p.is_file() else None) for p in root.rglob("*"))


def cli(env, *args, input_=None):
    return subprocess.run([sys.executable, "-m", "organotis", *args], env=env, input=input_,
                          capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def cfgp(tmp_path):
    return tmp_path / "cfg" / "c.json"


@pytest.fixture
def env(cfgp):
    return {**os.environ, cfgmod.ENV_CONFIG: str(cfgp), "PYTHONUTF8": "1"}


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    make_docx(d / "alpha.docx", TAX)
    make_docx(d / "beta.docx", TAX + " δεύτερο έγγραφο")  # διαφορετικό περιεχόμενο: όχι διπλότυπο
    write(d / "IMG_20230514_1.jpg", b"jpg")
    return d


def _plan(root):
    from organotis.planner import build_plan
    from organotis.scanner import build_report, scan
    cfg = cfgmod.Config()
    res = scan(root, cfg)
    return build_plan(res, build_report(res), cfg)


def save_rules(cfgp, *rules):
    cfgmod.save(cfgmod.Config(learned_rules=list(rules)), cfgp)


def r(id_, wt, wv, tt, tv=None):
    then = {"type": tt} if tv is None else {"type": tt, "value": tv}
    return {"id": id_, "when": {"type": wt, "value": wv}, "then": then, "created": "2026-10-31"}


def test_learned_rules_apply_move_and_undo_after_restart(data, env, cfgp):
    """Κανόνες από το αρχείο ρυθμίσεων → μετακίνηση → αναίρεση σε άλλη διεργασία → όλα όπως πριν."""
    save_rules(cfgp, r(1, "name_word", "alpha", "keep"), r(2, "name_word", "beta", "topic", "Λογαριασμοί"))
    before = snap(data)
    mv = cli(env, "move", str(data), "--yes")
    assert mv.returncode == 0, mv.stderr + mv.stdout
    assert "κανόνας σου #2" in mv.stdout
    assert (data / "alpha.docx").exists()                                             # «μένει»
    assert (data / "Έγγραφα" / "Λογαριασμοί" / "2023" / "beta.docx").exists()       # «θέμα»
    assert (data / "Εικόνες" / "2023" / "2023-05" / "IMG_20230514_1.jpg").exists()   # το υπόλοιπο, αυτόματα
    u = cli(env, "undo", "--last", "--yes")
    assert u.returncode == 0, u.stdout + u.stderr
    assert snap(data) == before


def test_rules_command_lists_and_removes(env, cfgp):
    assert "Δεν έχει μάθει" in cli(env, "rules").stdout
    save_rules(cfgp, r(1, "name_word", "ΔΕΗ", "keep"), r(2, "source_folder", "Παλιά", "topic", "Υγεία"))
    out = cli(env, "rules").stdout
    assert "#1: Όταν το όνομα του αρχείου περιέχει τη λέξη «ΔΕΗ»" in out and "θέμα «Υγεία»" in out
    rm = cli(env, "rules", "remove", "1")
    assert rm.returncode == 0 and "Διαγράφηκε" in rm.stdout
    assert [x["id"] for x in cfgmod.load(cfgp).learned_rules] == [2]
    assert cli(env, "rules", "remove", "9").returncode == 2
    assert cli(env, "rules", "remove").returncode == 2


def test_deleting_a_rule_restores_automatic_behaviour(data, env, cfgp):
    save_rules(cfgp, r(1, "name_word", "alpha", "keep"))
    cli(env, "rules", "remove", "1")
    assert cli(env, "move", str(data), "--yes").returncode == 0
    assert (data / "Έγγραφα" / "Φορολογικά" / "2023" / "alpha.docx").exists()


def test_interactive_removal_offers_rule_and_saves_it(data, env, cfgp, monkeypatch):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(cfgp))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    n = next(row.number for row in PreviewModel(_plan(data)).rows if row.proposal.entry.path.name == "alpha.docx")
    answers = iter([str(n), "1", "", "ΝΑΙ"])  # αφαίρεση γραμμής, επιλογή 1ης πρότασης κανόνα, τέλος, έγκριση
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": (prompts.append(prompt), next(answers))[1])
    assert main(["move", str(data)]) == 0
    saved = cfgmod.load(cfgp).learned_rules
    assert len(saved) == 1 and saved[0]["then"] == {"type": "keep"} and saved[0]["when"]["type"] == "name_word"
    assert any("Γράψε αριθμό" in p for p in prompts)
    assert (data / "alpha.docx").exists()  # αφαιρέθηκε και δεν μετακινήθηκε


def test_interactive_decline_saves_nothing(data, env, cfgp, monkeypatch):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(cfgp))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    n = next(row.number for row in PreviewModel(_plan(data)).rows if row.proposal.entry.path.name == "alpha.docx")
    answers = iter([str(n), "", "", "ΝΑΙ"])  # αφαίρεση, «όχι» στον κανόνα, τέλος, έγκριση
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert main(["move", str(data)]) == 0
    assert not cfgp.exists() or cfgmod.load(cfgp).learned_rules == []


def test_rules_file_stays_valid_json_after_learning(data, cfgp, monkeypatch):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(cfgp))
    save_rules(cfgp, r(1, "name_word", "a", "keep"))
    raw = json.loads(cfgp.read_text(encoding="utf-8"))
    assert raw["learned_rules"][0]["when"] == {"type": "name_word", "value": "a"}
    assert raw["schema_version"] == 1
