"""Make the project root importable as ``modules`` during test collection.

``modules`` is a namespace package (no top-level ``__init__.py``) and the
project is installed with ``package = false``, so ``import modules...`` only
works when the project root is on ``sys.path``. Living at the repo root, this
conftest guarantees pytest puts that root on the path regardless of where the
test run is launched from.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
