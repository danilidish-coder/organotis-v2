"""Σε ξεχωριστή διεργασία: κάνει αναίρεση και σκοτώνεται απότομα στη N-οστή επιστροφή αρχείου."""
import os
import sys
from pathlib import Path

from organotis import mover, undo

path, mode, nth = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
real, count = mover._rename, 0


def crashing(src, dest):
    global count
    count += 1
    if count == nth:
        if mode == "after":
            real(src, dest)
        os._exit(9)
    real(src, dest)


mover._rename = crashing
undo.undo(path)
