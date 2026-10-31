# Branch Scrapper v0.1.1

The app formerly known as **Ghost Scraper** is now **Branch Scrapper**. This release is mostly the rename, plus
an automatic upgrade path so you keep your data.

## What changed

- **New name everywhere:** window title, installer, About screen, executables and data folder.
- **Your data is migrated automatically** (see below).
- **Upgrade installs cleanly:** the installer removes the old Ghost Scraper program for you.
- New promo video with its own music and sound effects (in the README and attached below).

## Upgrading from Ghost Scraper 0.1.0

Just run the new installer; there is nothing to do by hand.

1. **Old program:** the installer looks for the per-user Ghost Scraper entry in Windows' installed-apps list and
   runs its uninstaller silently before installing Branch Scrapper. That removes the old program files,
   shortcuts and registry entries. It keeps your data. Close the old app first. (If the automatic removal ever
   fails, uninstall Ghost Scraper from Windows Settings > Apps, then run the installer again.)
2. **Your data:** `%APPDATA%\GhostScraper` (`data.db`, `config.json`, `logs\`) is **copied** to
   `%APPDATA%\BranchScrapper` the first time Branch Scrapper starts, before anything else opens the database.
   The copy is made in a temporary folder next to the target and renamed into place, so you never end up with a
   half-copied folder. The database is checkpointed first, so a write-ahead log from the old app is included.
   If `%APPDATA%\BranchScrapper` already contains data, nothing is copied.
3. **The old folder is left in place, untouched,** except for a `MIGRATED_TO.txt` note in it. It is safe to
   delete once you have checked that Branch Scrapper shows your data. Nothing is ever deleted for you.
4. A one-time notice on the Run screen tells you the data was moved; dismiss it and it does not come back.
5. If copying fails (for example the disk is full), the app still starts with an empty data folder, shows a
   warning, and your old data stays where it was.
6. **UI preferences reset once** (for example the Browsers layout). The app now has a new identifier
   (`com.branchscrapper.desktop`), so Windows gives it a fresh WebView2 profile. Your searches, results and
   settings are in the data folder and are not affected.
7. The old app's browser profile folder `%LOCALAPPDATA%\com.ghostscraper.desktop` (cache and its log) is not
   touched. Nothing needs it any more; delete it if you want the space back.
8. If you set `GHOSTSCRAPER_HOME` yourself, no migration happens; that folder is used as it is. The environment
   variable names are unchanged.

## Install

1. Download `BranchScrapper_0.1.1_x64-setup.exe` below.
2. Run it (per-user install, no admin rights). Windows SmartScreen will say "Windows protected your PC"
   because the installer is not code-signed: click **More info**, then **Run anyway**.
3. You need Google Chrome (or Microsoft Edge / Brave) installed. WebView2 is downloaded if missing.

Your data is stored in `%APPDATA%\BranchScrapper` and is kept when you uninstall.

SHA256: 6546dfec6255d83cf300af10592772ce0bb5c70b98a140579a87afff39cf046a

## Known limitations

- Only Windows has been tested. Linux and macOS are not supported yet.
- Not yet tested: recovery from a real Chrome crash, logged-in Chrome profiles, Edge and Brave.
- Unsigned installer, so SmartScreen warns on first run. Some antivirus products may flag the bundled
  `branch-scrapper-sidecar.exe`; if so, add an exclusion for the install folder.
- GitHub search returns at most 1000 results per query.

## Responsible use

Respect GitHub's Terms of Service and rate limits. For large-scale or commercial use, prefer the official API.

## Support

Free and optional: [Buy Me a Coffee](https://buymeacoffee.com/abhishekairan) |
[PayPal](https://paypal.me/AbhishekAiran001) | UPI `abhishekairan1234@okhdfcbank`

Report problems at https://github.com/abhishekairan/Branch-Scrapper/issues
