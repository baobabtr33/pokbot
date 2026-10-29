"""Always calls, never raises. The simplest SDK bot."""

from pokbot_core.sdk import Bot


class Caller(Bot):
    def act(self, state):
        return "C" if "C" in state["legal"] else "X"
