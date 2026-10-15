"""Public, safe HTTP errors for the standalone workbench."""


class GatewayError(Exception):
    """A client-facing error with the existing API status and optional code."""

    def __init__(self, message, status=502, code=None):
        super().__init__(message)
        self.status = status
        self.code = code
