class ContextTypes:
    DEFAULT_TYPE = object


class Application:
    def __init__(self):
        self.post_init = None
        self.post_shutdown = None
        self.handlers = []
        self.bot_data = {}
        self.bot = None

    def add_handler(self, handler):
        self.handlers.append(handler)


class CallbackQueryHandler:
    def __init__(self, callback, pattern=None):
        self.callback = callback
        self.pattern = pattern


class CommandHandler:
    def __init__(self, command, callback):
        self.command = command
        self.callback = callback
