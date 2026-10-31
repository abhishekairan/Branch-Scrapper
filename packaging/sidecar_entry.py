"""PyInstaller entry point for the sidecar (the frozen equivalent of ``python -m ghscraper.sidecar``).

A separate script is needed because PyInstaller runs its entry script as ``__main__`` outside a package,
so ``ghscraper/sidecar.py`` (which uses relative imports) cannot be the entry itself.
"""
import logging
import multiprocessing
import os
import sys

from ghscraper.sidecar import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    code = main()
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)      # a daemon thread may still be blocked reading stdin (same as ghscraper/sidecar.py)
