"""Προεπισκόπηση και έγκριση (αρχή 1).

Μόνο ένα αντικείμενο `ApprovedPlan` μπορεί να δοθεί στον μεταφορέα, και
φτιάχνεται μόνο από το `PreviewModel.approve()`, αφού ο χρήστης έχει δει
τον πίνακα και έχει αφαιρέσει όσες γραμμές δεν θέλει.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .planner import MOVE, REVIEW, Plan, Proposal

_TOKEN = object()


@dataclass(frozen=True)
class Row:
    number: int          # αριθμός γραμμής στον πίνακα (1, 2, 3, …)
    proposal: Proposal


class ApprovedPlan:
    """Εγκεκριμένες μετακινήσεις. Δεν φτιάχνεται απευθείας: μόνο μέσω `approve()`."""

    def __init__(self, root: Path, proposals: tuple[Proposal, ...], removed: int, *, _token=None):
        if _token is not _TOKEN:
            raise TypeError("Το ApprovedPlan φτιάχνεται μόνο από την προεπισκόπηση (PreviewModel.approve).")
        self.root = root
        self.proposals = proposals
        self.removed = removed


class PreviewModel:
    def __init__(self, plan: Plan):
        self.root = plan.root
        self.rows: list[Row] = [
            Row(i, p) for i, p in enumerate(
                (p for p in plan.proposals if p.action in (MOVE, REVIEW)), start=1)
        ]
        self.removed: set[int] = set()

    def _check(self, numbers) -> list[int]:
        nums = list(numbers)
        valid = {r.number for r in self.rows}
        bad = [n for n in nums if n not in valid]
        if bad:
            raise ValueError("Δεν υπάρχουν οι γραμμές: " + ", ".join(map(str, bad)))
        return nums

    def remove(self, numbers) -> None:
        self.removed.update(self._check(numbers))

    def restore(self, numbers) -> None:
        self.removed.difference_update(self._check(numbers))

    @property
    def active(self) -> list[Row]:
        return [r for r in self.rows if r.number not in self.removed]

    def approve(self) -> ApprovedPlan:
        active = self.active
        if not active:
            raise ValueError("Δεν έμεινε καμία γραμμή για μετακίνηση.")
        return ApprovedPlan(self.root, tuple(r.proposal for r in active),
                            len(self.removed), _token=_TOKEN)


def parse_numbers(text: str) -> list[int]:
    """«1, 3, 5-7» → [1, 3, 5, 6, 7]. Σηκώνει ValueError με ελληνικό μήνυμα."""
    out: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a), int(b)
                if lo > hi:
                    raise ValueError
                out.extend(range(lo, hi + 1))
            else:
                out.append(int(part))
        except ValueError:
            raise ValueError(f"Δεν κατάλαβα το «{part}». Γράψε αριθμούς όπως 1, 3, 5-7.") from None
    return out
