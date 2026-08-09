"""Injectable HTTP transport with one cookie jar per account client."""

from __future__ import annotations

import threading
from typing import Any, Mapping, Protocol

import requests

from welearn_studio import __version__

DEFAULT_USER_AGENT = f"WeLearnStudio/{__version__}"


class HttpResponse(Protocol):
    status_code: int
    url: str
    text: str

    def json(self) -> Any: ...


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        data: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        allow_redirects: bool = False,
        timeout: float | tuple[float, float] | None = None,
    ) -> HttpResponse: ...

    def close(self) -> None: ...


class RequestsSessionTransport:
    """A requests transport whose session belongs to exactly one account."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session if session is not None else requests.Session()
        self._lock = threading.RLock()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, object] | None = None,
        data: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        allow_redirects: bool = False,
        timeout: float | tuple[float, float] | None = None,
    ) -> requests.Response:
        # requests.Session mutates cookies and adapter state. Keep each network
        # exchange atomic while timed waits remain concurrent outside the lock.
        request_headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers is not None:
            request_headers.update(headers)
        with self._lock:
            return self._session.request(
                method,
                url,
                params=params,
                data=data,
                headers=request_headers,
                allow_redirects=allow_redirects,
                timeout=timeout,
            )

    def close(self) -> None:
        with self._lock:
            self._session.close()
