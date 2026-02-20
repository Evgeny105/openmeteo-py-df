"""Exceptions for the OpenMeteo API client.

This module defines custom exceptions for error handling in the OpenMeteo
client. All exceptions inherit from OpenMeteoError for easy catching.

Example:
    Catching all OpenMeteo errors::

        from openmeteo import OpenMeteoClient, OpenMeteoError

        try:
            async with OpenMeteoClient() as client:
                data = await client.get_historical(55.75, 37.62, start, end)
        except OpenMeteoError as e:
            print(f"OpenMeteo error: {e}")

    Catching specific errors::

        from openmeteo import (
            OpenMeteoAPIError,
            OpenMeteoConnectionError,
            OpenMeteoValidationError,
        )

        try:
            data = await client.get_historical(55.75, 37.62, future, future)
        except OpenMeteoValidationError as e:
            print(f"Validation failed: {e}")
"""


class OpenMeteoError(Exception):
    """Base exception for all OpenMeteo errors.

    All exceptions in this module inherit from this class, allowing
    callers to catch all OpenMeteo-related errors with a single except.

    Example:
        >>> try:
        ...     await client.get_historical(lat, lon, start, end)
        ... except OpenMeteoError as e:
        ...     print(f"OpenMeteo operation failed: {e}")
    """

    pass


class OpenMeteoAPIError(OpenMeteoError):
    """Exception raised when the OpenMeteo API returns an error response.

    This occurs when the API successfully processes the request but returns
    an error (e.g., invalid parameters, rate limiting, server error).

    Args:
        reason: The error reason/message returned by the API.

    Attributes:
        reason: Human-readable error message from the API.

    Example:
        >>> raise OpenMeteoAPIError("Cannot resolve historical data")
        OpenMeteoAPIError: API error: Cannot resolve historical data
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"API error: {reason}")


class OpenMeteoConnectionError(OpenMeteoError):
    """Exception raised when network connection to OpenMeteo API fails.

    This occurs when there are HTTP-level errors, timeouts, DNS failures,
    or network connectivity issues. Wraps underlying httpx exceptions.

    Example:
        >>> # May be raised during network issues
        >>> try:
        ...     await client.get_historical(55.75, 37.62, start, end)
        ... except OpenMeteoConnectionError as e:
        ...     print(f"Network error: {e}")
    """

    pass


class OpenMeteoValidationError(OpenMeteoError):
    """Exception raised when input validation fails.

    This occurs when provided parameters are invalid (e.g., coordinates
    out of range, end_date before start_date, future dates for historical).

    Example:
        >>> # Historical data cannot be in the future
        >>> try:
        ...     await client.get_historical(55.75, 37.62, future, future)
        ... except OpenMeteoValidationError as e:
        ...     print(f"Invalid input: {e}")
    """

    pass


class OpenMeteoCacheError(OpenMeteoError):
    """Exception raised when cache operations fail.

    This occurs when there are issues reading from or writing to
    the historical data cache files (e.g., permission errors,
    corrupted files, disk full).

    Example:
        >>> try:
        ...     await client.get_historical(55.75, 37.62, start, end)
        ... except OpenMeteoCacheError as e:
        ...     print(f"Cache error: {e}")
    """

    pass
