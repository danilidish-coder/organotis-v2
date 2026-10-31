"""Σάρωση φακέλου, μόνο ανάγνωση.

Δεν μετακινεί, δεν μετονομάζει, δεν γράφει και δεν εκτελεί τίποτα.
Τα αρχεία (και τα EXE) διαβάζονται μόνο ως bytes για το αποτύπωμα (SHA-256).
Οι συμβολικοί σύνδεσμοι δεν ακολουθούνται, ώστε να μη γίνονται κύκλοι ή
έξοδος από τον φάκελο.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Entry:
    path: Path
    size: int
    kind: str  # κατηγορία από τις ρυθμίσεις ή "other"
    ext: str
    mtime_ns: int = 0  # για έλεγχο ότι το αρχείο δεν άλλαξε μετά τη σάρωση


@dataclass
class Problem:
    path: Path
    reason: str  # σε απλά ελληνικά


@dataclass
class ScanResult:
    root: Path
    entries: list[Entry] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    skipped_links: int = 0


@dataclass
class Report:
    root: Path
    total: int
    total_size: int
    by_kind: dict[str, tuple[int, int]]  # κατηγορία -> (πλήθος, bytes)
    duplicate_groups: list[list[Entry]]  # ίδιο περιεχόμενο
    same_name_groups: list[list[Entry]]  # ίδιο όνομα, διαφορετικό περιεχόμενο
    problems: list[Problem]
    skipped_links: int

    @property
    def duplicate_extra_files(self) -> int:
        return sum(len(g) - 1 for g in self.duplicate_groups)

    @property
    def duplicate_wasted_bytes(self) -> int:
        return sum((len(g) - 1) * g[0].size for g in self.duplicate_groups)


def scan(root: Path, cfg: Config) -> ScanResult:
    root = Path(root)
    res = ScanResult(root=root)
    if not root.is_dir():
        res.problems.append(Problem(root, "Ο φάκελος δεν υπάρχει ή δεν είναι φάκελος."))
        return res

    def on_error(err: OSError) -> None:
        res.problems.append(Problem(Path(err.filename or root), f"Δεν διαβάζεται ο φάκελος ({err.strerror})."))

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
        # Ο φάκελος ελέγχου δεν σαρώνεται: ό,τι έχει ήδη παραμεριστεί δεν ξαναπροτείνεται.
        kept = []
        for d in sorted(dirnames):
            if d == cfg.review_folder_name:
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                res.skipped_links += 1
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.is_symlink():
                res.skipped_links += 1
                continue
            try:
                st = p.stat()
                size = st.st_size
            except OSError as exc:
                res.problems.append(Problem(p, f"Δεν διαβάζονται τα στοιχεία του αρχείου ({exc.strerror})."))
                continue
            ext = os.path.splitext(name)[1].lower()
            res.entries.append(Entry(p, size, cfg.kind_of(name) or "other", ext, st.st_mtime_ns))
    return res


def _hash(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        remaining = limit
        while True:
            n = CHUNK if remaining is None else min(CHUNK, remaining)
            if n == 0:
                break
            buf = f.read(n)
            if not buf:
                break
            h.update(buf)
            if remaining is not None:
                remaining -= len(buf)
    return h.hexdigest()


def build_report(result: ScanResult) -> Report:
    problems = list(result.problems)
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for e in result.entries:
        by_kind[e.kind][0] += 1
        by_kind[e.kind][1] += e.size

    # Διπλότυπα: πρώτα ίδιο μέγεθος, μετά αποτύπωμα πρώτου MB, μετά πλήρες SHA-256.
    by_size: dict[int, list[Entry]] = defaultdict(list)
    for e in result.entries:
        if e.size > 0:  # τα κενά αρχεία δεν θεωρούνται διπλότυπα
            by_size[e.size].append(e)

    def regroup(items: list[Entry], limit: int | None) -> dict[str, list[Entry]]:
        out: dict[str, list[Entry]] = defaultdict(list)
        for e in items:
            try:
                out[_hash(e.path, limit)].append(e)
            except OSError as exc:  # κλειδωμένο ή χωρίς δικαίωμα: δεν μετράει, αναφέρεται
                problems.append(Problem(e.path, f"Δεν διαβάστηκε (κλειδωμένο ή χωρίς δικαίωμα): {exc.strerror}."))
        return out

    dup_groups: list[list[Entry]] = []
    for items in by_size.values():
        if len(items) < 2:
            continue
        for part in regroup(items, CHUNK).values():
            if len(part) < 2:
                continue
            for full in regroup(part, None).values():
                if len(full) > 1:
                    dup_groups.append(sorted(full, key=lambda x: str(x.path)))
    dup_groups.sort(key=lambda g: str(g[0].path))

    in_dup = {e.path for g in dup_groups for e in g}
    by_name: dict[str, list[Entry]] = defaultdict(list)
    for e in result.entries:
        by_name[e.path.name.lower()].append(e)
    same_name: list[list[Entry]] = []
    for items in by_name.values():
        if len({e.path.parent for e in items}) < 2:
            continue
        # Κρατάμε μόνο όσα δεν είναι ήδη καταγεγραμμένα ως πανομοιότυπα.
        rest = [e for e in items if e.path not in in_dup]
        if len(rest) > 1 and len({e.path.parent for e in rest}) > 1:
            same_name.append(sorted(rest, key=lambda x: str(x.path)))
    same_name.sort(key=lambda g: g[0].path.name.lower())

    return Report(
        root=result.root,
        total=len(result.entries),
        total_size=sum(e.size for e in result.entries),
        by_kind={k: (v[0], v[1]) for k, v in sorted(by_kind.items())},
        duplicate_groups=dup_groups,
        same_name_groups=same_name,
        problems=sorted(problems, key=lambda p: str(p.path)),
        skipped_links=result.skipped_links,
    )
