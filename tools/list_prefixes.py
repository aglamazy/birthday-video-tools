#!/usr/bin/env python3
"""List ordered sequence prefixes based on grouped media files."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import incremental_builder as ib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path("sequence"),
        help="Sequence directory to scan (default: sequence/).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    directory = args.directory.resolve()
    prefixes = list(ib.iter_media_prefixes(directory))
    if prefixes:
        print(" ".join(prefixes))


if __name__ == "__main__":
    main()
