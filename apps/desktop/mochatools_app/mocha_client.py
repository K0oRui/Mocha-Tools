"""Thin, Qt-free HTTP client for the Mocha API.

Every Mocha API request in the app goes through :class:`MochaClient`.
Workers and tabs call these methods instead of building ``requests``
calls themselves, so base URL, auth headers, timeouts, and error
handling have a single home.
"""

from __future__ import annotations

import contextlib
import itertools
import time
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

import requests

from .constants import HARDCODED_BASE_URL, PART_UPLOAD_TIMEOUT, SHARE_BASE_URL
from .logging_utils import write_debug_log

if TYPE_CHECKING:
    from collections.abc import Callable

    from .providers import ApiKeyProvider


_HTTP_ERROR_MIN = 400
_LOG_BODY_TRUNC = 400


class StreamingBody(Protocol):
    """File-like object accepted as request data (has ``read`` and ``fed``)."""

    def read(self, size: int = -1) -> bytes: ...

    fed: int


class MochaAPIError(Exception):
    """Raised when a Mocha API call fails.

    Attributes:
        status_code: HTTP status when the server responded, else None.
        response_text: Raw response body when the server responded, else None.
        kind: "http" (non-2xx response), "connection" (network/timeout),
              or "protocol" (unexpected response shape).

    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_text: str | None = None,
        kind: str = "http",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.kind = kind


class MochaClient:
    """Qt-free HTTP client for the Mocha API.

    The API key is read lazily via ``get_api_key`` on every request, so a
    single shared instance always uses the current key.  ``logger`` is an
    optional callable(msg) used for [DEBUG] request logging.
    """

    _TIMEOUT = (5, 60)  # (connect, read)

    def __init__(
        self,
        api_key_provider: ApiKeyProvider,
        base_url: str = HARDCODED_BASE_URL,
        timeout: float | tuple[float, float] = _TIMEOUT,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._api_key_provider = api_key_provider
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._logger = logger
        self._session = requests.Session()
        self._req_ids = itertools.count(1)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def has_api_key(self) -> bool:
        """True when the current API key is non-empty."""
        try:
            return bool(self._api_key_provider.get_api_key())
        except (AttributeError, TypeError, RuntimeError) as e:
            write_debug_log(f"[Silenced] has_api_key: {e}")
            return False

    def _log(self, msg: str, logger: Callable[[str], None] | None = None) -> None:
        log = logger or self._logger
        if log is not None:
            with contextlib.suppress(Exception):
                log(msg)

    def _headers(self, file_name: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key_provider.get_api_key()}"}
        if file_name:
            # RFC 5987 encode so apostrophes/accents/etc don't corrupt the header
            headers["x-file-name"] = quote(file_name, safe="")
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict | None = None,
        data: StreamingBody | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | tuple[float, float] | None = None,
        auth: bool = True,
        logger: Callable[[str], None] | None = None,
    ) -> requests.Response:
        url = f"{self._base_url}{path}"
        hdrs = self._headers() if auth else {}
        if headers:
            hdrs.update(headers)
        if json is not None:
            hdrs.setdefault("Content-Type", "application/json")
        timeout = timeout or self._timeout

        req_id = next(self._req_ids)
        started = time.perf_counter()
        self._log(f"[DEBUG] #{req_id} {method.upper()} {url}", logger)
        if params:
            self._log(f"[DEBUG] #{req_id} Params: {params}", logger)
        if json is not None:
            self._log(f"[DEBUG] #{req_id} Payload: {json}", logger)
        log_headers = {
            k: ("(hidden)" if k.lower() == "authorization" else v)
            for k, v in hdrs.items()
        }
        self._log(f"[DEBUG] #{req_id} Headers: {log_headers}", logger)

        try:
            resp = self._session.request(
                method,
                url,
                params=params,
                json=json,
                data=data,
                headers=hdrs,
                timeout=timeout,
            )
        except requests.RequestException as e:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._log(
                f"[DEBUG] #{req_id} Exception after {elapsed_ms:.0f}ms: {e}",
                logger,
            )
            raise MochaAPIError(str(e), kind="connection") from e

        elapsed_ms = (time.perf_counter() - started) * 1000
        body = resp.text or ""
        self._log(
            f"[DEBUG] #{req_id} -> {resp.status_code} in {elapsed_ms:.0f}ms "
            f"({len(resp.content)} bytes)",
            logger,
        )
        if body:
            self._log(
                f"[DEBUG] #{req_id} Body: {body[:_LOG_BODY_TRUNC]!r}"
                f"{'...' if len(body) > _LOG_BODY_TRUNC else ''}",
                logger,
            )
        if resp.status_code >= _HTTP_ERROR_MIN:
            msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            raise MochaAPIError(
                msg,
                status_code=resp.status_code,
                response_text=resp.text,
            )
        return resp

    def _json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict | None = None,
        timeout: float | tuple[float, float] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> dict:
        resp = self._request(
            method,
            path,
            params=params,
            json=json,
            timeout=timeout,
            logger=logger,
        )
        try:
            return resp.json()
        except ValueError as e:
            msg = f"Invalid JSON response from {path}: {e}"
            raise MochaAPIError(
                msg,
                kind="protocol",
            ) from e

    # ── Files ──────────────────────────────────────────────────────────────

    def list_files(
        self,
        path: str = "/",
        timeout: float | tuple[float, float] | None = None,
    ) -> dict:
        return self._json(
            "GET",
            "/api/files",
            params={"path": path, "includeSubfolders": "0"},
            timeout=timeout,
        )

    def delete_file(
        self,
        file_name: str,
        timeout: float | tuple[float, float] | None = None,
    ) -> None:
        # Strip leading slash — API path is /api/files/{fileName}
        encoded = quote(file_name.lstrip("/"), safe="")
        self._request("DELETE", f"/api/files/{encoded}", timeout=timeout)

    def create_folder(
        self,
        parent: str,
        name: str,
        timeout: float | tuple[float, float] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> dict:
        return self._json(
            "POST",
            "/api/files/folders",
            json={"path": parent, "name": name},
            timeout=timeout,
            logger=logger,
        )

    def delete_folder(
        self,
        parent: str,
        name: str,
        timeout: float | tuple[float, float] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._request(
            "DELETE",
            "/api/files/folders",
            json={"path": parent, "name": name},
            timeout=timeout,
            logger=logger,
        )

    def rename_folder(
        self,
        path: str,
        old_name: str,
        new_name: str,
        timeout: float | tuple[float, float] | None = None,
    ) -> None:
        self._request(
            "PATCH",
            "/api/files/folders",
            json={"path": path, "oldName": old_name, "newName": new_name},
            timeout=timeout,
        )

    def move(
        self,
        *,
        file_id: str | None = None,
        folder_path: str | None = None,
        source_path: str | None = None,
        to_path: str,
        timeout: float | tuple[float, float] = 30,
        logger: Callable[[str], None] | None = None,
    ) -> dict:
        if folder_path:
            payload = {"folderPath": folder_path, "toPath": to_path}
        elif file_id:
            payload = {"fileId": file_id, "toPath": to_path}
        else:
            payload = {"sourcePath": source_path, "toPath": to_path}
        return self._json(
            "POST",
            "/api/files/move",
            json=payload,
            timeout=timeout,
            logger=logger,
        )

    def presigned_url(
        self,
        file_id: str,
        timeout: float | tuple[float, float] | None = 15,
    ) -> str:
        data = self._json(
            "GET",
            "/api/files/presigned",
            params={"fileId": file_id},
            timeout=timeout,
        )
        url = (
            data.get("url") or data.get("presignedUrl") or data.get("downloadUrl") or ""
        )
        if not url:
            msg = f"No presigned URL in response: {data}"
            raise MochaAPIError(
                msg,
                kind="protocol",
            )
        return url

    # ── Shares ─────────────────────────────────────────────────────────────

    def create_share(
        self,
        file_id: str,
        expires_in_hours: int | None = None,
        max_downloads: int = 0,
        timeout: float | tuple[float, float] | None = 30,
        logger: Callable[[str], None] | None = None,
    ) -> dict:
        payload: dict[str, Any] = {"fileId": file_id}
        if expires_in_hours is not None:
            payload["expiresInHours"] = expires_in_hours
        if max_downloads and max_downloads > 0:
            payload["maxDownloads"] = max_downloads
        return self._json(
            "POST",
            "/api/shares",
            json=payload,
            timeout=timeout,
            logger=logger,
        )

    def list_shares(
        self,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict:
        return self._json("GET", "/api/shares", timeout=timeout)

    def get_share(
        self,
        token: str,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict:
        # Share metadata is fetched without auth today; keep that behavior.
        resp = self._request("GET", f"/api/shares/{token}", auth=False, timeout=timeout)
        try:
            return resp.json()
        except ValueError as e:
            msg = f"Invalid JSON response from /api/shares/{token}: {e}"
            raise MochaAPIError(
                msg,
                kind="protocol",
            ) from e

    def set_share_active(
        self,
        token: str,
        is_active: bool,
        timeout: float | tuple[float, float] | None = 15,
    ) -> None:
        self._request(
            "PATCH",
            f"/api/shares/{token}",
            json={"isActive": is_active},
            timeout=timeout,
        )

    def delete_share(
        self,
        token: str,
        timeout: float | tuple[float, float] | None = 15,
    ) -> None:
        self._request("DELETE", f"/api/shares/{token}", timeout=timeout)

    @staticmethod
    def share_url(token: str) -> str:
        return f"{SHARE_BASE_URL}/share/{token}" if token else ""

    # ── Multipart ──────────────────────────────────────────────────────────

    def multipart_init(
        self,
        file_name: str,
        path: str,
        size: int,
        mime_type: str,
        timeout: float | tuple[float, float] | None = 30,
        logger: Callable[[str], None] | None = None,
    ) -> dict:
        return self._json(
            "POST",
            "/api/files/multipart/init",
            json={
                "originalName": file_name,
                "path": path,
                "size": size,
                "mimeType": mime_type,
            },
            timeout=timeout,
            logger=logger,
        )

    def multipart_complete(
        self,
        payload: dict[str, Any],
        timeout: float | tuple[float, float] | None = 180,
        logger: Callable[[str], None] | None = None,
    ) -> dict:
        return self._json(
            "POST",
            "/api/files/multipart/complete",
            json=payload,
            timeout=timeout,
            logger=logger,
        )

    def multipart_part_relay(
        self,
        session: dict[str, Any],
        part_num: int,
        body: StreamingBody,
        timeout: float | tuple[float, float] | None = PART_UPLOAD_TIMEOUT,
        logger: Callable[[str], None] | None = None,
    ) -> str:
        params = {
            "strategy": session["strategy"],
            "uploadId": session["uploadId"],
            "key": session["key"],
            "nodeId": session["nodeId"],
            "originalName": session["originalName"],
            "path": session["path"],
            "partNumber": part_num,
        }
        resp = self._request(
            "PUT",
            "/api/files/multipart/part",
            params=params,
            data=body,
            timeout=timeout,
            logger=logger,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        etag = data.get("etag") or resp.headers.get("ETag", "")
        if not etag:
            msg = f"No ETag returned for part {part_num}: {data}"
            raise MochaAPIError(
                msg,
                kind="protocol",
            )
        return etag

    def multipart_presign(
        self,
        session: dict[str, Any],
        part_num: int,
        timeout: float | tuple[float, float] | None = 30,
        logger: Callable[[str], None] | None = None,
    ) -> str:
        data = self._json(
            "POST",
            "/api/files/multipart/presigned",
            json={**session, "partNumbers": [part_num]},
            timeout=timeout,
            logger=logger,
        )
        signed_url = data.get("url") or data.get("presignedUrl")
        if signed_url is None and isinstance(data.get("urls"), list):
            for entry in data["urls"]:
                if entry.get("partNumber") == part_num and "url" in entry:
                    signed_url = entry["url"]
                    break
        if not signed_url:
            msg = f"No presigned URL in response: {data}"
            raise MochaAPIError(
                msg,
                kind="protocol",
            )
        return signed_url

    def multipart_part_s3(
        self,
        signed_url: str,
        body: StreamingBody,
        timeout: float | tuple[float, float] | None = PART_UPLOAD_TIMEOUT,
        logger: Callable[[str], None] | None = None,
        part_num: int | None = None,
    ) -> str:
        short_url = signed_url.split("?", 1)[0] if signed_url else signed_url
        label = f"part {part_num} " if part_num else ""
        started = time.perf_counter()
        self._log(f"[DEBUG] PUT {label}{short_url}", logger)
        try:
            resp = self._session.put(signed_url, data=body, timeout=timeout)
        except requests.RequestException as e:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self._log(f"[DEBUG] Exception after {elapsed_ms:.0f}ms: {e}", logger)
            raise MochaAPIError(str(e), kind="connection") from e
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._log(
            f"[DEBUG] -> {resp.status_code} in {elapsed_ms:.0f}ms "
            f"({len(resp.content)} bytes)",
            logger,
        )
        if resp.status_code >= _HTTP_ERROR_MIN:
            msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
            raise MochaAPIError(
                msg,
                status_code=resp.status_code,
                response_text=resp.text,
            )
        etag = resp.headers.get("ETag", "")
        if not etag:
            msg = "No ETag returned for S3 part"
            raise MochaAPIError(msg, kind="protocol")
        return etag

    def multipart_abort(
        self,
        session: dict[str, Any],
        part_numbers: list[int] | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        payload = dict(session)
        if part_numbers:
            payload["partNumbers"] = part_numbers
        with contextlib.suppress(MochaAPIError):
            self._request(
                "POST",
                "/api/files/multipart/abort",
                json=payload,
                logger=logger,
            )

    # ── Remote ingest ──────────────────────────────────────────────────────

    def remote_ingest(
        self,
        source_url: str,
        file_name: str,
        path: str,
        timeout: float | tuple[float, float] | None = 30,
    ) -> dict:
        return self._json(
            "POST",
            "/api/files/remote-download",
            json={"sourceUrl": source_url, "fileName": file_name, "path": path},
            timeout=timeout,
        )

    def list_transfer_jobs(
        self,
        active_only: bool = True,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict:
        params = {"active": "true"} if active_only else {}
        return self._json(
            "GET",
            "/api/admin/transfer-jobs",
            params=params,
            timeout=timeout,
        )

    def cancel_transfer_job(
        self,
        job_id: str,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict:
        return self._json(
            "DELETE",
            "/api/admin/transfer-jobs",
            params={"id": job_id},
            timeout=timeout,
        )

    # ── Storage ────────────────────────────────────────────────────────────

    def storage_available(
        self,
        timeout: float | tuple[float, float] | None = None,
    ) -> dict:
        return self._json("GET", "/api/storage/available", timeout=timeout)
