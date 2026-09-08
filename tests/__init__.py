from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)
