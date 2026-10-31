"""Χειρόγραφο (manifest) στον δίσκο, αρχείο JSON Lines.

Κάθε μετακίνηση γράφεται ΠΡΙΝ γίνει («intent») και ΜΕΤΑ («done»), με fsync.
Έτσι, ακόμη και αν κοπεί το ρεύμα ή κλείσει βίαια το πρόγραμμα στη μέση,
φαίνεται με ακρίβεια τι πρόλαβε να γίνει. Οι διαδρομές γράφονται σχετικά με
τον φάκελο σάρωσης (λίστα τμημάτων), ώστε να μη χρειάζεται σταθερή διαδρομή.
Το αρχείο μόνο προστίθεται (append-only): τίποτα δεν ξαναγράφεται.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FORMAT = 1


def rel_parts(path: Path, root: Path) -> list[str]:
    return list(path.relative_to(root).parts)


def from_parts(root: Path, parts: list[str]) -> Path:
    return root.joinpath(*parts)


class ManifestWriter:
    def __init__(self, directory: Path, root: Path, app_version: str):
        directory.mkdir(parents=True, exist_ok=True)
        self.root = root
        self.run_id = datetime.now().strftime("%Y%m%dT%H%M%S%f") + "-" + secrets.token_hex(2)
        self.path = directory / f"{self.run_id}.jsonl"
        self._f = open(self.path, "x", encoding="utf-8")  # "x": ποτέ δεν γράφει πάνω σε υπάρχον
        self.write({"t": "start", "format": FORMAT, "run": self.run_id, "app_version": app_version,
                    "root": str(root), "started": datetime.now().isoformat(timespec="seconds")})

    def write(self, obj: dict) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._f.flush()
        os.fsync(self._f.fileno())

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def append(path: Path, obj: dict) -> None:
    """Προσθήκη γραμμής σε υπάρχον manifest (π.χ. κατά την ανάκτηση)."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


@dataclass
class Op:
    index: int
    src: list[str]
    dest: list[str]
    size: int
    mtime_ns: int
    action: str
    rule: str
    reason: str
    state: str = "pending"  # pending | done | skipped | conflict
    why: str = ""
    undo: str = ""          # "" (δεν αναιρέθηκε) | pending | undone | skipped
    undo_why: str = ""


@dataclass
class Manifest:
    path: Path
    root: Path
    run_id: str
    started: str
    ops: list[Op] = field(default_factory=list)
    dirs: list[list[str]] = field(default_factory=list)  # φάκελοι που δημιούργησε η εκτέλεση
    ended: bool = False
    status: str = ""
    undo_started: bool = False
    undo_ended: bool = False
    undo_status: str = ""

    def src_path(self, op: Op) -> Path:
        return from_parts(self.root, op.src)

    def dest_path(self, op: Op) -> Path:
        return from_parts(self.root, op.dest)


def read(path: Path) -> Manifest:
    """Διαβάζει manifest. Μια μισογραμμένη τελευταία γραμμή (διακοπή) αγνοείται."""
    lines = path.read_text(encoding="utf-8").split("\n")
    records: list[dict] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if any(l.strip() for l in lines[i + 1:]):
                raise ValueError(f"Κατεστραμμένο manifest «{path.name}» στη γραμμή {i + 1}.") from None
            break  # μισογραμμένη τελευταία γραμμή
    if not records or records[0].get("t") != "start":
        raise ValueError(f"Το «{path.name}» δεν είναι έγκυρο manifest.")
    st = records[0]
    if st.get("format") != FORMAT:
        raise ValueError(f"Το manifest «{path.name}» έχει άγνωστη μορφή {st.get('format')}.")
    m = Manifest(path=path, root=Path(st["root"]), run_id=st["run"], started=st["started"])
    by_index: dict[int, Op] = {}
    for r in records[1:]:
        t = r.get("t")
        if t == "mkdir":
            m.dirs.append(r["path"])
        elif t == "intent":
            op = Op(r["i"], r["src"], r["dest"], r["size"], r["mtime_ns"],
                    r["action"], r["rule"], r["reason"])
            by_index[op.index] = op
            m.ops.append(op)
        elif t in ("done", "skip", "conflict") and r.get("i") in by_index:
            op = by_index[r["i"]]
            op.state = {"done": "done", "skip": "skipped", "conflict": "conflict"}[t]
            op.why = r.get("why", "")
        elif t == "end":
            m.ended = True
            m.status = r.get("status", "")
        elif t == "undo_start":
            m.undo_started, m.undo_ended = True, False
        elif t in ("undo_intent", "undone", "undo_skip") and r.get("i") in by_index:
            op = by_index[r["i"]]
            op.undo = {"undo_intent": "pending", "undone": "undone", "undo_skip": "skipped"}[t]
            op.undo_why = r.get("why", "")
        elif t == "undo_end":
            m.undo_ended = True
            m.undo_status = r.get("status", "")
    return m


def list_manifests(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.jsonl"))
