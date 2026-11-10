import sys
from pathlib import Path

# The pipeline's scripts import each other as top-level modules (they run as scripts)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
