from __future__ import annotations

import site
import os
import sys
from pathlib import Path


ROOT_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
BUNDLED_BROWSER_DIR = ROOT_DIR / "browsers"
if getattr(sys, "frozen", False) and BUNDLED_BROWSER_DIR.exists():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BUNDLED_BROWSER_DIR))
SRC_DIR = ROOT_DIR / "src"
USER_SITE = Path(site.getusersitepackages())
for candidate in (SRC_DIR, USER_SITE):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from basket_wordpress_sync.cli.main import main


if __name__ == "__main__":
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        from basket_wordpress_sync.gui import run_gui

        run_gui(ROOT_DIR)
    else:
        raise SystemExit(main())
