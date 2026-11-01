# SignPath Foundation application notes (for the maintainer)

Working notes for applying to the free [SignPath Foundation](https://signpath.org) open-source code signing
program and wiring it into CI. Not user-facing documentation. Checked against signpath.org and
docs.signpath.io on 2026-11-01; re-check before applying, the terms page is marked as a draft.

## 1. Answers for the application form

The apply page (https://signpath.org/apply) is rendered by script and its field list could not be retrieved
automatically, so the exact form fields are not confirmed. These are the facts the form is expected to need:

| Item | Value |
|---|---|
| Project name | Branch Scrapper (formerly "Ghost Scraper" up to v0.1.0) |
| Repository | https://github.com/abhishekairan/Branch-Scrapper |
| Project website / download page | Same repository (README and Releases page) |
| Licence | AGPL-3.0-only (OSI-approved, no commercial dual licence) |
| Description | Windows desktop app that searches GitHub for repositories and collects per-repo data (stars, forks, branches, languages, tech stack, ...) with a hidden local Chrome, stores it in a local SQLite database and exports JSON/CSV. Tauri 2 shell, Svelte UI, Python sidecar frozen with PyInstaller. |
| Maintainer / applicant | Abhishek Airan, GitHub `abhishekairan`, contact@abhishekairan.com |
| Build system | GitHub Actions, workflow `.github/workflows/build-windows.yml` (GitHub-hosted `windows-latest`, triggered by `v*` tags or manual dispatch) |
| Artifact to sign | NSIS installer `Branch Scrapper_<version>_x64-setup.exe` (per-user, no admin rights) |
| Code signing policy page | `docs/code-signing-policy.md` (linked from the README download section; also link it from each release's notes) |
| Privacy statement | In the policy page: no telemetry; network only to github.com, raw.githubusercontent.com (when the user starts a scrape), links opened in the system browser, and Microsoft's WebView2 bootstrapper if missing |
| Releases | v0.1.0 and v0.1.1 published (unsigned) |

## 2. Requirements and where the project stands

Source: https://signpath.org/terms (and docs.signpath.io). "Met" means checked in this repo; "Owner" means
only the owner can confirm or do it.

| Requirement | Status |
|---|---|
| OSI-approved licence, no commercial dual licensing | Met (AGPL-3.0-only) |
| No proprietary / non-open-source components (system libraries allowed) | Met as far as `THIRD_PARTY_NOTICES.md` shows: all listed components are open source. The Microsoft WebView2 bootstrapper is downloaded at install time, not bundled. The frozen sidecar contains the Microsoft C runtime DLLs shipped with Python/PyInstaller (system libraries). Rust transitive crates are not enumerated in the notices file. |
| Actively maintained, already released in the form to be signed | Met (v0.1.1 released as the same NSIS installer) |
| Functionality described on the download page | Met (README) |
| Code signing policy page using the wording "Free code signing provided by SignPath.io, certificate by SignPath Foundation", linked from home and download/release pages | Policy page written. README links it. **Not yet:** link it from the Releases page text (add to release notes), and keep the "pending" notice until approved, then remove it. |
| Defined roles (committers, reviewers, approvers) | Documented, but one person holds every role. Terms ask for an approver "trusted by the entire team"; confirm a sole maintainer is acceptable. |
| Multi-factor authentication for all team members on SignPath and GitHub | **Owner:** confirm 2FA on the GitHub account and enable MFA on SignPath. |
| Own binaries only, own repo and build scripts | Met: sources and build scripts are in this repo; the workflow builds from source. |
| Binaries built from source "in a verifiable way" (automated build from the named repo) | Workflow added but **not yet run on a tag**. Build is automated and CI-hosted, not bit-for-bit reproducible: the Python transitive dependencies are not pinned (only direct ones in `requirements.txt`), and `tauri build` downloads NSIS at build time. Cargo.lock and package-lock.json are committed. SignPath asks for verifiable, not bit-identical builds. |
| Metadata enforced on signed binaries: product name = project name, same product version on every build | **To do:** the NSIS installer and the Tauri shell carry version info; the PyInstaller sidecar exe has no version resource (the spec sets none). Add one if the sidecar exe is to be signed, and define the restrictions in the SignPath artifact configuration. |
| Signed packages: unsigned upstream binaries are allowed, but SignPath "reserve the right to require that signed packages only include signed program and library files" | **Open risk.** The installer includes ~138 sidecar files (Python DLLs, `.pyd`). Plan: first sign only the installer. If SignPath requires signed contents, restructure into two signing passes (sign `branch-scrapper.exe` and `branch-scrapper-sidecar.exe` before NSIS packaging, then the installer). The SignPath docs found do not document an NSIS-specific artifact element; deep signing is documented for MSI, so nested files inside an NSIS installer cannot be signed in a single request as far as verified. |
| No hacking tools: no features "designed to identify or exploit security vulnerabilities" and no circumvention of security measures | **Judgement call, raise it in the application.** The app does not look for vulnerabilities. It does include a "stealth" module (human-like typing and scrolling, randomised pauses, shared rate-limit cooldown with backoff) and is built on a CDP browser driver, to scrape GitHub pages like a person would and to back off on rate limits. SignPath may read this as bot-detection evasion. Describe it plainly (it respects GitHub rate-limit pages by backing off; it does not solve CAPTCHAs or bypass authentication). The module and README wording use "stealth", which reads worse than the behaviour is. |
| Privacy: no unspecified data transfers | Met, see the policy page. Verified by grepping the code: the only network calls are the browser loading github.com, a `urllib` fetch of raw.githubusercontent.com in `ghscraper/techstack.py`, https links opened through the opener plugin, and the sidecar's `127.0.0.1` WebSocket. No updater, telemetry or analytics. |
| Uninstallation provided | Met (NSIS uninstaller, `uninstall.exe`) |
| Every release needs manual approval | Set the SignPath signing policy to require approval; the approver is the maintainer. |
| Help SignPath with complaints / root-cause analysis | Stated in the policy page. |

## 3. After approval: SignPath side

1. In the SignPath organization, add the predefined **GitHub.com** trusted build system and link it to the project.
2. Create the project (slug e.g. `branch-scrapper`), a signing policy (slug e.g. `release-signing`, manual
   approval, origin restrictions to this repo and ideally to tags), and an artifact configuration.
3. Suggested artifact configuration for the first iteration (the zip comes from `actions/upload-artifact`'s
   default archive of one file; the root must therefore be `zip-file`; element names follow
   https://docs.signpath.io/artifact-configuration/syntax and were not test-run):

   ```xml
   <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
     <zip-file>
       <pe-file path="*_x64-setup.exe">
         <authenticode-sign/>
       </pe-file>
     </zip-file>
   </artifact-configuration>
   ```

   Add file metadata restrictions (`product-name` = `Branch Scrapper`, `product-version` fixed per release)
   as the terms require.
4. Create a SignPath user with submitter rights for the project and an API token for it.

## 4. After approval: GitHub repository settings

Repository -> Settings -> Secrets and variables -> Actions.

| Name | Kind | Value |
|---|---|---|
| `SIGNPATH_API_TOKEN` | Secret | API token of the SignPath CI user |
| `SIGNPATH_ORGANIZATION_ID` | Variable | SignPath organization ID (GUID from the SignPath UI) |
| `SIGNPATH_PROJECT_SLUG` | Variable | Project slug, e.g. `branch-scrapper` |
| `SIGNPATH_SIGNING_POLICY_SLUG` | Variable | Signing policy slug, e.g. `release-signing` |
| `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Variable | Artifact configuration slug (optional; empty uses the project's default configuration) |
| `SIGNPATH_ENABLED` | Variable | `true` (set last; until then the `sign` job is skipped and the workflow still passes) |

When enabled, `build` additionally uploads the installer alone as `installer-for-signpath`, the `sign` job
submits it with `signpath/github-action-submit-signing-request@v3`, checks `Get-AuthenticodeSignature` is
`Valid`, and uploads `signed-installer` (exe + `.sha256`). The workflow never creates a GitHub release: attach
the signed installer to the release yourself. Also enable MFA, and in SignPath restrict the policy's origin to
this repository so a forked workflow cannot request a signature.
