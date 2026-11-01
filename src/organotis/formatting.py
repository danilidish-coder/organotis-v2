"""Αναφορά σάρωσης σε απλά ελληνικά."""
from __future__ import annotations

from pathlib import Path

from .scanner import Report

KIND_LABELS = {
    "word": "Word", "pdf": "PDF", "excel": "Excel", "image": "Εικόνες",
    "exe": "EXE (δεν ανοίγονται ποτέ)", "other": "Άλλα",
}


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def format_report(r: Report, max_groups: int = 50) -> str:
    out: list[str] = []
    out.append(f"Σάρωση: {r.root}")
    out.append("(Μόνο ανάγνωση: δεν μετακινήθηκε ούτε άλλαξε τίποτα.)")
    out.append("")
    out.append(f"Σύνολο: {r.total} αρχεία, {human_size(r.total_size)}")
    out.append("")
    out.append("Αρχεία ανά τύπο:")
    for kind, (count, size) in r.by_kind.items():
        out.append(f"  {KIND_LABELS.get(kind, kind):<28} {count:>6}  {human_size(size):>10}")

    out.append("")
    out.append(
        f"Διπλότυπα (ίδιο περιεχόμενο): {len(r.duplicate_groups)} ομάδες, "
        f"{r.duplicate_extra_files} επιπλέον αντίγραφα, "
        f"{human_size(r.duplicate_wasted_bytes)} χώρος"
    )
    for g in r.duplicate_groups[:max_groups]:
        out.append(f"  • {g[0].path.name} ({human_size(g[0].size)}), {len(g)} αντίγραφα:")
        out.extend(f"      {_rel(e.path, r.root)}" for e in g)
    if len(r.duplicate_groups) > max_groups:
        out.append(f"  … και {len(r.duplicate_groups) - max_groups} ακόμη ομάδες.")

    out.append("")
    out.append(
        f"Ίδιο όνομα σε διαφορετικές θέσεις, με διαφορετικό περιεχόμενο: "
        f"{len(r.same_name_groups)} ονόματα"
    )
    for g in r.same_name_groups[:max_groups]:
        out.append(f"  • {g[0].path.name}:")
        out.extend(f"      {_rel(e.path, r.root)} ({human_size(e.size)})" for e in g)
    if len(r.same_name_groups) > max_groups:
        out.append(f"  … και {len(r.same_name_groups) - max_groups} ακόμη ονόματα.")

    if r.skipped_links:
        out.append("")
        out.append(f"Παραλείφθηκαν {r.skipped_links} συντομεύσεις/σύνδεσμοι (δεν ακολουθούνται).")
    if r.problems:
        out.append("")
        out.append(f"Προβλήματα ({len(r.problems)}), τα αρχεία αυτά δεν συμμετέχουν στη σύγκριση:")
        out.extend(f"  ! {_rel(p.path, r.root)}: {p.reason}" for p in r.problems)
    return "\n".join(out)


