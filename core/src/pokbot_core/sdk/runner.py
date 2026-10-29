"""`python -m pokbot_core.sdk.runner FILE [ClassName]`

Loads a user's Python bot. If the file defines a `Bot` subclass, runs it through the SDK
loop; otherwise the file is treated as a raw protocol script and run as __main__.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from pokbot_core.sdk import Bot, run


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python -m pokbot_core.sdk.runner FILE [ClassName]")
    path = Path(sys.argv[1]).resolve()
    wanted = sys.argv[2] if len(sys.argv) > 2 else None
    sys.path.insert(0, str(path.parent))

    namespace = runpy.run_path(str(path), run_name="__pokbot__")
    classes = {
        name: obj
        for name, obj in namespace.items()
        if isinstance(obj, type) and issubclass(obj, Bot) and obj is not Bot
    }
    if wanted:
        if wanted not in classes:
            sys.exit(f"{path.name} has no Bot subclass named {wanted}")
        run(classes[wanted]())
    elif len(classes) == 1:
        run(next(iter(classes.values()))())
    elif classes:
        sys.exit(f"{path.name} defines several bots ({', '.join(classes)}); use FILE:ClassName")
    else:
        runpy.run_path(str(path), run_name="__main__")


if __name__ == "__main__":
    main()
