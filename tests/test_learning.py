"""Λειτουργία 1 της v0.12: το πρόγραμμα μαθαίνει από τις διορθώσεις (μόνο τοπικά)."""
import json
import re
from datetime import date
from pathlib import Path

import pytest

from organotis import config as cfgmod, learning
from organotis.config import Config
from organotis.learning import Condition, suggest_conditions
from organotis.planner import CHECK, KEEP, MOVE, REVIEW, SURE, build_plan
from organotis.scanner import build_report, scan

from helpers import make_docx, write

TAX = "Εκκαθαριστικό σημείωμα ΑΦΜ 2023 2023"
TODAY = date(2026, 10, 31)


def rule(id_, when_type, when_value, then_type, then_value=None):
    then = {"type": then_type} if then_value is None else {"type": then_type, "value": then_value}
    return {"id": id_, "when": {"type": when_type, "value": when_value}, "then": then, "created": "2026-10-31"}


def plan_for(root, rules=(), extra=()):
    cfg = Config(learned_rules=list(rules))
    res = scan(root, cfg)
    return build_plan(res, build_report(res), cfg, today=TODAY, extra_rules=extra)


def prop(plan, name):
    return next(p for p in plan.proposals if p.entry.path.name == name)


# ---------- προτάσεις «ΟΤΑΝ» (μόνο από ονόματα) ----------

def test_suggestions_come_from_names_not_content(tmp_path):
    f = make_docx(tmp_path / "Παλιά" / "Δήλωση_ΔΕΗ_2023.docx", "μυστικο περιεχομενο ΑΦΜ 123456789")
    conds = suggest_conditions(f, tmp_path)
    labels = " | ".join(c.label for c in conds)
    assert "«Δήλωση»" in labels and "«ΔΕΗ»" in labels and "«Παλιά»" in labels
    assert "2023" not in labels and "μυστικο" not in labels and "123456789" not in labels


def test_suggestions_skip_short_digits_stopwords_and_limit(tmp_path):
    f = tmp_path / "ab_scan_12345_final_alpha_beta_gamma_delta_epsilon.pdf"
    words = [c.value for c in suggest_conditions(f, tmp_path) if c.type == "name_word"]
    assert len(words) == 4 and "ab" not in words and "scan" not in words and "final" not in words
    assert not any(w.isdigit() for w in words)


def test_no_folder_suggestion_for_files_in_root(tmp_path):
    assert all(c.type != "source_folder" for c in suggest_conditions(tmp_path / "abc def.pdf", tmp_path))


# ---------- ταίριασμα ----------

def test_name_word_is_whole_word_and_accent_insensitive(tmp_path):
    r = rule(1, "name_word", "Εκκαθαριστικό", "keep")
    assert learning.matches(r, tmp_path / "εκκαθαριστικο_2023.pdf", tmp_path, "pdf")
    assert learning.matches(r, tmp_path / "ΕΚΚΑΘΑΡΙΣΤΙΚΟ 2023.pdf", tmp_path, "pdf")
    assert not learning.matches(r, tmp_path / "μεεκκαθαριστικοπροτασης.pdf", tmp_path, "pdf")


def test_source_folder_matches_only_below_root(tmp_path):
    r = rule(1, "source_folder", "Παλιά", "keep")
    assert learning.matches(r, tmp_path / "Παλιά" / "a.pdf", tmp_path, "pdf")
    assert not learning.matches(r, tmp_path / "Νέα" / "a.pdf", tmp_path, "pdf")
    assert not learning.matches(r, tmp_path / "a.pdf", tmp_path / "Παλιά", "pdf")  # το ίδιο το root δεν μετράει


def test_topic_rule_does_not_apply_to_non_documents(tmp_path):
    r = rule(1, "name_word", "foto", "topic", "Υγεία")
    assert not learning.matches(r, tmp_path / "foto.jpg", tmp_path, "image")
    assert learning.matches(r, tmp_path / "foto.pdf", tmp_path, "pdf")


# ---------- εφαρμογή στην πρόταση ----------

def test_keep_rule_leaves_file_and_shows_reason(tmp_path):
    make_docx(tmp_path / "scan1.docx", TAX)
    p = prop(plan_for(tmp_path, [rule(1, "name_word", "scan1", "keep")]), "scan1.docx")
    assert p.action == KEEP and p.learned_id == 1 and p.confidence == SURE
    assert "ο κανόνας σου #1: όταν το όνομα του αρχείου περιέχει τη λέξη «scan1»" in p.reason
    assert "τότε το αρχείο μένει στη θέση του" in p.reason


