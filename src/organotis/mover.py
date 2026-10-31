"""Μετακίνηση εγκεκριμένων αρχείων με manifest. Ποτέ δεν διαγράφει και ποτέ δεν γράφει πάνω σε αρχείο.

Σειρά για κάθε αρχείο: έλεγχοι → «intent» στο manifest (fsync) → μετακίνηση → «done».
Αν κάτι αποτύχει (π.χ. κλειδωμένο αρχείο), το αρχείο παραλείπεται με λόγο και
η εκτέλεση συνεχίζει με τα υπόλοιπα.
"""
from __future__ import annotations

import errno
import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__, manifest as mf
from .approval import ApprovedPlan
from .planner import Proposal


@dataclass
class RunResult:
    run_id: str
    manifest_path: Path
    moved: int = 0
    skipped: list[tuple[Proposal, str]] = field(default_factory=list)
    status: str = "complete"  # complete | stopped
    recovered_notes: list[str] = field(default_factory=list)


def _is_locked(exc: OSError) -> bool:
    return isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM, errno.EBUSY) \
        or getattr(exc, "winerror", None) in (5, 32, 33)


def _rename(src: Path, dest: Path) -> None:
    """Ξεχωριστή συνάρτηση, ώστε οι δοκιμές να μπορούν να προσομοιώσουν βλάβες."""
    os.rename(src, dest)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _move_file(src: Path, dest: Path) -> None:
    """Μετακίνηση χωρίς αντικατάσταση. Σε άλλον δίσκο: αντιγραφή, επαλήθευση, μετά αφαίρεση του αρχικού."""
    if os.path.lexists(dest):
        raise FileExistsError(errno.EEXIST, "υπάρχει ήδη", str(dest))
    try:
        _rename(src, dest)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    tmp = dest.with_name(dest.name + ".organotis-part")
    try:
        shutil.copy2(src, tmp)
        if tmp.stat().st_size != src.stat().st_size or _sha256(tmp) != _sha256(src):
            raise OSError(errno.EIO, "η αντιγραφή δεν επαληθεύτηκε")
        if os.path.lexists(dest):
            raise FileExistsError(errno.EEXIST, "υπάρχει ήδη", str(dest))
        os.rename(tmp, dest)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    os.unlink(src)  # το αντίγραφο έχει επαληθευτεί· αυτό ολοκληρώνει τη μετακίνηση


def _precheck(p: Proposal, root: Path) -> str | None:
    src, dest = p.entry.path, p.dest
    if dest is None:
        return "Δεν υπάρχει προορισμός."
    try:
        dest.resolve().relative_to(root.resolve())
    except ValueError:
        return "Ο προορισμός είναι έξω από τον φάκελο σάρωσης."
    if src.is_symlink():
        return "Το αρχείο είναι πλέον σύνδεσμος."
    try:
        st = src.stat()
    except FileNotFoundError:
        return "Το αρχείο δεν υπάρχει πια στη θέση του."
    except OSError as exc:
        return f"Δεν διαβάζεται το αρχείο ({exc.strerror})."
    if st.st_size != p.entry.size or (p.entry.mtime_ns and st.st_mtime_ns != p.entry.mtime_ns):
        return "Το αρχείο άλλαξε μετά τη σάρωση."
    if os.path.lexists(dest):
        return "Υπάρχει ήδη αρχείο με αυτό το όνομα στον προορισμό."
    return None


def _make_dirs(folder: Path, root: Path, w: mf.ManifestWriter) -> None:
    missing: list[Path] = []
    cur = folder
    while not cur.exists() and cur != root:
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        w.write({"t": "mkdir", "path": mf.rel_parts(d, root)})  # πρώτα καταγραφή
        d.mkdir()


