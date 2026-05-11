from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from db_cleanup_agent import task6_reorganize_categories_rollup


async def main() -> int:
    parser = argparse.ArgumentParser(description='Backfill and reorganize parts into the new category hierarchy.')
    parser.add_argument('--batch-size', type=int, default=500, help='Rows to inspect per batch')
    parser.add_argument('--max-batches', type=int, default=0, help='Stop after this many batches; 0 means run until done')
    args = parser.parse_args()

    total_updated = 0
    batch_index = 0
    while True:
        batch_index += 1
        updated = await task6_reorganize_categories_rollup(batch_size=args.batch_size)
        total_updated += updated
        print(f'[Backfill] batch={batch_index} updated={updated} total_updated={total_updated}')
        if updated == 0:
            break
        if args.max_batches and batch_index >= args.max_batches:
            break
    print(f'[Backfill] complete total_updated={total_updated} batches={batch_index}')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
