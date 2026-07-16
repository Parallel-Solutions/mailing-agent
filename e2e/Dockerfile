FROM mcr.microsoft.com/playwright:v1.61.1-noble

WORKDIR /e2e

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV CI=1

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json playwright.config.ts ./
COPY tests ./tests
COPY scripts ./scripts

# Browsers already present in the official image; do not reinstall.
CMD ["npx", "playwright", "test"]
