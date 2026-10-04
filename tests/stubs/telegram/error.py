class TelegramError(Exception):
    pass


class NetworkError(TelegramError):
    pass


class BadRequest(NetworkError):
    pass


class TimedOut(NetworkError):
    pass


class Forbidden(TelegramError):
    pass


class RetryAfter(TelegramError):
    def __init__(self, retry_after=1):
        super().__init__("retry after")
        self.retry_after = retry_after
