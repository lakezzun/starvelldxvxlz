class StarvellError(RuntimeError):
    pass


class StarvellAuthError(StarvellError):
    pass


class StarvellResponseError(StarvellError):
    pass
