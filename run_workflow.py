#!/usr/bin/env python3
"""工作流薄封装入口，使 `python3 -I run_workflow.py ...` 在隔离模式下可运行。

真实逻辑全部在 carbon 包内；本文件只负责把仓库根加入 sys.path 并转交 CLI。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from carbon.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
