import sys
from pathlib import Path

# The pipeline's scripts import each other as top-level modules (they run as scripts). Other
# pipelines have scripts by the same names (build.py), so a copy imported from elsewhere is
# set aside and this pipeline's own are used.
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
for name in ("build", "parse", "fetch"):
    module = sys.modules.get(name)
    if module is not None and Path(getattr(module, "__file__", "") or "").parent != HERE:
        del sys.modules[name]
