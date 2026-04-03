FROM python:3.14-slim

# Install common dev tools
RUN apt-get update && apt-get install -y \
    git curl wget jq make gcc g++ openssh-client procps \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS) and npm
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install AI coding CLIs
RUN npm install -g @google/gemini-cli @openai/codex @anthropic-ai/claude-code

# Copy cloudflared and uv binaries directly from their official images
COPY --from=cloudflare/cloudflared:latest /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /opt/lemming
COPY . .
RUN uv sync --no-dev --frozen

# Install frontend dependencies and Playwright browsers
RUN npm install && npx playwright install --with-deps

WORKDIR /workspace
ENTRYPOINT ["uv", "run", "--project", "/opt/lemming", "lemming", "serve", "--host", "0.0.0.0"]
