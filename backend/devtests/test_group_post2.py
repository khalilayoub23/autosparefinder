import asyncio, sys, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
sys.path.insert(0, '/app')

async def test_group_post():
    from social.facebook_browser import GroupAgent
    agent = GroupAgent()
    result = await agent.publish_group_post(
        "https://www.facebook.com/groups/musahnikim/",
        "🔧 בדיקת פרסום אוטומטי — AutoSpareFinder #חלפים",
    )
    print(f"RESULT: {result}")

asyncio.run(test_group_post())
