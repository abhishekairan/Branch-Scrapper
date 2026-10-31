"""Tech-stack detection from languages, root files, topics and manifests."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# Root file / directory name -> technology. Matched on the first path segment
# and on the basename, case-insensitively.
FILE_SIGNATURES: dict[str, str] = {
    "package.json": "Node.js", "tsconfig.json": "TypeScript", "deno.json": "Deno",
    "bun.lockb": "Bun", "bun.lock": "Bun", "pnpm-lock.yaml": "pnpm", "yarn.lock": "Yarn",
    "turbo.json": "Turborepo", "nx.json": "Nx", "lerna.json": "Lerna",
    "requirements.txt": "Python", "pyproject.toml": "Python", "setup.py": "Python",
    "pipfile": "Python (Pipenv)", "poetry.lock": "Poetry", "uv.lock": "uv",
    "manage.py": "Django", "environment.yml": "Conda",
    "cargo.toml": "Rust (Cargo)", "go.mod": "Go", "pom.xml": "Java (Maven)",
    "build.gradle": "Gradle", "build.gradle.kts": "Gradle (Kotlin)", "gemfile": "Ruby (Bundler)",
    "composer.json": "PHP (Composer)", "mix.exs": "Elixir", "pubspec.yaml": "Flutter/Dart",
    "package.swift": "Swift PM", "podfile": "CocoaPods", "cmakelists.txt": "CMake",
    "makefile": "Make", "meson.build": "Meson", "build.zig": "Zig", "stack.yaml": "Haskell (Stack)",
    "dockerfile": "Docker", "docker-compose.yml": "Docker Compose", "docker-compose.yaml": "Docker Compose",
    "compose.yml": "Docker Compose", "compose.yaml": "Docker Compose", "chart.yaml": "Helm",
    "vercel.json": "Vercel", "netlify.toml": "Netlify", "procfile": "Heroku", "fly.toml": "Fly.io",
    "serverless.yml": "Serverless Framework", "main.tf": "Terraform", "pulumi.yaml": "Pulumi",
    "mkdocs.yml": "MkDocs", "docusaurus.config.js": "Docusaurus", "_config.yml": "Jekyll",
    ".pre-commit-config.yaml": "pre-commit", "tox.ini": "tox", ".eslintrc.json": "ESLint",
    "eslint.config.js": "ESLint", "eslint.config.mjs": "ESLint", "biome.json": "Biome",
    ".prettierrc": "Prettier", ".prettierrc.json": "Prettier", "jest.config.js": "Jest",
    "vitest.config.ts": "Vitest", "playwright.config.ts": "Playwright", "cypress.config.ts": "Cypress",
    ".travis.yml": "Travis CI", ".gitlab-ci.yml": "GitLab CI", "jenkinsfile": "Jenkins",
    "prisma": "Prisma", "schema.prisma": "Prisma", "drizzle.config.ts": "Drizzle",
    "angular.json": "Angular", "svelte.config.js": "Svelte", "astro.config.mjs": "Astro",
    "remix.config.js": "Remix", "gatsby-config.js": "Gatsby", "electron-builder.yml": "Electron",
    "tauri.conf.json": "Tauri", "src-tauri": "Tauri", "app.json": "Expo/React Native",
    "android": "Android", "ios": "iOS", "crates": "Rust (Cargo workspace)",
}

# Prefix signatures (config files with many extensions).
PREFIX_SIGNATURES: dict[str, str] = {
    "next.config.": "Next.js", "nuxt.config.": "Nuxt", "vite.config.": "Vite",
    "webpack.config.": "Webpack", "rollup.config.": "Rollup", "tailwind.config.": "Tailwind CSS",
    "postcss.config.": "PostCSS", "babel.config.": "Babel", "svelte.config.": "Svelte",
    "astro.config.": "Astro", "jest.config.": "Jest", "vitest.config.": "Vitest",
    "playwright.config.": "Playwright", "cypress.config.": "Cypress", "drizzle.config.": "Drizzle",
    "eslint.config.": "ESLint", ".eslintrc": "ESLint", "tsconfig": "TypeScript",
}

SUFFIX_SIGNATURES: dict[str, str] = {
    ".csproj": ".NET", ".sln": ".NET", ".fsproj": ".NET (F#)", ".tf": "Terraform",
    ".cabal": "Haskell (Cabal)", ".gemspec": "Ruby gem", ".nimble": "Nim",
}

# Dependency name -> technology, used when manifests are fetched.
NODE_DEPS: dict[str, str] = {
    "react": "React", "next": "Next.js", "vue": "Vue", "nuxt": "Nuxt", "svelte": "Svelte",
    "@sveltejs/kit": "SvelteKit", "@angular/core": "Angular", "solid-js": "SolidJS",
    "preact": "Preact", "astro": "Astro", "@remix-run/react": "Remix", "gatsby": "Gatsby",
    "express": "Express", "fastify": "Fastify", "koa": "Koa", "@nestjs/core": "NestJS",
    "hono": "Hono", "electron": "Electron", "react-native": "React Native", "expo": "Expo",
    "tailwindcss": "Tailwind CSS", "@mui/material": "Material UI", "styled-components": "styled-components",
    "@chakra-ui/react": "Chakra UI", "redux": "Redux", "@reduxjs/toolkit": "Redux Toolkit",
    "zustand": "Zustand", "@tanstack/react-query": "TanStack Query", "graphql": "GraphQL",
    "@apollo/client": "Apollo", "prisma": "Prisma", "@prisma/client": "Prisma", "drizzle-orm": "Drizzle",
    "mongoose": "MongoDB (Mongoose)", "typeorm": "TypeORM", "sequelize": "Sequelize",
    "socket.io": "Socket.IO", "three": "Three.js", "d3": "D3.js", "puppeteer": "Puppeteer",
    "playwright": "Playwright", "cheerio": "Cheerio", "axios": "Axios", "zod": "Zod",
    "typescript": "TypeScript", "vite": "Vite", "webpack": "Webpack", "jest": "Jest",
    "vitest": "Vitest", "mocha": "Mocha", "storybook": "Storybook", "@storybook/react": "Storybook",
    "openai": "OpenAI SDK", "@anthropic-ai/sdk": "Anthropic SDK", "langchain": "LangChain",
    "firebase": "Firebase", "@supabase/supabase-js": "Supabase", "stripe": "Stripe",
    "better-auth": "Better Auth", "next-auth": "NextAuth", "trpc": "tRPC", "@trpc/server": "tRPC",
}

PY_DEPS: dict[str, str] = {
    "django": "Django", "flask": "Flask", "fastapi": "FastAPI", "starlette": "Starlette",
    "tornado": "Tornado", "aiohttp": "aiohttp", "sanic": "Sanic", "litestar": "Litestar",
    "streamlit": "Streamlit", "gradio": "Gradio", "dash": "Dash", "celery": "Celery",
    "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic", "requests": "Requests", "httpx": "HTTPX",
    "scrapy": "Scrapy", "selenium": "Selenium", "seleniumbase": "SeleniumBase",
    "playwright": "Playwright", "beautifulsoup4": "BeautifulSoup", "lxml": "lxml",
    "numpy": "NumPy", "pandas": "pandas", "polars": "Polars", "scipy": "SciPy",
    "scikit-learn": "scikit-learn", "torch": "PyTorch", "tensorflow": "TensorFlow",
    "keras": "Keras", "jax": "JAX", "transformers": "Hugging Face Transformers",
    "langchain": "LangChain", "llama-index": "LlamaIndex", "openai": "OpenAI SDK",
    "anthropic": "Anthropic SDK", "matplotlib": "Matplotlib", "plotly": "Plotly",
    "opencv-python": "OpenCV", "pillow": "Pillow", "pytest": "pytest", "click": "Click",
    "typer": "Typer", "rich": "Rich", "uvicorn": "Uvicorn", "gunicorn": "Gunicorn",
    "redis": "Redis", "psycopg2": "PostgreSQL", "psycopg": "PostgreSQL", "pymongo": "MongoDB",
    "boto3": "AWS SDK", "kafka-python": "Kafka", "pyspark": "Spark", "airflow": "Airflow",
    "apache-airflow": "Airflow", "pyqt5": "PyQt", "pyside6": "PySide", "kivy": "Kivy",
}

MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "Pipfile", "Cargo.toml", "go.mod", "composer.json")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def detect_from_files(root_files: list[str]) -> list[str]:
    found: list[str] = []
    for raw in root_files:
        for name in {raw.split("/")[0].lower(), raw.rsplit("/", 1)[-1].lower()}:
            tech = FILE_SIGNATURES.get(name)
            if not tech:
                tech = next((t for p, t in PREFIX_SIGNATURES.items() if name.startswith(p)), None)
            if not tech:
                tech = next((t for s, t in SUFFIX_SIGNATURES.items() if name.endswith(s)), None)
            if tech and tech not in found:
                found.append(tech)
    if any(f.lower().startswith(".github") for f in root_files):
        found.append("GitHub (.github config)")
    return found


def _fetch_raw(full_name: str, branch: str, path: str, timeout: int = 10) -> str | None:
    url = f"https://raw.githubusercontent.com/{full_name}/{branch}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(512_000).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _py_names(text: str) -> set[str]:
    names = set()
    for m in re.finditer(r"""(?m)^\s*["']?([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:[<>=!~;,"'\s]|$)""", text):
        names.add(m.group(1).lower().replace("_", "-"))
    return names


def detect_from_manifests(full_name: str, branch: str, root_files: list[str]) -> list[str]:
    """Fetch known manifests from raw.githubusercontent.com and map dependencies."""
    present = {f.lower() for f in root_files}
    found: list[str] = []

    def add(t: str) -> None:
        if t not in found:
            found.append(t)

    for manifest in MANIFESTS:
        if manifest.lower() not in present or not branch:
            continue
        text = _fetch_raw(full_name, branch, manifest)
        if not text:
            continue
        if manifest == "package.json":
            try:
                pkg = json.loads(text)
            except json.JSONDecodeError:
                continue
            deps: set[str] = set()
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps |= set((pkg.get(key) or {}).keys())
            for dep in sorted(deps):
                if dep in NODE_DEPS:
                    add(NODE_DEPS[dep])
        elif manifest in ("requirements.txt", "pyproject.toml", "Pipfile"):
            names = _py_names(text)
            for dep, tech in PY_DEPS.items():
                if dep in names:
                    add(tech)
        elif manifest == "Cargo.toml":
            for dep, tech in {"tokio": "Tokio", "actix-web": "Actix Web", "axum": "Axum",
                              "serde": "Serde", "rocket": "Rocket", "bevy": "Bevy",
                              "tauri": "Tauri", "wasm-bindgen": "WebAssembly"}.items():
                if re.search(rf"(?m)^\s*{re.escape(dep)}\s*=", text):
                    add(tech)
        elif manifest == "go.mod":
            for dep, tech in {"gin-gonic/gin": "Gin", "labstack/echo": "Echo", "gofiber/fiber": "Fiber",
                              "gorilla/mux": "Gorilla Mux", "gorm.io/gorm": "GORM",
                              "spf13/cobra": "Cobra", "grpc": "gRPC"}.items():
                if dep in text:
                    add(tech)
        elif manifest == "composer.json":
            for dep, tech in {"laravel/framework": "Laravel", "symfony/": "Symfony",
                              "guzzlehttp/guzzle": "Guzzle"}.items():
                if dep in text:
                    add(tech)
    return found


def build_tech_stack(repo: dict, fetch_manifests: bool) -> list[str]:
    stack: list[str] = []

    def extend(items: list[str]) -> None:
        for i in items:
            if i and i not in stack:
                stack.append(i)

    extend([lang for lang, pct in (repo.get("languages") or {}).items() if pct >= 1.0 and lang != "Other"])
    if not stack and repo.get("language"):
        stack.append(repo["language"])
    files = repo.get("root_files") or []
    extend(detect_from_files(files))
    if fetch_manifests:
        extend(detect_from_manifests(repo["full_name"], repo.get("default_branch", ""), files))
    return stack
