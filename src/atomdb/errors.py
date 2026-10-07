"""Error types with stable machine-readable codes (used in CLI JSON and API responses)."""


class AtomDBError(Exception):
    code = "error"
    exit_code = 1
    http_status = 400

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}


class NotInitialized(AtomDBError):
    code = "not_initialized"
    exit_code = 3


class NotFound(AtomDBError):
    code = "not_found"
    exit_code = 4
    http_status = 404


class InvalidInput(AtomDBError):
    code = "invalid_input"
    exit_code = 2
    http_status = 422


class FormatError(AtomDBError):
    code = "format_error"
    exit_code = 5
    http_status = 422


class DataLossError(AtomDBError):
    code = "data_loss"
    exit_code = 6
    http_status = 409


class IntegrityProblem(AtomDBError):
    code = "integrity"
    exit_code = 7
    http_status = 409
