"""Explicit cache-only initialization, status and bounded maintenance."""
import argparse
import json
import os

from .evaluation_cache import EvaluationCache
from .evaluation_models import CacheLimits


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init','status','maintain'])
    parser.add_argument('--cache', required=True, help='Independent cache path; never a GameLibrary database')
    parser.add_argument('--max-rows', type=int, default=100)
    parser.add_argument('--seconds', type=float, default=2)
    args = parser.parse_args(argv)
    cache = EvaluationCache(args.cache, CacheLimits.model_validate_json(os.environ.get('CHESS_COACH_EVAL_LIMITS', '{}')))
    if args.action == 'init': cache.initialize()
    result = cache.maintain(args.max_rows, args.seconds) if args.action == 'maintain' else cache.status()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
