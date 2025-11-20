"""Custom exceptions for gptme server."""


class ModelConfigurationError(Exception):
    """Raised when model configuration is missing or invalid.

    This exception indicates that the server cannot initialize because:
    - No model was specified
    - Required API keys are missing
    - Provider cannot be auto-detected

    The server can fall back to a default model when this exception is caught.
    """

    pass
