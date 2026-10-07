class ProcessingError(Exception):
    """A client-correctable input failure, safe to expose in the API."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code
