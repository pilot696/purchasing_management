"""数据工厂入口（从仓库根目录运行）。

用法：
    .venv\\Scripts\\python.exe scripts/seed.py --seed 42
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.seed.runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
