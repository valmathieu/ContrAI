# syntax=docker/dockerfile:1
# contrai-scraper, headless, as a non-root user. Build from the repository root:
#   docker compose -f deploy/compose.yml build scraper
# Playwright's image ships browsers and system libraries for exactly one
# Playwright version; tests/test_deploy.py keeps this tag equal to uv.lock's.
FROM mcr.microsoft.com/playwright/python:v1.59.0-noble

ENV DEBIAN_FRONTEND=noninteractive
# iproute2 answers the egress gate's route question. The colour emoji font is
# not optional: a table's kind is shown as a glyph.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      iproute2 fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /uvx /bin/

# The image's own Python is 3.12; the workspace needs 3.14, which uv installs as
# a managed build. The browsers stay where the image installed them. HOME points
# at /tmp for Chromium's runtime user, so the uv cache is pinned to the build
# cache mount explicitly — otherwise it would follow HOME into an image layer.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/root/.cache/uv \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_PROJECT_ENVIRONMENT=/opt/contrai/venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/tmp

WORKDIR /src

# Third-party dependencies first, so a code change reinstalls only our own
# packages. Every member's manifest is copied: uv reads the
# whole workspace the lock was made for.
COPY .python-version pyproject.toml uv.lock ./
COPY packages/contrai-core/pyproject.toml packages/contrai-core/README.md packages/contrai-core/
COPY packages/contrai-data/pyproject.toml packages/contrai-data/README.md packages/contrai-data/
COPY packages/contrai-engine/pyproject.toml packages/contrai-engine/README.md packages/contrai-engine/
COPY packages/contrai-analyzer/pyproject.toml packages/contrai-analyzer/README.md packages/contrai-analyzer/
COPY packages/contrai-scraper/pyproject.toml packages/contrai-scraper/README.md packages/contrai-scraper/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package contrai-scraper

COPY packages/contrai-core/src packages/contrai-core/src
COPY packages/contrai-data/src packages/contrai-data/src
COPY packages/contrai-scraper/src packages/contrai-scraper/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --package contrai-scraper

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin contrai
USER 10001
ENTRYPOINT ["/opt/contrai/venv/bin/contrai-scrape"]
CMD ["run", "--profile", "/etc/contrai/profile.toml", "--headless"]
