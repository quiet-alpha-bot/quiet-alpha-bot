class HTTPError(Exception):
    pass


class TimeoutException(HTTPError):
    pass


class Timeout:
    def __init__(self, *a, **k):
        pass


class AsyncClient:
    def __init__(self, *a, **k):
        pass

    async def aclose(self):
        pass
