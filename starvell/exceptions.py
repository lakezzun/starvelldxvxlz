class StarvellError(RuntimeError):
    pass


class StarvellAuthError(StarvellError):
    pass


class StarvellResponseError(StarvellError):
    pass


class StarvellRateLimitError(StarvellResponseError):
    def __init__(self, message: str = "HTTP 429", wait: int = 15) -> None:
        super().__init__(message)
        self.wait = max(5, int(wait))
