# Branch Scrapper v0.1.3

This release makes the database search understand several words, adds display options for the results table and
the browser grid, and lets you make the whole interface bigger or smaller.

## What's new

- **Search with several words.** Next to the search box on the Data screen there is now an
  **All words | Any word** switch. *All words* (as before) finds repositories that contain every word you type;
  *Any word* finds repositories that contain at least one of them, so `shopify woocommerce magento` returns
  everything about any of the three. Saved datasets remember the choice.
- **Table options** on the Data screen (the new **Table** button): row height (**Compact**, **Regular**, **Tall**;
  Tall shows two lines of long descriptions), striped rows, column lines, and keeping the Repository column in
  view while you scroll sideways.
- **More grid options** on the Browsers screen: up to **6 columns**, tile **spacing**, the **shape of the preview**
  (16:9, 16:10, 4:3) and **Hide finished browsers**.
- **Interface size.** Under **Settings > Interface size** you can scale the whole app from 80 % to 175 %, or use
  **Ctrl + =** (larger), **Ctrl + -** (smaller) and **Ctrl + 0** (back to 100 %). Remembered on this computer.

## Updating

- **From v0.1.2:** the app offers this update itself (or use **Settings > Updates > Check now**).
- **From v0.1.1 or older:** download `BranchScrapper_0.1.3_x64-setup.exe` below and run it once; it replaces the
  old version in place and keeps your data.
- Your data folder (`%APPDATA%\BranchScrapper`) is kept in every case.

## Install

1. Download `BranchScrapper_0.1.3_x64-setup.exe` below.
2. Run it (per-user install, no admin rights). Windows SmartScreen will say "Windows protected your PC"
   because the installer is not code-signed: click **More info**, then **Run anyway**.
3. You need Google Chrome (or Microsoft Edge / Brave) installed. WebView2 is downloaded if missing.

Your data is stored in `%APPDATA%\BranchScrapper` and is kept when you uninstall.

## Known limitations

- Only Windows has been tested. Linux and macOS are not supported yet.
- The interface size is applied a moment after the app starts, so the first frame can show at 100 %.
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
