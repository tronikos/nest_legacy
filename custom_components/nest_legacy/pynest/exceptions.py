"""Exceptions used by PyNest."""


class PynestException(Exception):
    """Base class for all exceptions raised by pynest."""


class NestServiceException(PynestException):
    """Raised when service is not available."""


class BadCredentialsException(PynestException):
    """Raised when credentials are incorrect."""


class NotAuthenticatedException(PynestException):
    """Raised when session is invalid."""


class NonRetryablePynestException(PynestException):
    """Raised when an operation fails with a non-retryable error."""


class AuthenticationFailedException(
    NotAuthenticatedException, NonRetryablePynestException
):
    """Raised when a command is denied because authentication failed.

    Nest answers this both for a stale session, which signing in again fixes,
    and for a trait the account can never access. The message cannot tell the
    two apart, so it is not retried in place: the caller refreshes the session
    once and treats a failure that survives that as permanent.
    """


class GatewayTimeoutException(NestServiceException):
    """Raised when server times out."""


class BadGatewayException(NestServiceException):
    """Raised when server returns Bad Gateway."""


class EmptyResponseException(NestServiceException):
    """Raised when server returns Status 200 (OK), but empty response."""
