"""Clear the quota one account has already spent, so it can be used again.

Usage:
    uv run python -m scripts.reset_usage <username> [username ...]

Only the sliding window table is touched. The permanent statistics log keeps
the accesses that were already delivered, so the numbers published on /stats
are not rewritten by an administrative reset. Database settings come from the
same ``.env`` the application reads, so run this from the repository root.

The exit code is 0 when every named account was reset, 1 when at least one
name was unknown, and 2 when no name was given at all.
"""

import asyncio
import logging
import sys

from app.accounts import build_account_store
from app.config import Settings

logger = logging.getLogger("reset_usage")


async def _reset(usernames: list[str]) -> int:
    """Reset every named account and return how many names were unknown."""
    store = build_account_store(Settings())
    await store.initialize()
    unknown = 0
    try:
        for username in usernames:
            outcome = await store.reset_usage(username)
            if outcome is None:
                logger.error("没有这个账号 %s", username)
                unknown += 1
                continue
            logger.info("已重置账号 %s,删除 %d 条访问记录", outcome.username, outcome.removed)
    finally:
        await store.close()
    return unknown


def main(argv: list[str]) -> int:
    """Reset the quota of each username named on the command line."""
    usernames = argv[1:]
    if not usernames:
        logger.error("用法:uv run python -m scripts.reset_usage <用户名> [用户名 ...]")
        return 2
    unknown = asyncio.run(_reset(usernames))
    return 1 if unknown else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main(sys.argv))
