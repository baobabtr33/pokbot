"""Deliberately bad bot for testing validation: answers garbage and sometimes stalls."""

import time

from pokbot_core.sdk import Bot


class Broken(Bot):
    n = 0

    def act(self, state):
        self.n += 1
        if self.n % 7 == 0:
            time.sleep(1.5)
        return "BET A LOT" if self.n % 3 == 0 else "R 1"
