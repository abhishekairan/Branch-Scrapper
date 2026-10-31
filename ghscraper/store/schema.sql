-- Schema v1. Applied by db.py inside one transaction; user_version is set to 1 afterwards.
-- Text keys use COLLATE NOCASE so lookups/filters are case-insensitive and still use the indexes.

CREATE TABLE runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    config_json TEXT NOT NULL DEFAULT '{}',
    counts_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE repos (
    full_name           TEXT PRIMARY KEY COLLATE NOCASE,
    name                TEXT,
    owner               TEXT,
    url                 TEXT,
    description         TEXT,
    website             TEXT,
    language            TEXT COLLATE NOCASE,
    license             TEXT COLLATE NOCASE,
    default_branch      TEXT,
    stars               INTEGER,
    forks               INTEGER,
    watchers            INTEGER,
    branches            INTEGER,
    tags                INTEGER,
    releases            INTEGER,
    commits             INTEGER,
    contributors        INTEGER,
    open_issues         INTEGER,
    open_pull_requests  INTEGER,
    created_at          TEXT,
    updated_at          TEXT,
    last_commit_at      TEXT,
    archived            INTEGER,   -- 0/1/NULL
    is_fork             INTEGER,
    is_template         INTEGER,
    error               TEXT,
    scraped_at          TEXT,
    raw_json            TEXT NOT NULL
);

CREATE INDEX idx_repos_stars          ON repos(stars);
CREATE INDEX idx_repos_forks          ON repos(forks);
CREATE INDEX idx_repos_watchers       ON repos(watchers);
CREATE INDEX idx_repos_contributors   ON repos(contributors);
CREATE INDEX idx_repos_open_issues    ON repos(open_issues);
CREATE INDEX idx_repos_open_prs       ON repos(open_pull_requests);
CREATE INDEX idx_repos_commits        ON repos(commits);
CREATE INDEX idx_repos_releases       ON repos(releases);
CREATE INDEX idx_repos_language       ON repos(language);
CREATE INDEX idx_repos_license        ON repos(license);
CREATE INDEX idx_repos_created_at     ON repos(created_at);
CREATE INDEX idx_repos_last_commit_at ON repos(last_commit_at);
CREATE INDEX idx_repos_archived       ON repos(archived);
CREATE INDEX idx_repos_is_fork        ON repos(is_fork);
CREATE INDEX idx_repos_is_template    ON repos(is_template);

CREATE TABLE repo_topics (
    repo  TEXT NOT NULL COLLATE NOCASE REFERENCES repos(full_name) ON DELETE CASCADE,
    topic TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (repo, topic)
) WITHOUT ROWID;
CREATE INDEX idx_repo_topics_topic ON repo_topics(topic, repo);

CREATE TABLE repo_tech (
    repo TEXT NOT NULL COLLATE NOCASE REFERENCES repos(full_name) ON DELETE CASCADE,
    tech TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (repo, tech)
) WITHOUT ROWID;
CREATE INDEX idx_repo_tech_tech ON repo_tech(tech, repo);

CREATE TABLE repo_languages (
    repo     TEXT NOT NULL COLLATE NOCASE REFERENCES repos(full_name) ON DELETE CASCADE,
    language TEXT NOT NULL COLLATE NOCASE,
    pct      REAL NOT NULL,
    PRIMARY KEY (repo, language)
) WITHOUT ROWID;
CREATE INDEX idx_repo_languages_lang ON repo_languages(language, pct, repo);

-- Provenance: which run found which repo for which query. Accumulates across re-scrapes.
CREATE TABLE repo_runs (
    repo   TEXT NOT NULL COLLATE NOCASE REFERENCES repos(full_name) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    query  TEXT NOT NULL,
    PRIMARY KEY (repo, run_id, query)
);
CREATE INDEX idx_repo_runs_query ON repo_runs(query, repo);
CREATE INDEX idx_repo_runs_run   ON repo_runs(run_id, repo);

CREATE TABLE datasets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    filter_json  TEXT NOT NULL DEFAULT '{}',
    columns_json TEXT NOT NULL DEFAULT '[]',
    sort_json    TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Standalone FTS5 table, rowid = repos.rowid, kept in sync by db.py (upsert/delete).
CREATE VIRTUAL TABLE repos_fts USING fts5(
    full_name, description, topics, website,
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '2 3 4'
);
