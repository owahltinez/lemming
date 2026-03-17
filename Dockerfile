FROM python:3.11-slim

# Copy cloudflared and uv binaries directly from their official images
COPY --from=cloudflare/cloudflared:latest /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY . .
RUN uv sync --no-dev --frozen

ENTRYPOINT ["uv", "run", "lemming", "serve", "--host", "0.0.0.0"]
