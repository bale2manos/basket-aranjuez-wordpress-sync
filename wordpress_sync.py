from __future__ import annotations

import site
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (SRC_DIR, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from basket_wordpress_sync.cli.main import main


if __name__ == "__main__":
    raise SystemExit(main())
