FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MERCURY_TOOLS_MCP_TRANSPORT=streamable-http \
    MERCURY_TOOLS_HOST=0.0.0.0 \
    MERCURY_TOOLS_MCP_PATH=/mcp

WORKDIR /app

COPY pyproject.toml README.md LICENSE uv.lock ./
COPY src ./src
COPY plugins ./plugins
COPY wiki ./wiki
COPY supabase ./supabase

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["mercury-tools", "mcp", "serve", "--transport", "streamable-http"]