def execute(approved: ApprovedPlan, manifest_dir: Path,
            should_stop: Callable[[], bool] | None = None,
            progress: Callable[[int, int, Proposal], None] | None = None) -> RunResult:
    if not isinstance(approved, ApprovedPlan):
        raise TypeError("Η μετακίνηση χρειάζεται εγκεκριμένη προεπισκόπηση (ApprovedPlan).")
    root = approved.root
    recovered = reconcile_all(manifest_dir)
    total = len(approved.proposals)
    with mf.ManifestWriter(manifest_dir, root, __version__) as w:
        res = RunResult(w.run_id, w.path, recovered_notes=recovered)
        for i, p in enumerate(approved.proposals, start=1):
            if should_stop and should_stop():
                res.status = "stopped"
                break
            if progress:
                progress(i, total, p)
            why = _precheck(p, root)
            if why:
                w.write({"t": "pre_skip", "src": mf.rel_parts(p.entry.path, root), "why": why})
                res.skipped.append((p, why))
                continue
            src, dest = p.entry.path, p.dest
            try:
                _make_dirs(dest.parent, root, w)
            except OSError as exc:
                why = f"Δεν δημιουργήθηκε ο φάκελος προορισμού ({exc.strerror})."
                w.write({"t": "pre_skip", "src": mf.rel_parts(src, root), "why": why})
                res.skipped.append((p, why))
                continue
            w.write({"t": "intent", "i": i, "src": mf.rel_parts(src, root),
                     "dest": mf.rel_parts(dest, root), "size": p.entry.size,
                     "mtime_ns": src.stat().st_mtime_ns, "action": p.action,
                     "rule": p.rule, "reason": p.reason, "confidence": p.confidence})
            try:
                _move_file(src, dest)
            except OSError as exc:
                why = ("Το αρχείο είναι κλειδωμένο ή δεν έχεις δικαίωμα (ίσως είναι ανοιχτό αλλού)."
                       if _is_locked(exc) else f"Δεν μετακινήθηκε ({exc.strerror or exc})." )
                w.write({"t": "skip", "i": i, "why": why})
                res.skipped.append((p, why))
                continue
            w.write({"t": "done", "i": i})
            res.moved += 1
        w.write({"t": "end", "status": res.status, "moved": res.moved, "skipped": len(res.skipped)})
    return res


# ---------- ανάκτηση μετά από διακοπή ----------

def reconcile(path: Path) -> list[str]:
    """Κλείνει ένα manifest που διακόπηκε, είτε στη μετακίνηση είτε στην αναίρεση.

    Για κάθε «intent» χωρίς αποτέλεσμα κοιτάζει τον δίσκο:
    - το αρχείο είναι ακόμη στην αρχική θέση → δεν πρόλαβε να μετακινηθεί
    - το αρχείο είναι μόνο στον προορισμό → μετακινήθηκε (καταγράφεται ως done)
    - και στα δύο ή πουθενά → «conflict», δεν αγγίζεται τίποτα, ενημερώνεται ο χρήστης
    Στην αναίρεση ισχύει το αντίστροφο. Δεν μετακινεί και δεν διαγράφει τίποτα.
    """
    m = mf.read(path)
    notes: list[str] = []
    for op in m.ops:
        src_exists = os.path.lexists(m.src_path(op))
        dest_exists = os.path.lexists(m.dest_path(op))
        name = op.src[-1]
        if op.state == "pending":
            if src_exists and not dest_exists:
                mf.append(path, {"t": "skip", "i": op.index, "why": "Η εκτέλεση διακόπηκε πριν μετακινηθεί."})
                notes.append(f"«{name}»: δεν πρόλαβε να μετακινηθεί, μένει στη θέση του.")
            elif dest_exists and not src_exists:
                mf.append(path, {"t": "done", "i": op.index, "why": "Ανακτήθηκε μετά από διακοπή."})
                notes.append(f"«{name}»: είχε μετακινηθεί, καταγράφηκε στο manifest.")
            else:
                why = ("Υπάρχει και στις δύο θέσεις." if src_exists else "Δεν βρέθηκε πουθενά.")
                mf.append(path, {"t": "conflict", "i": op.index, "why": why})
                notes.append(f"«{name}»: {why} Έλεγξέ το χειροκίνητα, δεν άλλαξε τίποτα.")
        if op.undo == "pending":
            if src_exists and not dest_exists:
                mf.append(path, {"t": "undone", "i": op.index, "why": "Ανακτήθηκε μετά από διακοπή."})
                notes.append(f"«{name}»: είχε αναιρεθεί, καταγράφηκε στο manifest.")
            else:
                why = ("Υπάρχει και στις δύο θέσεις." if src_exists and dest_exists
                       else "Η αναίρεση διακόπηκε πριν γίνει.")
                mf.append(path, {"t": "undo_skip", "i": op.index, "why": why})
                notes.append(f"«{name}»: {why} Μπορείς να ξανατρέξεις την αναίρεση.")
    if not m.ended:
        mf.append(path, {"t": "end", "status": "recovered"})
    if m.undo_started and not m.undo_ended:
        mf.append(path, {"t": "undo_end", "status": "recovered"})
    return notes


def reconcile_all(manifest_dir: Path) -> list[str]:
    notes: list[str] = []
    for path in mf.list_manifests(manifest_dir):
        try:
            m = mf.read(path)
            if not m.ended or (m.undo_started and not m.undo_ended):
                notes.extend(reconcile(path))
        except ValueError as exc:
            notes.append(str(exc))
    return notes
