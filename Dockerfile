# syntax=docker/dockerfile:1
FROM node:22-bookworm AS frontend-build
ENV NODE_OPTIONS=--max-old-space-size=3072
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm AS python-deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fontconfig \
        fonts-dejavu-core \
        fonts-dejavu-extra \
        fonts-freefont-ttf \
        fonts-ipafont-gothic \
        fonts-liberation \
        fonts-noto-color-emoji \
        fonts-tlwg-loma-otf \
        fonts-unifont \
        fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv

# Keep dependency installation independent from README and application-source
# changes. The project itself is not installed into the environment.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    touch README.md \
    && uv sync --frozen --no-dev --no-install-project \
    && rm -f README.md

FROM python-deps AS browser-deps

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libfontconfig1 \
        libfreetype6 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xfonts-scalable \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN .venv/bin/python -m playwright install chromium

FROM browser-deps AS runtime

COPY . .
COPY --from=frontend-build /frontend/dist /app/frontend/dist
RUN mkdir -p /app/storage /app/logs /app/data /app/tmp /app/src/parser_new/memory/vectors

EXPOSE 9806

CMD [".venv/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9806", "--loop", "asyncio", "--http", "h11"]

# Unit/integration image: reuse production Python and browser dependencies.
# Fixed-layout/PDF regression tests render with Chromium, so the browser stage
# is intentionally shared with runtime instead of duplicated.
# Build with: docker build --target test
FROM browser-deps AS test
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra dev --extra mcp --no-install-project
COPY . .
RUN mkdir -p /app/storage /app/logs /app/data /app/tmp /app/src/parser_new/memory/vectors
CMD [".venv/bin/python", "-m", "tests"]
