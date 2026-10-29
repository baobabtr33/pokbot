"""No SDK: reads protocol JSON lines on stdin, writes one capital letter per decision."""

import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if msg["type"] == "action_request":
        print("X" if "X" in msg["legal"] else "C", flush=True)
    elif msg["type"] == "match_end":
        break
