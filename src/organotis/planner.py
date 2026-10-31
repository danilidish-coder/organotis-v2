"""Κανόνες και πρόταση δομής. Δεν αγγίζει κανένα αρχείο: επιστρέφει μόνο πρόταση.

Κάθε πρόταση έχει «λόγο» σε απλά ελληνικά (ποιος κανόνας και ποια στοιχεία).
Δεν υπάρχει σκορ: το θέμα προτείνεται μόνο αν ένα θέμα έχει καθαρά
περισσότερες λέξεις-κλειδιά από τα υπόλοιπα, αλλιώς το αρχείο μένει στη θέση του.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .config import Config
from .extract import extract
from .scanner import Entry, Report, ScanResult

MOVE, REVIEW, KEEP = "move", "review", "keep"
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


@dataclass
class Plan:
    root: Path
    proposals: list[Proposal]

    def by_action(self, action: str) -> list[Proposal]:
        return [p for p in self.proposals if p.action == action]


def normalize(text: str) -> str:
    """Πεζά, χωρίς τόνους, ς→σ, ώστε «ΔΕΗ», «Δεη» και «δεή» να ταυτίζονται."""
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("ς", "σ")


def _kw_pattern(kw: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(normalize(kw).strip()) + r"(?!\w)")


def find_topic(cfg: Config, name_text: str, body_text: str) -> tuple[str | None, str]:
    """Επιστρέφει (θέμα ή None, εξήγηση)."""
    name_n, body_n = normalize(name_text), normalize(body_text)
    hits: dict[str, list[tuple[str, str]]] = {}
    for rule in cfg.topic_rules:
        found: list[tuple[str, str]] = []
        for kw in rule["keywords"]:
            pat = _kw_pattern(kw)
            if pat.search(name_n):
                found.append((kw, "στο όνομα"))
            elif pat.search(body_n):
                found.append((kw, "στο κείμενο"))
        if found:
            hits[rule["topic"]] = found
    if not hits:
        return None, "δεν βρέθηκε καμία λέξη-κλειδί θέματος"
    best = max(len(v) for v in hits.values())
    top = sorted(t for t, v in hits.items() if len(v) == best)
    if len(top) > 1:
        return None, ("ασαφές θέμα, ισοβαθμία ανάμεσα σε: "
                      + ", ".join(f"«{t}»" for t in top))
    topic = top[0]
    words = ", ".join(f"«{kw}» ({where})" for kw, where in hits[topic])
    return topic, f"θέμα «{topic}»: βρέθηκαν οι λέξεις {words}"


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


def _image_date(entry: Entry) -> tuple[date, str]:
    m = _IMG_DATE_RE.search(entry.path.stem)
    if m:
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return d, f"ημερομηνία {d.isoformat()} από το όνομα του αρχείου"
        except ValueError:
            pass
    d = datetime.fromtimestamp(entry.path.stat().st_mtime).date()
    return d, f"ημερομηνία {d.isoformat()} από την ημερομηνία τροποποίησης του αρχείου"


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


def build_plan(result: ScanResult, report: Report, cfg: Config,
               today: date | None = None) -> Plan:
    root = result.root
    max_year = (today or date.today()).year + 1
    review_root = root / cfg.review_folder_name / cfg.duplicates_subfolder
    dests = _Dests()
    proposals: dict[Path, Proposal] = {}

    # Φάκελοι που μοιάζουν με φάκελο εφαρμογής (για τα EXE).
    parents_with_non_exe = {e.path.parent for e in result.entries if e.kind != "exe"}
    parents_with_subdirs = {p for e in result.entries for p in e.path.parents
                            if p != e.path.parent and p != root.parent}

    # --- 1. Διπλότυπα: κρατιέται το πιο ρηχό αντίγραφο, τα υπόλοιπα στον φάκελο ελέγχου.
    keep_in_group: dict[Path, Path] = {}
    for group in report.duplicate_groups:
        keeper = min(group, key=lambda e: (_depth(e, root), str(e.path).casefold()))
        for e in group:
            keep_in_group[e.path] = keeper.path
            if e is keeper:
                continue
            if _in_app_folder(e, root, parents_with_non_exe, parents_with_subdirs):
                # Ένα EXE μέσα σε φάκελο εφαρμογής δεν βγαίνει από εκεί, ούτε ως διπλότυπο.
                continue
            dest, renamed = dests.resolve(review_root, e.path.name, e.path)
            reason = (f"Διπλότυπο (ίδιο περιεχόμενο) με «{keeper.path.relative_to(root)}», "
                      "που βρίσκεται πιο ρηχά και κρατιέται. Πάει στον φάκελο ελέγχου, δεν διαγράφεται.")
            if renamed:
                reason += f" Μετονομάζεται σε «{dest.name}» γιατί υπάρχει ήδη αρχείο με το ίδιο όνομα."
            proposals[e.path] = Proposal(e, REVIEW, dest, "διπλότυπο", reason)

    # --- 2. Τα υπόλοιπα αρχεία.
    for e in result.entries:
        if e.path in proposals:
            continue
        proposals[e.path] = _classify(
            e, root, cfg, dests, max_year, parents_with_non_exe, parents_with_subdirs)

    ordered = [proposals[e.path] for e in result.entries]
    return Plan(root=root, proposals=ordered)


def _in_app_folder(e: Entry, root: Path, parents_with_non_exe: set[Path],
                   parents_with_subdirs: set[Path]) -> bool:
    if e.kind != "exe" or e.path.parent == root:
        return False
    return e.path.parent in parents_with_non_exe or e.path.parent in parents_with_subdirs


def _keep(e: Entry, rule: str, reason: str) -> Proposal:
    return Proposal(e, KEEP, None, rule, reason)


def _target(e: Entry, folder: Path, dests: _Dests, rule: str, reason: str) -> Proposal:
    if e.path.parent == folder:
        return _keep(e, rule, reason + " Βρίσκεται ήδη στον προτεινόμενο φάκελο.")
    dest, renamed = dests.resolve(folder, e.path.name, e.path)
    if renamed:
        reason += f" Μετονομάζεται σε «{dest.name}» γιατί υπάρχει ήδη αρχείο με το ίδιο όνομα εκεί."
    return Proposal(e, MOVE, dest, rule, reason)


def _classify(e: Entry, root: Path, cfg: Config, dests: _Dests, max_year: int,
              parents_with_non_exe: set[Path], parents_with_subdirs: set[Path]) -> Proposal:
    if e.kind == "image":
        d, why = _image_date(e)
        folder = root / cfg.images_folder / str(d.year) / f"{d.year}-{d.month:02d}"
        return _target(e, folder, dests, "εικόνα: ημερομηνία",
                       f"Εικόνα: {why}, άρα πάει στο «{cfg.images_folder}/{d.year}/{d.year}-{d.month:02d}».")

    if e.kind == "exe":
        # Ένα EXE ταξινομείται μόνο με όνομα και θέση. Δεν ανοίγεται ποτέ.
        if _in_app_folder(e, root, parents_with_non_exe, parents_with_subdirs):
            return _keep(e, "EXE: φάκελος εφαρμογής",
                         "Το EXE βρίσκεται σε φάκελο με άλλα αρχεία ή υποφακέλους, δηλαδή πιθανόν "
                         "φάκελο εφαρμογής. Αν μετακινηθεί μόνο του, μπορεί να μη δουλεύει, γι' αυτό μένει εκεί.")
        return _target(e, root / cfg.programs_folder, dests, "EXE: όνομα και θέση",
                       f"Πρόγραμμα (EXE) που βρίσκεται μόνο του: ταξινομείται με βάση το όνομα και τη θέση "
                       f"(δεν ανοίγεται), άρα πάει στο «{cfg.programs_folder}».")

    if e.kind in ("word", "pdf", "excel"):
        ex = extract(e.path, e.kind, e.size)
        name_text = e.path.stem.replace("_", " ")
        topic, topic_why = find_topic(cfg, name_text, ex.text)
        year, year_why = find_year(name_text, ex.text, max_year)
        note = f" Σημείωση: {ex.note}." if ex.note else ""
        if topic is None:
            return _keep(e, "έγγραφο: χωρίς πρόταση",
                         f"Μένει στη θέση του: {topic_why}.{note}")
        parts = [cfg.documents_folder, topic] + ([str(year)] if year else [])
        folder = root.joinpath(*parts)
        reason = (f"Έγγραφο: {topic_why}; "
                  f"{year_why if year else 'χωρίς έτος (' + year_why + '), ο φάκελος δεν έχει υποφάκελο έτους'}."
                  f"{note} Άρα πάει στο «{'/'.join(parts)}».")
        return _target(e, folder, dests, "έγγραφο: θέμα και έτος", reason)

    return _keep(e, "άγνωστος τύπος", "Άγνωστος τύπος αρχείου: δεν αγγίζεται.")
