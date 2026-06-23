FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-dejavu-core \
        fonts-dejavu-extra \
        fonts-liberation \
        fontconfig \
        libreoffice \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
RUN .venv/bin/python -m playwright install --with-deps chromium
RUN uv pip install --python .venv/bin/python "pypdf>=6.0.0"

COPY . .
RUN mkdir -p /app/storage /app/logs /app/data /app/tmp

EXPOSE 9806

CMD [".venv/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9806", "--loop", "asyncio", "--http", "h11"]