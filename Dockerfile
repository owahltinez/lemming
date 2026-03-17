FROM python:3.11-slim

# Install cloudflared (architecture-aware)
RUN apt-get update && apt-get install -y wget && \
    ARCH=$(dpkg --print-architecture) && \
    wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb && \
    dpkg -i cloudflared-linux-${ARCH}.deb && \
    rm cloudflared-linux-${ARCH}.deb && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY . .
RUN uv sync --no-dev --frozen

ENTRYPOINT ["uv", "run", "lemming", "serve", "--host", "0.0.0.0"]
