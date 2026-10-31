"""Αναίρεση εκτέλεσης, μόνο από το manifest στον δίσκο (δουλεύει και μετά από επανεκκίνηση).

Κάθε αρχείο επιστρέφει στην αρχική του θέση, με τους ίδιους ελέγχους ασφάλειας
όπως στη μετακίνηση: ποτέ δεν γράφει πάνω σε αρχείο και ποτέ δεν διαγράφει.
Ό,τι δεν μπορεί να αναιρεθεί (άλλαξε, λείπει, η αρχική θέση πιάστηκε, είναι
κλειδωμένο) παραλείπεται με λόγο, και μπορεί να ξαναδοκιμαστεί αργότερα.
Στο τέλος αφαιρούνται μόνο οι ΑΔΕΙΟΙ φάκελοι που δημιούργησε η ίδια η εκτέλεση.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import manifest as mf
from .mover import _is_locked, _move_file, reconcile_all


@dataclass
class RunInfo:
    path: Path
    run_id: str
    started: str
    root: Path
    moved: int          # πόσα αρχεία μετακινήθηκαν σε εκείνη την εκτέλεση
    undone: int         # πόσα έχουν ήδη αναιρεθεί

    @property
    def remaining(self) -> int:
        return self.moved - self.undone

    @property
    def label(self) -> str:
        if self.moved == 0:
            return "δεν μετακίνησε τίποτα"
        if self.undone == 0:
            return "εφαρμόστηκε"
        if self.remaining == 0:
            return "αναιρέθηκε"
        return f"αναιρέθηκε μερικώς ({self.undone} από {self.moved})"


@dataclass
class UndoResult:
    run_id: str
    restored: int = 0
    skipped: list[tuple[mf.Op, str]] = field(default_factory=list)
    removed_dirs: int = 0
    status: str = "complete"  # complete | partial | stopped
    recovered_notes: list[str] = field(default_factory=list)


def list_runs(manifest_dir: Path) -> list[RunInfo]:
    """Όλες οι εκτελέσεις, οι πιο πρόσφατες πρώτα. Κατεστραμμένα manifest παραλείπονται."""
    runs: list[RunInfo] = []
    for path in mf.list_manifests(manifest_dir):
        try:
            m = mf.read(path)
        except ValueError:
            continue
        done = [o for o in m.ops if o.state == "done"]
        runs.append(RunInfo(path, m.run_id, m.started, m.root, len(done),
                            sum(1 for o in done if o.undo == "undone")))
    return sorted(runs, key=lambda r: r.run_id, reverse=True)


def pending_ops(m: mf.Manifest) -> list[mf.Op]:
    """Μετακινήσεις που μπορούν ακόμη να αναιρεθούν, με σειρά αντίστροφη της εκτέλεσης."""
    return [o for o in reversed(m.ops) if o.state == "done" and o.undo in ("", "skipped")]


def check_op(m: mf.Manifest, op: mf.Op, allow_modified: bool = False) -> str | None:
    """Λόγος για τον οποίο ΔΕΝ μπορεί να αναιρεθεί (ή None αν μπορεί)."""
    src, dest = m.src_path(op), m.dest_path(op)
    if not os.path.lexists(dest):
        return "Το αρχείο δεν βρίσκεται πια εκεί που το έβαλε ο οργανωτής (το μετακίνησες ή το έσβησες;)."
    if dest.is_symlink():
        return "Στη θέση του υπάρχει πλέον σύνδεσμος."
    try:
        st = dest.stat()
    except OSError as exc:
        return f"Δεν διαβάζεται το αρχείο ({exc.strerror})."
    if not allow_modified and (st.st_size != op.size or st.st_mtime_ns != op.mtime_ns):
        return "Το αρχείο άλλαξε μετά τη μετακίνηση, άρα δεν είναι σίγουρο ότι είναι το ίδιο."
    if os.path.lexists(src):
        return "Η αρχική θέση έχει πιαστεί από άλλο αρχείο."
    return None


def undo(manifest_path: Path, should_stop: Callable[[], bool] | None = None,
         progress: Callable[[int, int, mf.Op], None] | None = None,
         allow_modified: bool = False) -> UndoResult:
    notes = reconcile_all(manifest_path.parent)  # κλείνει τυχόν διακοπτόμενες εκτελέσεις πρώτα
    m = mf.read(manifest_path)
    if not m.root.is_dir():
        raise ValueError(f"Ο φάκελος «{m.root}» δεν υπάρχει πια, δεν μπορεί να γίνει αναίρεση.")
    res = UndoResult(m.run_id, recovered_notes=notes)
    ops = pending_ops(m)
    mf.append(manifest_path, {"t": "undo_start"})
    for n, op in enumerate(ops, start=1):
        if should_stop and should_stop():
            res.status = "stopped"
            break
        if progress:
            progress(n, len(ops), op)
        why = check_op(m, op, allow_modified)
        if why:
            mf.append(manifest_path, {"t": "undo_skip", "i": op.index, "why": why})
            res.skipped.append((op, why))
            continue
        src, dest = m.src_path(op), m.dest_path(op)
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            why = f"Δεν δημιουργήθηκε ο αρχικός φάκελος ({exc.strerror})."
            mf.append(manifest_path, {"t": "undo_skip", "i": op.index, "why": why})
            res.skipped.append((op, why))
            continue
        mf.append(manifest_path, {"t": "undo_intent", "i": op.index})
        try:
            _move_file(dest, src)
        except OSError as exc:
            why = ("Το αρχείο είναι κλειδωμένο ή δεν έχεις δικαίωμα (ίσως είναι ανοιχτό αλλού)."
                   if _is_locked(exc) else f"Δεν επέστρεψε ({exc.strerror or exc}).")
            mf.append(manifest_path, {"t": "undo_skip", "i": op.index, "why": why})
            res.skipped.append((op, why))
            continue
        mf.append(manifest_path, {"t": "undone", "i": op.index})
        res.restored += 1
    # Μόνο άδειοι φάκελοι που δημιούργησε η εκτέλεση· os.rmdir αποτυγχάνει αν έχουν οτιδήποτε.
    for parts in reversed(m.dirs):
        d = mf.from_parts(m.root, parts)
        try:
            if d.is_dir() and not d.is_symlink():
                d.rmdir()
                mf.append(manifest_path, {"t": "rmdir", "path": parts})
                res.removed_dirs += 1
        except OSError:
            pass
    if res.status != "stopped" and res.skipped:
        res.status = "partial"
    mf.append(manifest_path, {"t": "undo_end", "status": res.status,
                              "restored": res.restored, "skipped": len(res.skipped)})
    return res
