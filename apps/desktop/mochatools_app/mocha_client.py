"""Thin, Qt-free HTTP client for the Mocha API.

Every Mocha API request in the app goes through :class:`MochaClient`.
Workers and tabs call these methods instead of building ``requests``
calls themselves, so base URL, auth headers, timeouts, and error
handling have a single home.
"""

from __future__ import annotations

import itertools
import time
from urllib.parse import quote

import requests

from .constants import HARDCODED_BASE_URL, PART_UPLOAD_TIMEOUT, SHARE_BASE_URL


class MochaAPIError(Exception):
    """Raised when a Mocha API call fails.

    Attributes:
        status_code: HTTP status when the server responded, else None.
        response_text: Raw response body when the server responded, else None.
        kind: "http" (non-2xx response), "connection" (network/timeout),
              or "protocol" (unexpected response shape).
    """

    def __init__(self, message, status_code=None, response_text=None, kind="http"):
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
        self, get_api_key, base_url=HARDCODED_BASE_URL, timeout=_TIMEOUT, logger=None
    ):
        self._get_api_key = get_api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._logger = logger
        self._session = requests.Session()
        self._req_ids = itertools.count(1)

    @property
    def base_url(self):
        return self._base_url

    @property
    def has_api_key(self) -> bool:
        """True when the current API key is non-empty."""
        try:
            return bool(self._get_api_key())
        except Exception:
            return False

    def _log(self, msg, logger=None):
        log = logger or self._logger
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    def _headers(self, file_name=None):
        headers = {"Authorization": f"Bearer {self._get_api_key()}"}
        if file_name:
            # RFC 5987 encode so apostrophes/accents/etc don't corrupt the header
            headers["x-file-name"] = quote(file_name, safe="")
        return headers

    def _request(
        self,
        method,
        path,
        *,
        params=None,
        json=None,
        data=None,
        headers=None,
        timeout=None,
        auth=True,
        logger=None,
    ):
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
                f"[DEBUG] #{req_id} Exception after {elapsed_ms:.0f}ms: {e}", logger
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
                f"[DEBUG] #{req_id} Body: {body[:400]!r}"
                f"{'...' if len(body) > 400 else ''}",
                logger,
            )
        if resp.status_code >= 400:
            raise MochaAPIError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                response_text=resp.text,
            )
        return resp

    def _json(self, method, path, **kwargs):
        resp = self._request(method, path, **kwargs)
        try:
            return resp.json()
        except ValueError as e:
            raise MochaAPIError(
                f"Invalid JSON response from {path}: {e}",
                kind="protocol",
            ) from e

    # ── Files ──────────────────────────────────────────────────────────────

    def list_files(self, path="/", timeout=None):
        return self._json(
            "GET",
            "/api/files",
            params={"path": path, "includeSubfolders": "0"},
            timeout=timeout,
        )

    def delete_file(self, file_name, timeout=None):
        # Strip leading slash — API path is /api/files/{fileName}
        encoded = quote(file_name.lstrip("/"), safe="")
        self._request("DELETE", f"/api/files/{encoded}", timeout=timeout)

    def create_folder(self, parent, name, timeout=None, logger=None):
        return self._json(
            "POST",
            "/api/files/folders",
            json={"path": parent, "name": name},
            timeout=timeout,
            logger=logger,
        )

    def delete_folder(self, parent, name, timeout=None, logger=None):
        self._request(
            "DELETE",
            "/api/files/folders",
            json={"path": parent, "name": name},
            timeout=timeout,
            logger=logger,
        )

    def rename_folder(self, path, old_name, new_name, timeout=None):
        self._request(
            "PATCH",
            "/api/files/folders",
            json={"path": path, "oldName": old_name, "newName": new_name},
            timeout=timeout,
        )

    def move(
        self,
        *,
        file_id=None,
        folder_path=None,
        source_path=None,
        to_path,
        timeout=30,
        logger=None,
    ):
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

    def presigned_url(self, file_id, timeout=15):
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
            raise MochaAPIError(
                f"No presigned URL in response: {data}", kind="protocol"
            )
        return url

    # ── Shares ─────────────────────────────────────────────────────────────

    def create_share(
        self, file_id, expires_in_hours=None, max_downloads=0, timeout=30, logger=None
    ):
        payload = {"fileId": file_id}
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

    def list_shares(self, timeout=None):
        return self._json("GET", "/api/shares", timeout=timeout)

    def get_share(self, token, timeout=None):
        # Share metadata is fetched without auth today; keep that behavior.
        resp = self._request("GET", f"/api/shares/{token}", auth=False, timeout=timeout)
        try:
            return resp.json()
        except ValueError as e:
            raise MochaAPIError(
                f"Invalid JSON response from /api/shares/{token}: {e}",
                kind="protocol",
            ) from e

    def set_share_active(self, token, is_active, timeout=15):
        self._request(
            "PATCH",
            f"/api/shares/{token}",
            json={"isActive": is_active},
            timeout=timeout,
        )

    def delete_share(self, token, timeout=15):
        self._request("DELETE", f"/api/shares/{token}", timeout=timeout)

    @staticmethod
    def share_url(token):
        return f"{SHARE_BASE_URL}/share/{token}" if token else ""

    # ── Multipart ──────────────────────────────────────────────────────────

    def multipart_init(self, file_name, path, size, mime_type, timeout=30, logger=None):
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

    def multipart_complete(self, payload, timeout=180, logger=None):
        return self._json(
            "POST",
            "/api/files/multipart/complete",
            json=payload,
            timeout=timeout,
            logger=logger,
        )

    def multipart_part_relay(
        self, session, part_num, body, timeout=PART_UPLOAD_TIMEOUT, logger=None
    ):
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
            raise MochaAPIError(
                f"No ETag returned for part {part_num}: {data}", kind="protocol"
            )
        return etag

    def multipart_presign(self, session, part_num, timeout=30, logger=None):
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
            raise MochaAPIError(
                f"No presigned URL in response: {data}", kind="protocol"
            )
        return signed_url

    def multipart_part_s3(
        self, signed_url, body, timeout=PART_UPLOAD_TIMEOUT, logger=None, part_num=None
    ):
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
        if resp.status_code >= 400:
            raise MochaAPIError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                status_code=resp.status_code,
                response_text=resp.text,
            )
        etag = resp.headers.get("ETag", "")
        if not etag:
            raise MochaAPIError("No ETag returned for S3 part", kind="protocol")
        return etag

    def multipart_abort(self, session, part_numbers=None, logger=None):
        payload = dict(session)
        if part_numbers:
            payload["partNumbers"] = part_numbers
        try:
            self._request(
                "POST",
                "/api/files/multipart/abort",
                json=payload,
                logger=logger,
            )
        except MochaAPIError:
            pass

    # ── Remote ingest ──────────────────────────────────────────────────────

    def remote_ingest(self, source_url, file_name, path, timeout=30):
        return self._json(
            "POST",
            "/api/files/remote-download",
            json={"sourceUrl": source_url, "fileName": file_name, "path": path},
            timeout=timeout,
        )

    def list_transfer_jobs(self, active_only=True, timeout=None):
        params = {"active": "true"} if active_only else {}
        return self._json(
            "GET", "/api/admin/transfer-jobs", params=params, timeout=timeout
        )

    def cancel_transfer_job(self, job_id, timeout=None):
        return self._json(
            "DELETE",
            "/api/admin/transfer-jobs",
            params={"id": job_id},
            timeout=timeout,
        )

    # ── Storage ────────────────────────────────────────────────────────────

    def storage_available(self, timeout=None):
        return self._json("GET", "/api/storage/available", timeout=timeout)
