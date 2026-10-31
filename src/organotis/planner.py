"""Κανόνες και πρόταση δομής. Δεν αγγίζει κανένα αρχείο: επιστρέφει μόνο πρόταση.

Κάθε πρόταση έχει «λόγο» σε απλά ελληνικά (ποιος κανόνας και ποια στοιχεία).
Δεν υπάρχει σκορ: το θέμα προτείνεται μόνο αν ένα θέμα έχει καθαρά
περισσότερες λέξεις-κλειδιά από τα υπόλοιπα, αλλιώς το αρχείο μένει στη θέση του.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import learning
from .config import Config
from .extract import extract
from .scanner import Entry, Report, ScanResult
from .textutil import name_text, normalize, word_pattern  # noqa: F401  (normalize: επανεξαγωγή)

MOVE, REVIEW, KEEP = "move", "review", "keep"
SURE, CHECK = "sure", "check"   # «σίγουρη» / «να τη δεις»
MIN_YEAR = 1990

_YEAR_RE = re.compile(r"(?<!\d)(19[9]\d|20\d\d)(?!\d)")
_IMG_DATE_RE = re.compile(
    r"(?<!\d)(19\d\d|20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)")


@dataclass(frozen=True)
class Proposal:
    entry: Entry
    action: str            # "move" | "review" | "keep"
    dest: Path | None      # πλήρης νέα διαδρομή αρχείου (None στο "keep")
    rule: str              # σύντομο όνομα κανόνα
    reason: str            # πλήρης εξήγηση σε απλά ελληνικά
    confidence: str = CHECK        # "sure" | "check": ποτέ αριθμός/σκορ
    confidence_why: str = ""       # ο κανόνας βεβαιότητας σε απλά ελληνικά
    learned_id: int | None = None  # αριθμός μαθημένου κανόνα, αν προέρχεται από τέτοιον


@dataclass
class Plan:
    root: Path
    proposals: list[Proposal]

    def by_action(self, action: str) -> list[Proposal]:
        return [p for p in self.proposals if p.action == action]


@dataclass(frozen=True)
class TopicResult:
    topic: str | None
    why: str
    hits: int = 0          # πόσες ΔΙΑΦΟΡΕΤΙΚΕΣ λέξεις του θέματος βρέθηκαν
    other_topics: int = 0  # πόσα ΑΛΛΑ θέματα είχαν κάποια λέξη
    words: tuple[str, ...] = ()


def find_topic_detail(cfg: Config, name_text_: str, body_text: str) -> TopicResult:
    name_n, body_n = normalize(name_text_), normalize(body_text)
    hits: dict[str, list[tuple[str, str]]] = {}
    for rule in cfg.topic_rules:
        found: list[tuple[str, str]] = []
        for kw in rule["keywords"]:
            pat = word_pattern(kw)
            if pat.search(name_n):
                found.append((kw, "στο όνομα"))
            elif pat.search(body_n):
                found.append((kw, "στο κείμενο"))
        if found:
            hits[rule["topic"]] = found
    if not hits:
        return TopicResult(None, "δεν βρέθηκε καμία λέξη-κλειδί θέματος")
    best = max(len(v) for v in hits.values())
    top = sorted(t for t, v in hits.items() if len(v) == best)
    if len(top) > 1:
        return TopicResult(None, "ασαφές θέμα, ισοβαθμία ανάμεσα σε: " + ", ".join(f"«{t}»" for t in top))
    topic = top[0]
    words = ", ".join(f"«{kw}» ({where})" for kw, where in hits[topic])
    return TopicResult(topic, f"θέμα «{topic}»: βρέθηκαν οι λέξεις {words}", hits=best,
                       other_topics=len(hits) - 1, words=tuple(kw for kw, _ in hits[topic]))


def find_topic(cfg: Config, name_text_: str, body_text: str) -> tuple[str | None, str]:
    """Επιστρέφει (θέμα ή None, εξήγηση)."""
    r = find_topic_detail(cfg, name_text_, body_text)
    return r.topic, r.why


def find_year(name_text: str, body_text: str, max_year: int) -> tuple[int | None, str]:
    def valid(ys: list[str]) -> list[int]:
        return [int(y) for y in ys if MIN_YEAR <= int(y) <= max_year]

    from_name = sorted(set(valid(_YEAR_RE.findall(name_text))))
    if len(from_name) == 1:
        return from_name[0], f"έτος {from_name[0]}: αναφέρεται στο όνομα του αρχείου"
    if len(from_name) > 1:
        return None, "ασαφές έτος: το όνομα έχει περισσότερα από ένα έτη"
    counts = Counter(valid(_YEAR_RE.findall(body_text)))
    if not counts:
        return None, "δεν βρέθηκε έτος"
    ranked = counts.most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None, "ασαφές έτος: περισσότερα έτη αναφέρονται εξίσου συχνά στο κείμενο"
    y, n = ranked[0]
    return y, f"έτος {y}: αναφέρεται {n} φορές στο κείμενο και είναι το συχνότερο"


def _image_date(entry: Entry) -> tuple[date, str, bool]:
    """(ημερομηνία, εξήγηση, προέρχεται από το όνομα;)"""
    m = _IMG_DATE_RE.search(entry.path.stem)
    if m:
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return d, f"ημερομηνία {d.isoformat()} από το όνομα του αρχείου", True
        except ValueError:
            pass
    d = datetime.fromtimestamp(entry.path.stat().st_mtime).date()
    return d, f"ημερομηνία {d.isoformat()} από την ημερομηνία τροποποίησης του αρχείου", False


def _depth(e: Entry, root: Path) -> int:
    try:
        return len(e.path.relative_to(root).parts)
    except ValueError:
        return 99


class _Dests:
    """Αποφεύγει συγκρούσεις ονομάτων στον προορισμό (και με υπάρχοντα αρχεία)."""

    def __init__(self) -> None:
        self.taken: set[str] = set()

    def resolve(self, folder: Path, name: str, source: Path) -> tuple[Path, bool]:
        stem, suffix = Path(name).stem, Path(name).suffix
        n, cand = 1, folder / name
        renamed = False
        while str(cand).casefold() in self.taken or (cand.exists() and cand != source):
            n += 1
            cand = folder / f"{stem} ({n}){suffix}"
            renamed = True
        self.taken.add(str(cand).casefold())
        return cand, renamed


def _in_app_folder(e: Entry, root: Path, parents_with_non_exe: set[Path],
                   parents_with_subdirs: set[Path]) -> bool:
    if e.kind != "exe" or e.path.parent == root:
        return False
    return e.path.parent in parents_with_non_exe or e.path.parent in parents_with_subdirs


def _keep(e: Entry, rule: str, reason: str, learned_id: int | None = None) -> Proposal:
    why = f"Το όρισες εσύ με τον κανόνα #{learned_id}." if learned_id and learned_id > 0 else ""
    return Proposal(e, KEEP, None, rule, reason, SURE if learned_id else CHECK, why, learned_id)


def _target(e: Entry, folder: Path, dests: _Dests, rule: str, reason: str,
            conf: str, conf_why: str, learned_id: int | None = None) -> Proposal:
    if e.path.parent == folder:
        return _keep(e, rule, reason + " Βρίσκεται ήδη στον προτεινόμενο φάκελο.", learned_id)
    dest, renamed = dests.resolve(folder, e.path.name, e.path)
    if renamed:
        reason += f" Μετονομάζεται σε «{dest.name}» γιατί υπάρχει ήδη αρχείο με το ίδιο όνομα εκεί."
        if conf == SURE:
            conf = CHECK
            conf_why = "υπάρχει ήδη αρχείο με το ίδιο όνομα, γι' αυτό το νέο μετονομάζεται (" + dest.name + ")"
    return Proposal(e, MOVE, dest, rule, reason, conf, conf_why, learned_id)


def _doc_with_topic(e: Entry, root: Path, cfg: Config, dests: _Dests, max_year: int,
                    topic: str, rule: dict) -> Proposal:
    ex = extract(e.path, e.kind, e.size)
    nt = name_text(e.path.stem)
    year, year_why = find_year(nt, ex.text, max_year)
    parts = [cfg.documents_folder, topic] + ([str(year)] if year else [])
    note = f" Σημείωση: {ex.note}." if ex.note else ""
    once = rule["id"] < 0
    intro = ("Αλλαγή που έκανες εσύ μόνο για αυτή τη φορά" if once
             else f"Ισχύει ο {learning.describe_rule_short(rule)}")
    reason = (f"{intro}. "
              f"{year_why[0].upper() + year_why[1:] if year else 'Χωρίς έτος (' + year_why + ')'}."
              f"{note} Άρα πάει στο «{'/'.join(parts)}».")
    label = "αλλαγή σου (μία φορά)" if once else f"μαθημένος κανόνας #{rule['id']}"
    why = "Το όρισες εσύ για αυτή τη φορά." if once else f"Το όρισες εσύ με τον κανόνα #{rule['id']}."
    return _target(e, root.joinpath(*parts), dests, label, reason, SURE, why, rule["id"])


def build_plan(result: ScanResult, report: Report, cfg: Config,
               today: date | None = None, extra_rules: tuple[dict, ...] | list[dict] = ()) -> Plan:
    """Πρόταση δομής. `extra_rules`: κανόνες μόνο γι' αυτή τη φορά (δεν αποθηκεύονται)."""
    root = result.root
    max_year = (today or date.today()).year + 1
    review_root = root / cfg.review_folder_name / cfg.duplicates_subfolder
    dests = _Dests()
    rules = list(cfg.learned_rules) + list(extra_rules)

    # Φάκελοι που μοιάζουν με φάκελο εφαρμογής (για τα EXE).
    parents_with_non_exe = {e.path.parent for e in result.entries if e.kind != "exe"}
    parents_with_subdirs = {p for e in result.entries for p in e.path.parents
                            if p != e.path.parent and p != root.parent}

    # Διπλότυπα: κρατιέται το πιο ρηχό αντίγραφο, τα υπόλοιπα προτείνονται για τον φάκελο ελέγχου.
    dup_extra: dict[Path, Entry] = {}  # αντίγραφο -> αυτό που κρατιέται
    for group in report.duplicate_groups:
        keeper = min(group, key=lambda e: (_depth(e, root), str(e.path).casefold()))
        for e in group:
            if e is keeper or _in_app_folder(e, root, parents_with_non_exe, parents_with_subdirs):
                continue  # ένα EXE σε φάκελο εφαρμογής δεν βγαίνει από εκεί, ούτε ως διπλότυπο
            dup_extra[e.path] = keeper

    proposals: list[Proposal] = []
    for e in result.entries:
        found = learning.find_matches(rules, e.path, root, e.kind)
        distinct = {(r["then"]["type"], r["then"].get("value")) for r in found}
        rule = min(found, key=lambda r: r["id"]) if len(distinct) == 1 else None
        conflict = ""
        if len(distinct) > 1:
            ids = ", ".join(f"#{r['id']}" for r in sorted(found, key=lambda r: r["id"]))
            conflict = f"Οι κανόνες σου {ids} δίνουν διαφορετικό αποτέλεσμα, γι' αυτό δεν εφαρμόστηκε κανένας"

        if rule and rule["then"]["type"] == "keep":
            proposals.append(_keep(
                e, f"μαθημένος κανόνας #{rule['id']}",
                f"Μένει στη θέση του, γιατί ισχύει ο {learning.describe_rule_short(rule)}.",
                rule["id"]))
        elif e.path in dup_extra:
            keeper = dup_extra[e.path]
            dest, renamed = dests.resolve(review_root, e.path.name, e.path)
            reason = (f"Διπλότυπο (ίδιο περιεχόμενο) με «{keeper.path.relative_to(root)}», "
                      "που βρίσκεται πιο ρηχά και κρατιέται. Πάει στον φάκελο ελέγχου, δεν διαγράφεται.")
            conf, why = SURE, ("τα αρχεία είναι πανομοιότυπα (ίδιο SHA-256, byte προς byte) και "
                               "δεν διαγράφεται τίποτα· πάει μόνο στον φάκελο ελέγχου")
            if renamed:
                reason += f" Μετονομάζεται σε «{dest.name}» γιατί υπάρχει ήδη αρχείο με το ίδιο όνομα."
                conf, why = CHECK, f"υπάρχει ήδη αρχείο με το ίδιο όνομα, γι' αυτό μετονομάζεται ({dest.name})"
            proposals.append(Proposal(e, REVIEW, dest, "διπλότυπο", reason, conf, why))
        elif rule and rule["then"]["type"] == "topic":
            proposals.append(_doc_with_topic(e, root, cfg, dests, max_year, rule["then"]["value"], rule))
        else:
            p = _classify(e, root, cfg, dests, max_year, parents_with_non_exe, parents_with_subdirs)
            if conflict and p.action != KEEP:
                p = Proposal(p.entry, p.action, p.dest, p.rule, p.reason + " " + conflict + ".",
                             CHECK, conflict, p.learned_id)
            proposals.append(p)
    return Plan(root=root, proposals=proposals)


