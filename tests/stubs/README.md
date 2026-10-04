Minimal import stubs used ONLY by tests/test_integration_pg.py in an
environment where python-telegram-bot, aiohttp, httpx and asyncpg cannot be
installed. They are never used in production (they are not on sys.path).
