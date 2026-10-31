"""Σημείο εισόδου του EXE (το PyInstaller χρειάζεται σενάριο στο ανώτατο επίπεδο)."""
import sys

from organotis import winconsole
from organotis.cli import main

if __name__ == "__main__":
    winconsole.attach_if_needed()
    sys.exit(main())
