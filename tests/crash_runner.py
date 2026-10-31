"""Εκτελείται σε ξεχωριστή διεργασία από τις δοκιμές: σκοτώνεται απότομα στη μέση της μετακίνησης."""
import os
import sys
from pathlib import Path

from organotis import mover
from organotis.approval import PreviewModel
from organotis.config import Config
from organotis.planner import build_plan
from organotis.scanner import build_report, scan

root, mdir, mode, nth = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
cfg = Config()
res = scan(root, cfg)
approved = PreviewModel(build_plan(res, build_report(res), cfg)).approve()

real, count = mover._rename, 0


def crashing(src, dest):
    global count
    count += 1
    if count == nth:
        if mode == "after":
            real(src, dest)  # η μετακίνηση έγινε, αλλά το «done» δεν γράφτηκε
        os._exit(9)
    real(src, dest)


mover._rename = crashing
mover.execute(approved, mdir)
