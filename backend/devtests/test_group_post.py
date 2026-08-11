import asyncio, sys, traceback, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
sys.path.insert(0, '/app')

async def test_group_post():
    try:
        from social.tools import facebook_group_publish
        result = await facebook_group_publish(
            group_target_id="3a9b463d-e8e9-4946-884d-34a0b3879ca5",
            content="🔧 בדיקת פרסום אוטומטי — AutoSpareFinder #חלפים",
        )
        print(f"RESULT: status={result.status} post_id={result.post_id} error={result.error}")
    except Exception as e:
        traceback.print_exc()

asyncio.run(test_group_post())
