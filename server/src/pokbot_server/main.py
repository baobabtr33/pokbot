"""`pokbot-server [--host H] [--port P] [--reload]`"""

from __future__ import annotations

import argparse
import logging

import uvicorn


def run() -> None:
    parser = argparse.ArgumentParser(prog="pokbot-server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    uvicorn.run(
        "pokbot_server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        ws_max_size=64 * 1024,  # protocol replies and chat lines are tiny; cap floods
        ws_ping_interval=20,
    )


if __name__ == "__main__":
    run()
