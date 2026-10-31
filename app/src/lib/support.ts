// Single source of truth for the project's public links and the app version.
// The same links also appear in README.md, .github/FUNDING.yml and docs/release-notes-*.md — keep them in sync.

import { version } from '../../package.json' // named import so Vite tree-shakes the rest of package.json

export const APP_NAME = 'Ghost Scraper'
export const APP_VERSION: string = version
export const APP_DESCRIPTION = 'Scrape GitHub repository search results with hidden Chrome browsers, then search and export them.'
export const LICENSE_NAME = 'AGPL-3.0'

export const REPO_URL = 'https://github.com/abhishekairan/Branch-Scrapper'
export const ISSUES_URL = `${REPO_URL}/issues`
export const RELEASES_URL = `${REPO_URL}/releases`
export const LICENSE_URL = `${REPO_URL}/blob/main/LICENSE`

export const BMC_URL = 'https://buymeacoffee.com/abhishekairan'
export const PAYPAL_URL = 'https://paypal.me/AbhishekAiran001'
/** Shown as text with a Copy button; `upi://` links are deliberately not opened (the opener is https-only). */
export const UPI_ID = 'abhishekairan1234@okhdfcbank'
