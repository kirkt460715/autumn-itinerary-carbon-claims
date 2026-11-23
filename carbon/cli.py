"""命令行入口：python -m carbon.cli --data data --as-of 2026-11-23 --out out/2026-11-23"""

from __future__ import annotations

import argparse
from datetime import date

from .model import load_dataset
from .report import write_csvs, write_markdown
from .workflow import run


def main() -> None:
    parser = argparse.ArgumentParser(description="研学低碳标签复核工作流")
    parser.add_argument("--data", default="data", help="CSV 源表目录")
    parser.add_argument("--as-of", required=True, help="复核时点 YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="输出目录")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of)
    dataset = load_dataset(args.data)
    result = run(dataset, as_of)

    from pathlib import Path

    out_dir = Path(args.out)
    csvs = write_csvs(result, out_dir)
    report = write_markdown(result, out_dir)
    print(f"as_of={as_of.isoformat()} 复核完成，写出 {len(csvs)} 份 CSV 与 1 份报告：")
    for path in csvs:
        print(f"  - {path}")
    print(f"  - {report}")


if __name__ == "__main__":
    main()
