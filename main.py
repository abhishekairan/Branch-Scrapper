import sys

from ghscraper.cli import main

if __name__ == "__main__":
    # Redirected output on Windows defaults to cp1252, which can't encode the panel's symbols.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