def test_without_rule_the_same_file_moves(tmp_path):
    make_docx(tmp_path / "scan1.docx", TAX)
    assert prop(plan_for(tmp_path), "scan1.docx").action == MOVE


def test_keep_rule_by_folder(tmp_path):
    make_docx(tmp_path / "Παλιά" / "a.docx", TAX)
    make_docx(tmp_path / "Νέα" / "b.docx", TAX)
    plan = plan_for(tmp_path, [rule(1, "source_folder", "Παλιά", "keep")])
    assert prop(plan, "a.docx").action == KEEP and prop(plan, "b.docx").action == MOVE


def test_topic_rule_overrides_automatic_topic_and_keeps_year(tmp_path):
    make_docx(tmp_path / "scan1.docx", TAX)
    p = prop(plan_for(tmp_path, [rule(3, "name_word", "scan1", "topic", "Λογαριασμοί")]), "scan1.docx")
    assert p.action == MOVE and p.dest == tmp_path / "Έγγραφα" / "Λογαριασμοί" / "2023" / "scan1.docx"
    assert p.learned_id == 3 and p.confidence == SURE and "#3" in p.reason and p.rule == "μαθημένος κανόνας #3"


def test_topic_rule_can_name_a_brand_new_topic(tmp_path):
    make_docx(tmp_path / "a.docx", "κείμενο χωρίς λέξεις θέματος 2024 2024")
    p = prop(plan_for(tmp_path, [rule(1, "name_word", "a", "topic", "Αυτοκίνητο")]), "a.docx")
    assert p.dest == tmp_path / "Έγγραφα" / "Αυτοκίνητο" / "2024" / "a.docx"


def test_conflicting_rules_apply_none_and_say_so(tmp_path):
    make_docx(tmp_path / "scan1.docx", TAX)
    rules = [rule(1, "name_word", "scan1", "keep"), rule(2, "name_word", "scan1", "topic", "Υγεία")]
    p = prop(plan_for(tmp_path, rules), "scan1.docx")
    assert p.action == MOVE and p.learned_id is None and p.confidence == CHECK  # αυτόματη πρόταση, «να τη δεις»
    assert "#1, #2" in p.reason and "δεν εφαρμόστηκε κανένας" in p.confidence_why
    assert p.dest.parent.parent.name == "Φορολογικά"  # αυτόματη, όχι «Υγεία»


def test_two_rules_with_same_result_are_not_a_conflict(tmp_path):
    make_docx(tmp_path / "Παλιά" / "scan1.docx", TAX)
    rules = [rule(1, "name_word", "scan1", "keep"), rule(2, "source_folder", "Παλιά", "keep")]
    p = prop(plan_for(tmp_path, rules), "scan1.docx")
    assert p.action == KEEP and p.learned_id == 1


def test_duplicates_rules_precedence(tmp_path):
    write(tmp_path / "a.pdf", b"dup"); write(tmp_path / "x" / "copy1.pdf", b"dup")
    write(tmp_path / "y" / "copy2.pdf", b"dup")
    rules = [rule(1, "name_word", "copy1", "keep"), rule(2, "name_word", "copy2", "topic", "Υγεία")]
    plan = plan_for(tmp_path, rules)
    assert prop(plan, "copy1.pdf").action == KEEP      # «μένει» νικά το διπλότυπο
    assert prop(plan, "copy2.pdf").action == REVIEW    # το «θέμα» δεν παρακάμπτει τον φάκελο ελέγχου


def test_one_time_override_is_not_saved_anywhere(tmp_path):
    make_docx(tmp_path / "scan1.docx", TAX)
    once = learning.one_time_rule(tmp_path / "scan1.docx", {"type": "topic", "value": "Υγεία"}, 1)
    cfg = Config()
    res = scan(tmp_path, cfg)
    p = prop(build_plan(res, build_report(res), cfg, today=TODAY, extra_rules=[once]), "scan1.docx")
    assert p.dest.parent.parent.name == "Υγεία" and "μόνο για αυτή τη φορά" in p.reason
    assert p.rule == "αλλαγή σου (μία φορά)" and cfg.learned_rules == []
    assert prop(plan_for(tmp_path), "scan1.docx").dest.parent.parent.name == "Φορολογικά"  # επόμενη φορά: αυτόματο


def test_rule_applies_to_every_matching_file(tmp_path):
    for i in range(3):
        make_docx(tmp_path / f"ΔΕΗ_{i}.docx", TAX)
    plan = plan_for(tmp_path, [rule(1, "name_word", "ΔΕΗ", "keep")])
    assert [p.action for p in plan.proposals] == [KEEP] * 3