def _classify(e: Entry, root: Path, cfg: Config, dests: _Dests, max_year: int,
              parents_with_non_exe: set[Path], parents_with_subdirs: set[Path]) -> Proposal:
    if e.kind == "image":
        d, why, from_name = _image_date(e)
        folder = root / cfg.images_folder / str(d.year) / f"{d.year}-{d.month:02d}"
        if from_name:
            conf, cwhy = SURE, "η ημερομηνία είναι γραμμένη μέσα στο όνομα του αρχείου"
        else:
            conf, cwhy = CHECK, ("η ημερομηνία προέρχεται μόνο από την ώρα τροποποίησης του αρχείου, "
                                 "που αλλάζει όταν το αρχείο αντιγράφεται")
        return _target(e, folder, dests, "εικόνα: ημερομηνία",
                       f"Εικόνα: {why}, άρα πάει στο «{cfg.images_folder}/{d.year}/{d.year}-{d.month:02d}».",
                       conf, cwhy)

    if e.kind == "exe":
        # Ένα EXE ταξινομείται μόνο με όνομα και θέση. Δεν ανοίγεται ποτέ.
        if _in_app_folder(e, root, parents_with_non_exe, parents_with_subdirs):
            return _keep(e, "EXE: φάκελος εφαρμογής",
                         "Το EXE βρίσκεται σε φάκελο με άλλα αρχεία ή υποφακέλους, δηλαδή πιθανόν "
                         "φάκελο εφαρμογής. Αν μετακινηθεί μόνο του, μπορεί να μη δουλεύει, γι' αυτό μένει εκεί.")
        return _target(e, root / cfg.programs_folder, dests, "EXE: όνομα και θέση",
                       f"Πρόγραμμα (EXE) που βρίσκεται μόνο του: ταξινομείται με βάση το όνομα και τη θέση "
                       f"(δεν ανοίγεται), άρα πάει στο «{cfg.programs_folder}».",
                       CHECK, "ένα πρόγραμμα ταξινομείται μόνο από το όνομα και τη θέση του, χωρίς να "
                              "ανοίγει· δεν εγκρίνεται ποτέ αυτόματα")

    if e.kind in learning.DOC_KINDS:
        ex = extract(e.path, e.kind, e.size)
        nt = name_text(e.path.stem)
        tr = find_topic_detail(cfg, nt, ex.text)
        year, year_why = find_year(nt, ex.text, max_year)
        note = f" Σημείωση: {ex.note}." if ex.note else ""
        if tr.topic is None:
            return _keep(e, "έγγραφο: χωρίς πρόταση", f"Μένει στη θέση του: {tr.why}.{note}")
        parts = [cfg.documents_folder, tr.topic] + ([str(year)] if year else [])
        folder = root.joinpath(*parts)
        reason = (f"Έγγραφο: {tr.why}; "
                  f"{year_why if year else 'χωρίς έτος (' + year_why + '), ο φάκελος δεν έχει υποφάκελο έτους'}."
                  f"{note} Άρα πάει στο «{'/'.join(parts)}».")
        issues: list[str] = []
        if tr.hits < 2:
            issues.append(f"βρέθηκε μόνο 1 λέξη του θέματος («{tr.words[0]}»)")
        if tr.other_topics:
            issues.append("ταιριάζουν λέξεις και άλλου θέματος")
        if not year and year_why.startswith("ασαφές"):
            issues.append("το έτος είναι ασαφές")
        if issues:
            conf, cwhy = CHECK, "; ".join(issues)
        else:
            conf = SURE
            cwhy = (f"βρέθηκαν {tr.hits} διαφορετικές λέξεις του θέματος, καμία λέξη άλλου θέματος, και "
                    + ("το έτος είναι σαφές" if year else "δεν βρέθηκε έτος που να μπερδεύει"))
        return _target(e, folder, dests, "έγγραφο: θέμα και έτος", reason, conf, cwhy)

    return _keep(e, "άγνωστος τύπος", "Άγνωστος τύπος αρχείου: δεν αγγίζεται.")
