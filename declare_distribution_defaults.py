"""Declare or clear a distribution override. Does not build or touch AppData."""
import argparse
from pathlib import Path

from distribution_defaults import DECLARATION, FILES, declare, json_bytes
from runtime_storage import atomic_write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument('--groups', nargs='+', choices=sorted(FILES))
    choices.add_argument('--clear', action='store_true')
    parser.add_argument('--apply', action='store_true', help='선언 파일 저장. 생략하면 미리보기만 합니다.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    value = dict(schema=1, enabled=False) if args.clear else declare(root, args.groups)
    if args.apply:
        atomic_write(root / DECLARATION, json_bytes(value))
    print(json_bytes(dict(saved=args.apply, declaration=value)).decode('utf-8'))


if __name__ == '__main__':
    main()
