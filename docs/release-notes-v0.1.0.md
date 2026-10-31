# Ghost Scraper v0.1.0

First public release: a Windows desktop app that scrapes GitHub repository search results with hidden Chrome
windows, stores them locally and lets you search, filter and export them.

## What is in it

- **Run**: queries and filters with validation, preview of GitHub's match counts, start/stop, live log.
- **Browsers**: live tiles that stream only while visible, and Take control to click and type in a worker.
- **Data**: full-text search, filters, saved datasets, JSON and CSV export, import of old CLI results.
- Several workers share one Chrome as separate contexts to save RAM.
- The command-line scraper still works from a source checkout.
- Licensed AGPL-3.0; third-party licences are in `THIRD_PARTY_NOTICES.md`.

## Install

1. Download `Ghost Scraper_0.1.0_x64-setup.exe` below.
2. Run it (per-user install, no admin rights). Windows SmartScreen will say "Windows protected your PC"
   because the installer is not code-signed: click **More info**, then **Run anyway**.
3. You need Google Chrome (or Microsoft Edge / Brave) installed. WebView2 is downloaded if missing.

Your data is stored in `%APPDATA%\GhostScraper` and is kept when you uninstall.

SHA256: 63669b149432551d463b0dbc81d6c4c9645654811652017bb3a0cd369c34afbe

## Known limitations

- Only Windows has been tested. Linux and macOS are not supported yet.
- Not yet tested: recovery from a real Chrome crash, logged-in Chrome profiles, Edge and Brave.
- Unsigned installer, so SmartScreen warns on first run. Some antivirus products may flag the bundled
  `ghscraper-sidecar.exe`; if so, add an exclusion for the install folder.
- GitHub search returns at most 1000 results per query.

## Responsible use

Respect GitHub's Terms of Service and rate limits. For large-scale or commercial use, prefer the official API.

## Support

Free and optional: [Buy Me a Coffee](https://buymeacoffee.com/abhishekairan) |
[PayPal](https://paypal.me/AbhishekAiran001) | UPI `abhishekairan1234@okhdfcbank`

Report problems at https://github.com/abhishekairan/Branch-Scrapper/issues
