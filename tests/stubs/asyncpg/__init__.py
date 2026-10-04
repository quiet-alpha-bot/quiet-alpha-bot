class Record(dict):
    pass


class Connection:
    pass


class Pool:
    pass


async def create_pool(*a, **k):  # pragma: no cover
    raise RuntimeError("stub")


async def connect(*a, **k):  # pragma: no cover
    raise RuntimeError("stub")
