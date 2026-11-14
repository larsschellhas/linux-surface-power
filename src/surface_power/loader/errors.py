"""Error type of the loader. Every error that comes from blob content carries the line number (FR-B6)."""


class BlobError(Exception):
    """The blob cannot be used. `line` is the 1-based line number in the decoded text, if the error belongs to a line."""

    def __init__(self, message: str, line: int | None = None):
        self.message = message
        self.line = line
        super().__init__(f"line {line}: {message}" if line is not None else message)
