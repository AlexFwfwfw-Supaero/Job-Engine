from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from jobhunt.snapshot import default_client
from jobhunt.store import Store


@dataclass
class Deps:
    """Everything the web layer needs from the outside world.

    Injected rather than imported so tests can supply a temporary database,
    a pinned date, and a fake HTTP client.
    """

    store_factory: Callable[[], Store]
    config_dir: Path
    today: Callable[[], date]
    http_client: Callable[[], object]

    @classmethod
    def from_env(cls) -> "Deps":
        def make_store() -> Store:
            store = Store(Path(os.environ.get("JOBHUNT_DB", "data/jobs.db")))
            store.initialize()
            return store

        return cls(
            store_factory=make_store,
            config_dir=Path(os.environ.get("JOBHUNT_CONFIG", "config")),
            today=date.today,
            http_client=default_client,
        )