def format_plan(plan, max_rows: int = 200) -> str:
    """Πρόταση δομής σε απλά ελληνικά. Δεν αλλάζει τίποτα."""
    from .planner import KEEP, MOVE, REVIEW

    root = plan.root
    moves, keeps = plan.by_action(MOVE), plan.by_action(KEEP)
    reviews = [p for p in plan.by_action(REVIEW) if p.rule != "ίδιο όνομα"]
    same_moved = [p for p in plan.by_action(REVIEW) if p.rule == "ίδιο όνομα"]
    same_kept = [p for p in keeps if p.rule == "ίδιο όνομα"]
    keeps = [p for p in keeps if p.rule != "ίδιο όνομα"]
    out = [f"Πρόταση δομής για: {root}",
           "(Μόνο πρόταση: δεν μετακινήθηκε ούτε άλλαξε τίποτα. Κάθε γραμμή έχει τον λόγο της.)",
           "",
           f"Προτείνονται {len(moves)} μετακινήσεις, {len(reviews)} αρχεία στον φάκελο ελέγχου "
           f"(διπλότυπα), {len(keeps) + len(same_kept)} μένουν στη θέση τους."]
    if same_moved:
        out[-1] += (f" Ίδια ονόματα: {len(same_kept)} κρατήθηκαν, "
                    f"{len(same_moved)} πάνε στον φάκελο ελέγχου.")

    def block(title: str, items: list) -> None:
        if not items:
            return
        out.extend(["", title])
        for p in items[:max_rows]:
            dest = _rel(p.dest, root) if p.dest else "(μένει εκεί που είναι)"
            out.append(f"  • {_rel(p.entry.path, root)}")
            out.append(f"      → {dest}")
            out.append(f"      Λόγος [{p.rule}]: {p.reason}")
        if len(items) > max_rows:
            out.append(f"  … και {len(items) - max_rows} ακόμη.")

    block("Μετακινήσεις:", moves)
    block("Στον φάκελο ελέγχου (διπλότυπα, καμία διαγραφή):", reviews)
    if same_moved:
        out.extend(["", "Ίδια ονόματα (διαφορετικό περιεχόμενο): τι κρατήθηκε και τι πάει στον φάκελο ελέγχου:"])
        groups: dict[str, list] = {}
        for p in same_kept + same_moved:
            groups.setdefault(p.entry.path.name.casefold(), []).append(p)
        for items in list(groups.values())[:max_rows]:
            out.append(f"  • {items[0].entry.path.name}")
            for p in items:
                if p.action == KEEP:
                    out.append(f"      ΚΡΑΤΙΕΤΑΙ  {_rel(p.entry.path, root)}")
                else:
                    out.append(f"      ΣΤΟΝ ΕΛΕΓΧΟ {_rel(p.entry.path, root)}  → {_rel(p.dest, root)}")
                    out.append(f"          Λόγος: {p.reason}")
    block("Μένουν στη θέση τους:", keeps)
    return "\n".join(out)


def format_preview(model, max_rows: int = 500) -> str:
    """Αριθμημένος πίνακας προεπισκόπησης. Τα ενεργά πρώτα· οι αφαιρεμένες σημειώνονται."""
    root = model.root
    out = [f"Προεπισκόπηση: {len(model.active)} από {len(model.rows)} γραμμές θα εκτελεστούν."]
    for r in model.rows[:max_rows]:
        p = r.proposal
        mark = "[αφαιρέθηκε] " if r.number in model.removed else ""
        where = "ΦΑΚΕΛΟΣ ΕΛΕΓΧΟΥ" if p.action == "review" else "μετακίνηση"
        out.append(f"{r.number:>4}. {mark}{_rel(p.entry.path, root)}")
        out.append(f"      → {_rel(p.dest, root)}   ({where})")
        out.append(f"      Λόγος [{p.rule}]: {p.reason}")
    if len(model.rows) > max_rows:
        out.append(f"… και {len(model.rows) - max_rows} ακόμη γραμμές (δεν εμφανίζονται εδώ).")
    return "\n".join(out)


def format_runs(runs) -> str:
    if not runs:
        return "Δεν υπάρχουν καταγεγραμμένες εκτελέσεις."
    out = ["Εκτελέσεις (οι πιο πρόσφατες πρώτα):"]
    for r in runs:
        out.append(f"  {r.run_id}   {r.started.replace('T', ' ')}   {r.moved} αρχεία   {r.label}")
        out.append(f"      φάκελος: {r.root}")
    return "\n".join(out)


def format_undo_preview(m, ops, max_rows: int = 200) -> str:
    out = [f"Θα επιστραφούν {len(ops)} αρχεία στην αρχική τους θέση (φάκελος: {m.root}):"]
    for op in ops[:max_rows]:
        out.append(f"  • {'/'.join(op.dest)}")
        out.append(f"      → {'/'.join(op.src)}")
    if len(ops) > max_rows:
        out.append(f"  … και {len(ops) - max_rows} ακόμη.")
    return "\n".join(out)
