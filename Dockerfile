# Website (React + Vite), built once and served by the API at /
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
# The How it works and Credits pages render these files (web/src/pages/DocPage.tsx imports them).
COPY docs/how-it-works.md /docs/how-it-works.md
COPY CREDITS.md /CREDITS.md
RUN npm run build

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[dev]"

COPY tests ./tests
COPY tools ./tools
COPY config ./config
COPY --from=web /web/dist ./web/dist

# data/ (maps, observations, retained demos) and output/ are mounted volumes
RUN mkdir -p /app/data/maps /app/output /demos
ENV CS2A_MAPS_DIR=/app/data/maps CS2A_OUTPUT_DIR=/app/output

ENTRYPOINT ["cs2-analyzer"]
CMD ["--help"]
