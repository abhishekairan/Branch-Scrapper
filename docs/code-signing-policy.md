# Code signing policy

**Status: pending.** Branch Scrapper installers are currently **not signed**. This page describes how signing
will work once the project's application to the SignPath Foundation open-source program is approved. Until
then the attribution below does not yet apply, and Windows SmartScreen will warn about the installer.

## Attribution

Free code signing provided by [SignPath.io](https://signpath.io), certificate by
[SignPath Foundation](https://signpath.org). *(Pending approval.)*

## What is signed

Only the Windows installer (`Branch Scrapper_<version>_x64-setup.exe`) of Branch Scrapper is signed, and only if it
was built by this repository's GitHub Actions workflow
([`.github/workflows/build-windows.yml`](../.github/workflows/build-windows.yml)) from the source code in
[abhishekairan/Branch-Scrapper](https://github.com/abhishekairan/Branch-Scrapper), on a GitHub-hosted runner.
Nothing is signed from a developer's machine, and no binary built by anyone else is signed.

The installer contains the Tauri shell, the Python sidecar frozen with PyInstaller and third-party components,
all open source (see [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)). Executables and DLLs inside the
installer are meant to be signed with the same certificate where the SignPath artifact configuration
allows it; the signing request is checked against the project's file metadata restrictions (product name
`Branch Scrapper`, one product version per build).

Branch Scrapper is licensed under the [AGPL-3.0](../LICENSE). Every signed release is an automated build of a
tagged commit of this repository, and the workflow also publishes the unsigned installer and SHA-256 checksums
as build artifacts so the build can be compared.

## Roles

| Role | Who | Responsibility |
|---|---|---|
| Committers and reviewers | [Abhishek Airan](https://github.com/abhishekairan) | Write code; review contributions from anyone else before they are merged |
| Approvers | [Abhishek Airan](https://github.com/abhishekairan) | Manually approve every signing request in SignPath |

The project currently has a single maintainer, who holds all roles. Pull requests from outside contributors are
reviewed and merged by the maintainer; nobody else can approve a signing request. If more maintainers join, this
table is updated first and they are added to the SignPath project with the matching role. Everyone with signing
or repository write access must use multi-factor authentication.

## Privacy policy

Branch Scrapper does not collect or send telemetry, analytics, crash reports or usage data, and has no
update checker. It does not transfer any information to other networked systems unless the user specifically
requests it. The network activity the user can start is:

- **Scraping GitHub.** When the user starts a run, the app drives a local Chrome, Edge or Brave browser that loads
  `github.com` search and repository pages, and fetches a few manifest files from `raw.githubusercontent.com`
  for tech-stack detection. This is the app's purpose; GitHub sees these requests as it would from any browser,
  including from a GitHub account if the user signs in inside the scraper browser, and from a proxy if the user
  configures one.
- **Opening links.** Links the user clicks (a repository page, a repository's website, the project's
  donation pages) open in the system browser.
- **Installer.** If WebView2 is not already installed, the installer downloads Microsoft's WebView2 bootstrapper
  from Microsoft.

The app's own sidecar listens only on `127.0.0.1` with a per-launch token. Scraped data, settings and logs are
stored locally in `%APPDATA%\BranchScrapper` and are never uploaded. WebView2 and the browser used for scraping
have their own privacy policies (Microsoft, Google, Brave).

## Reporting problems

If you think a signed Branch Scrapper binary is malicious, was not built from this repository, or was signed in
error, please [open an issue](https://github.com/abhishekairan/Branch-Scrapper/issues/new) (or contact the
maintainer through the address on the GitHub profile for a private report). The maintainer will help SignPath
Foundation with verification and root-cause analysis of any complaint and will revoke or re-issue affected
releases.
