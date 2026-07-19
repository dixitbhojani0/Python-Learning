import sys
from pathlib import Path

# Make `tools`, `constants`, `core` importable when pytest runs from anywhere.
sys.path.insert(0, str(Path(__file__).parent.parent))
