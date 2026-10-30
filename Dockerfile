# MCP server for AI agents: `pokbot mcp` over stdio, talking to the public BOT Arena server.
# docker build -t pokbot-mcp . && docker run -i --rm pokbot-mcp
FROM python:3.12-slim
WORKDIR /app
COPY core ./core
COPY cli ./cli
RUN pip install --no-cache-dir ./core ./cli
ENV POKBOT_SERVER=https://100-58-68-17.sslip.io
ENTRYPOINT ["pokbot", "mcp"]
