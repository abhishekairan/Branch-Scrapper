# Branch Scrapper v0.1.2

This release adds **in-app updates**, a **Settings** screen (theme and data folder), grid options for the live
browser tiles, a fix for the Single-view preview size, and keeps the scraper's Chrome windows out of the taskbar.

## What's new

- **In-app updates.** Branch Scrapper can now tell you when a new version is out and install it for you.
  It checks when it starts (at most once a day) and shows a bar at the top with **What's new**,
  **Install & restart** and **Later**. Nothing is installed without your click. You can turn the automatic check
  off, or check by hand, under **Settings > Updates**. Every update is checked against a cryptographic signature
  before it runs. If a run is active, stop it first; the app closes its backend cleanly before installing, and your
  data is not touched.
- **Settings screen.** Choose a **System / Light / Dark** theme (the title bar follows it), and see or change the
  **data folder**. Changing it copies your database, settings and logs to the folder you pick and checks the
  copy; the old folder is left untouched, and you can go back to the default location.
- **Grid options** on the Browsers screen: number of columns (Auto, 1 to 4), tile size (Compact, Comfortable,
  Large) and whether to show the page URL, the status caption and the RAM of each tile.
- **Single view preview sizing.** The live preview now always fits the area it is shown in, keeps the browser
  window's proportions and is requested at the size it is really drawn, instead of overflowing or stretching when
  the window is resized.
- **Taskbar icons hidden (Windows).** The scraper's Chrome windows stay headed and parked off-screen, and are now
  also kept out of the taskbar and Alt-Tab. Your own Chrome windows are never touched. It can be turned off with
  `--show-taskbar-icons` in the command-line tool.

## Updating

- **From v0.1.1 or older: install this version by hand once.** Earlier versions cannot update themselves.
  Download `BranchScrapper_0.1.2_x64-setup.exe` below and run it; it replaces the old version in place and keeps
  your data.
- **From v0.1.2 on, updates are automatic** (offered in the app, as described above).
- You can also run a newer installer by hand, or (once the winget package is approved) use
  `winget upgrade AbhishekAiran.BranchScrapper`.
  Your data folder (`%APPDATA%\BranchScrapper`) is kept in every case.

## Install

1. Download `BranchScrapper_0.1.2_x64-setup.exe` below.
2. Run it (per-user install, no admin rights). Windows SmartScreen will say "Windows protected your PC"
   because the installer is not code-signed: click **More info**, then **Run anyway**.
3. You need Google Chrome (or Microsoft Edge / Brave) installed. WebView2 is downloaded if missing.

Your data is stored in `%APPDATA%\BranchScrapper` and is kept when you uninstall.

SHA256: __INSTALLER_SHA256__

## Known limitations

- Only Windows has been tested. Linux and macOS are not supported yet.
- Not yet tested: recovery from a real Chrome crash, logged-in Chrome profiles, Edge and Brave.
- Unsigned installer (the update signature is separate from Windows code signing), so SmartScreen warns on first
  run. Some antivirus products may flag the bundled `branch-scrapper-sidecar.exe`; if so, add an exclusion for
  the install folder.
- GitHub search returns at most 1000 results per query.

## Responsible use

Respect GitHub's Terms of Service and rate limits. For large-scale or commercial use, prefer the official API.

## Support

Free and optional: [Buy Me a Coffee](https://buymeacoffee.com/abhishekairan) |
[PayPal](https://paypal.me/AbhishekAiran001) | UPI `abhishekairan1234@okhdfcbank`

Report problems at https://github.com/abhishekairan/Branch-Scrapper/issues
