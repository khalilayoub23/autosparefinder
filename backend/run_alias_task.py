import asyncio
import json

from BACKEND_DATABASE_MODELS import async_session_factory
from db_update_agent import run_task


async def main() -> None:
    async with async_session_factory() as db:
        result = await run_task("auto_add_hebrew_brand_aliases", db)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
