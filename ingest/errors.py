"""Shared error types for the ingestion path."""


class MissingCredentialError(RuntimeError):
    """Raised when a required environment variable is absent.

    Never degrade silently -- always name the exact variable and where to set it.
    """

    def __init__(self, var_name: str):
        super().__init__(f"MissingCredentialError: set {var_name} in .env -- see README section Setup")
        self.var_name = var_name
