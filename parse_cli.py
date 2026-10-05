#!/usr/bin/env python3
"""CLI для тестирования парсера на тексте из stdin или файла."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.parser import parse_app_message


def main() -> None:
    parser = argparse.ArgumentParser(description="Парсер сообщений о новых приложениях")
    parser.add_argument(
        "file",
        nargs="?",
        help="Путь к файлу с текстом сообщения (если не указан — читает из stdin)",
    )
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    result = parse_app_message(text)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
