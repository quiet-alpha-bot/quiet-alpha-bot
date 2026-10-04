class HTTPRequestEntityTooLarge(Exception):
    pass


class Request:
    pass


class Response:
    def __init__(self, body=None, status=200):
        self.body = body
        self.status = status


def json_response(data, status=200):
    return Response(data, status)


class Application:
    def __init__(self, client_max_size=None):
        self.router = _Router()


class _Router:
    def add_post(self, *a):
        pass

    def add_get(self, *a):
        pass


class AppRunner:
    def __init__(self, app, access_log=None):
        pass

    async def setup(self):
        pass

    async def cleanup(self):
        pass


class TCPSite:
    def __init__(self, runner, host=None, port=None):
        pass

    async def start(self):
        pass
