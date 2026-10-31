"""Κανόνες που μαθαίνει το πρόγραμμα από τις διορθώσεις του χρήστη.

Όλα γίνονται τοπικά. Ένας κανόνας είναι πάντα «όταν ... τότε ...», σε απλά ελληνικά:

  ΟΤΑΝ:  το όνομα του αρχείου περιέχει τη λέξη «X»        (name_word)
         ή το αρχείο βρίσκεται σε φάκελο με όνομα «Y»      (source_folder)
  ΤΟΤΕ:  το αρχείο μένει στη θέση του                      (keep)
         ή το έγγραφο πάει στο θέμα «Θ»                    (topic)

Αποθηκεύονται ΜΟΝΟ λέξεις από ονόματα αρχείων/φακέλων που επιβεβαιώνει ο χρήστης.
Ποτέ περιεχόμενο εγγράφων.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .textutil import name_text, normalize, word_pattern

WHEN_TYPES = ("name_word", "source_folder")
THEN_TYPES = ("keep", "topic")
DOC_KINDS = ("word", "pdf", "excel")  # μόνο αυτά αλλάζουν «θέμα»

# Λέξεις που δεν βοηθούν ως κανόνας.
_STOP = {"copy", "final", "new", "scan", "img", "doc", "file", "αντιγραφο", "νεο", "τελικο"}


@dataclass(frozen=True)
class Condition:
    type: str
    value: str
    label: str  # π.χ. «Το όνομα του αρχείου περιέχει τη λέξη «δεη»»


def suggest_conditions(path: Path, root: Path, limit: int = 4) -> list[Condition]:
    """Προτάσεις «ΟΤΑΝ» για ένα αρχείο, από το ΟΝΟΜΑ και τον φάκελό του (όχι το περιεχόμενο)."""
    out: list[Condition] = []
    seen: set[str] = set()
    words = re.findall(r"[^\W\d_]{3,}", name_text(path.stem))
    for w in sorted(words, key=len, reverse=True):
        n = normalize(w)
        if n in seen or n in _STOP:
            continue
        seen.add(n)
        out.append(Condition("name_word", w, f"Το όνομα του αρχείου περιέχει τη λέξη «{w}»"))
        if len(out) >= limit:
            break
    if path.parent != root and path.parent.name:
        out.append(Condition("source_folder", path.parent.name,
                             f"Το αρχείο βρίσκεται σε φάκελο με όνομα «{path.parent.name}»"))
    return out


def matches(rule: dict, path: Path, root: Path, kind: str) -> bool:
    """Ισχύει ο κανόνας για αυτό το αρχείο;"""
    then = rule["then"]
    if then["type"] == "topic" and kind not in DOC_KINDS:
        return False  # το θέμα αφορά μόνο έγγραφα
    when = rule["when"]
    if when["type"] == "name_word":
        return bool(word_pattern(when["value"]).search(normalize(name_text(path.stem))))
    if when["type"] == "source_folder":
        return path.parent != root and normalize(path.parent.name) == normalize(when["value"])
    if when["type"] == "path":  # μόνο για αλλαγές «αυτή τη φορά», δεν αποθηκεύεται ποτέ
        return str(path) == when["value"]
    return False


def find_matches(rules: list[dict], path: Path, root: Path, kind: str) -> list[dict]:
    return [r for r in rules if matches(r, path, root, kind)]


def describe_when(when: dict) -> str:
    if when["type"] == "path":
        return "το αρχείο είναι ακριβώς αυτό"
    if when["type"] == "name_word":
        return f"το όνομα του αρχείου περιέχει τη λέξη «{when['value']}»"
    return f"το αρχείο βρίσκεται σε φάκελο με όνομα «{when['value']}»"


def describe_then(then: dict) -> str:
    if then["type"] == "keep":
        return "το αρχείο μένει στη θέση του"
    return f"το έγγραφο πάει στο θέμα «{then['value']}»"


def one_time_rule(path: Path, then: dict, serial: int) -> dict:
    """Αλλαγή μόνο για αυτή τη φορά (αρνητικό id, δεν αποθηκεύεται στις ρυθμίσεις)."""
    return {"id": -serial, "when": {"type": "path", "value": str(path)}, "then": dict(then),
            "created": date.today().isoformat()}


def describe_rule_short(rule: dict) -> str:
    """Για τον «λόγο» μιας πρότασης: χωρίς ημερομηνία, με τον αριθμό του κανόνα."""
    return f"κανόνας σου #{rule['id']}: όταν {describe_when(rule['when'])}, τότε {describe_then(rule['then'])}"


def describe_rule(rule: dict) -> str:
    if rule["id"] < 0:
        return f"Αλλαγή σου μόνο για αυτή τη φορά: {describe_then(rule['then'])}"
    return (f"#{rule['id']}: Όταν {describe_when(rule['when'])}, τότε {describe_then(rule['then'])} "
            f"(τον έφτιαξες στις {rule.get('created', '?')})")


def new_rule(existing: list[dict], when: dict, then: dict, today: date | None = None) -> dict:
    next_id = max((r["id"] for r in existing), default=0) + 1
    return {"id": next_id, "when": dict(when), "then": dict(then),
            "created": (today or date.today()).isoformat()}


def has_rule(existing: list[dict], when: dict, then: dict) -> bool:
    return any(r["when"] == when and r["then"] == then for r in existing)


def validate_rule(rule: object, is_simple_name) -> str | None:
    """Επιστρέφει μήνυμα σφάλματος (ελληνικά) ή None αν ο κανόνας είναι έγκυρος."""
    if not isinstance(rule, dict) or set(rule) - {"id", "when", "then", "created"} \
            or not {"id", "when", "then"} <= set(rule):
        return "Κάθε μαθημένος κανόνας έχει «id», «when» και «then»."
    if not isinstance(rule["id"], int) or isinstance(rule["id"], bool) or rule["id"] < 1:
        return "Το «id» του κανόνα πρέπει να είναι θετικός αριθμός."
    w, t = rule["when"], rule["then"]
    if not isinstance(w, dict) or set(w) != {"type", "value"} or w["type"] not in WHEN_TYPES \
            or not isinstance(w["value"], str) or not w["value"].strip():
        return "Μη έγκυρη συνθήκη «when» σε μαθημένο κανόνα."
    if not isinstance(t, dict) or t.get("type") not in THEN_TYPES:
        return "Μη έγκυρη ενέργεια «then» σε μαθημένο κανόνα."
    if t["type"] == "keep" and set(t) != {"type"}:
        return "Η ενέργεια «keep» δεν έχει τιμή."
    if t["type"] == "topic" and (set(t) != {"type", "value"} or not is_simple_name(t["value"])):
        return "Η ενέργεια «topic» χρειάζεται απλό όνομα φακέλου."
    return None
