"""Explicit independent practice-store initialization and status."""
import argparse
import json
from .practice_store import PracticeStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'status'])
    parser.add_argument('--db', required=True)
    parser.add_argument('--library', required=True)
    args = parser.parse_args()
    store = PracticeStore(args.db, args.library)
    if args.command == 'init':
        store.initialize()
    print(json.dumps(store.status(), indent=2))


if __name__ == '__main__':
    main()
