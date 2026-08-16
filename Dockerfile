FROM python:3.14-slim

# Install common dev tools
RUN apt-get update && apt-get install -y \
    git curl wget jq make gcc g++ openssh-client procps \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (LTS) and npm
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install AI coding CLIs (antigravity ships a native binary, not an npm package)
RUN npm install -g --allow-scripts=opencode-ai \
      @openai/codex @anthropic-ai/claude-code opencode-ai \
    && curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin

# Copy cloudflared and uv binaries directly from their official images
COPY --from=cloudflare/cloudflared:latest /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /opt/lemming
COPY . .
# The evals group carries the linters readability shells out to.
RUN uv sync --no-dev --group evals --frozen

# Frontend dependencies are always needed: postinstall vendors mancha into
# the web assets. The browsers are not, and they are more than half the
# image, so anything that will never drive one builds without them.
ARG INSTALL_BROWSERS=true
RUN npm install \
    && if [ "$INSTALL_BROWSERS" = "true" ]; then \
         npx playwright install --with-deps; \
       fi

# Pre-create the lemming home directory so users can bind-mount a .env file into it
RUN mkdir -p /root/.local/lemming

WORKDIR /workspace
ENTRYPOINT ["uv", "run", "--project", "/opt/lemming", "lemming", "serve", "--host", "0.0.0.0"]
