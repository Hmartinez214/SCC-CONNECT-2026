import sys
from pathlib import Path

LAUNCHPAD = Path(__file__).resolve().parents[1]
for sub in ("", "hpl", "stream"):
    sys.path.insert(0, str(LAUNCHPAD / sub))