# ---------- ρυθμίσεις: αποθήκευση, έλεγχος, επανεκκίνηση ----------

def test_rules_survive_save_and_reload(tmp_path):
    cfgp = tmp_path / "c.json"
    cfg = Config(learned_rules=[rule(1, "name_word", "Δήλωση", "keep"), rule(2, "source_folder", "Παλιά", "topic", "Υγεία")])
    cfgmod.save(cfg, cfgp)
    assert cfgmod.load(cfgp).learned_rules == cfg.learned_rules
    assert "Δήλωση" in cfgp.read_text(encoding="utf-8")  # ελληνικά, όχι \u escapes


def test_rule_from_disk_changes_plan_after_restart(tmp_path):
    data = tmp_path / "data"; make_docx(data / "scan1.docx", TAX)
    cfgp = tmp_path / "c.json"
    cfgmod.save(Config(learned_rules=[rule(1, "name_word", "scan1", "keep")]), cfgp)
    cfg = cfgmod.load(cfgp)  # «νέα εκτέλεση του προγράμματος»
    res = scan(data, cfg)
    assert prop(build_plan(res, build_report(res), cfg), "scan1.docx").action == KEEP


@pytest.mark.parametrize("bad", [
    "x", [{"id": 1}], [rule(0, "name_word", "a", "keep")], [rule(1, "name_word", "", "keep")],
    [rule(1, "color", "a", "keep")], [rule(1, "name_word", "a", "delete")],
    [rule(1, "name_word", "a", "topic", "Α/Β")], [rule(1, "name_word", "a", "topic")],
    [rule(1, "name_word", "a", "keep", "x")], [rule(1, "path", "/x", "keep")],
    [rule(1, "name_word", "a", "keep"), rule(1, "name_word", "b", "keep")],
    [{**rule(1, "name_word", "a", "keep"), "extra": 1}],
    [{**rule(True, "name_word", "a", "keep")}],
])
def test_bad_learned_rules_rejected(bad):
    with pytest.raises(cfgmod.ConfigError):
        Config(learned_rules=bad).validate()


def test_describe_rule_is_plain_greek():
    t = learning.describe_rule(rule(2, "name_word", "ΔΕΗ", "topic", "Λογαριασμοί"))
    assert t == ("#2: Όταν το όνομα του αρχείου περιέχει τη λέξη «ΔΕΗ», τότε το έγγραφο πάει στο θέμα "
                 "«Λογαριασμοί» (τον έφτιαξες στις 2026-10-31)")
    assert "μένει στη θέση του" in learning.describe_rule(rule(1, "source_folder", "Παλιά", "keep"))


def test_new_rule_ids_increase_and_duplicates_detected():
    r1 = learning.new_rule([], {"type": "name_word", "value": "a"}, {"type": "keep"}, TODAY)
    r2 = learning.new_rule([r1], {"type": "name_word", "value": "b"}, {"type": "keep"}, TODAY)
    assert (r1["id"], r2["id"]) == (1, 2) and r1["created"] == "2026-10-31"
    assert learning.has_rule([r1], {"type": "name_word", "value": "a"}, {"type": "keep"})
    assert not learning.has_rule([r1], {"type": "name_word", "value": "a"}, {"type": "topic", "value": "X"})


# ---------- ιδιωτικότητα: τίποτα δεν φεύγει, ούτε περιεχόμενο αποθηκεύεται ----------

def test_saved_rule_never_contains_document_content(tmp_path):
    f = make_docx(tmp_path / "Δήλωση.docx", "ΜΥΣΤΙΚΗΛΕΞΗ ΑΦΜ 123456789 2023")
    cond = suggest_conditions(f, tmp_path)[0]
    cfg = Config()
    cfg.learned_rules.append(learning.new_rule([], {"type": cond.type, "value": cond.value}, {"type": "keep"}))
    cfgp = tmp_path / "c.json"
    cfgmod.save(cfg, cfgp)
    saved = cfgp.read_text(encoding="utf-8")
    assert "ΜΥΣΤΙΚΗΛΕΞΗ" not in saved and "123456789" not in saved


def test_source_code_has_no_network_access():
    """Όλα γίνονται τοπικά: ο κώδικας του προγράμματος δεν εισάγει τίποτα για δίκτυο."""
    src = Path(cfgmod.__file__).parent
    banned = re.compile(r"^\s*(?:import|from)\s+(?:socket|urllib|http|ftplib|smtplib|requests|httpx|aiohttp|"
                        r"websocket|xmlrpc|telnetlib|ssl)\b", re.M)
    for f in src.glob("*.py"):
        assert not banned.search(f.read_text(encoding="utf-8")), f.name
