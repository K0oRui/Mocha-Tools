"""
Shared upload/file operation logic (platform-agnostic, no PyQt6 dependencies).

All threading uses standard threading.Thread instead of QThread. Callbacks
use plain callables instead of pyqtSignal.
"""

import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Dict, List, Tuple, Any

import requests


# ── Constants (copied from desktop) ──────────────────────────────────────────
CHUNK_SIZE = 50 * 1024 * 1024
PART_UPLOAD_RETRIES = 10
PART_UPLOAD_TIMEOUT = 7200
S3_DEFAULT_CONCURRENCY = 24
S3_MAX_CONCURRENCY = 24
RELAY_DEFAULT_CONCURRENCY = 1
RELAY_MAX_CONCURRENCY = 1
DEFAULT_CHUNK_SIZE_MB = 50
DEFAULT_MAX_CHUNKS = 20


# ── Progress Tracker ─────────────────────────────────────────────────────────
class ProgressTracker:
	"""Thread-safe byte counter for multipart uploads."""
	EMIT_INTERVAL = 0.25  # seconds

	def __init__(self, total_bytes: int, on_progress: Callable[[int], None], on_speed: Callable[[float], None]):
		self._total = total_bytes
		self._sent = 0
		self._lock = threading.Lock()
		self._start = time.monotonic()
		self._last_emit = 0.0
		self._on_prog = on_progress
		self._on_speed = on_speed

	def feed(self, n_bytes: int):
		"""Called by upload threads as bytes leave the socket."""
		with self._lock:
			self._sent += n_bytes
			now = time.monotonic()
			elapsed = max(now - self._start, 0.001)
			if now - self._last_emit >= self.EMIT_INTERVAL:
				self._last_emit = now
				pct = min(int(self._sent / self._total * 100), 99)
				bps = self._sent / elapsed
				self._on_prog(pct)
				self._on_speed(bps)

	def finish(self):
		"""Call once when all parts are done to snap to 100%."""
		with self._lock:
			elapsed = max(time.monotonic() - self._start, 0.001)
			bps = self._sent / elapsed
		self._on_prog(100)
		self._on_speed(bps)

	def make_streaming_body(self, chunk: bytes, read_size: int = 65536):
		"""Wrap a chunk bytes object to track bytes as they're read."""
		class ChunkStream:
			def __init__(self, chunk_bytes: bytes, tracker, block_size: int):
				self.chunk = chunk_bytes
				self.tracker = tracker
				self.block_size = block_size
				self.offset = 0
				self.length = len(chunk_bytes)
				self.len = self.length

			def read(self, size=-1):
				if self.offset >= self.length:
					return b""
				if size is None or size < 0:
					size = self.block_size
				end = min(self.offset + size, self.length)
				piece = self.chunk[self.offset:end]
				if piece:
					self.tracker.feed(len(piece))
					self.offset = end
				return piece

			def __len__(self):
				return self.length

		return ChunkStream(chunk, self, read_size)


# ── Upload Worker (core logic, no QThread) ──────────────────────────────────
class UploadWorkerCore:
	"""Platform-agnostic upload worker. Subclass or call methods directly."""

	def __init__(
		self,
		api_key: str,
		base_url: str,
		file_pairs: List[Tuple[str, str]],
		create_share: bool = False,
		share_expiry: Optional[int] = None,
		share_max_downloads: int = 0,
		chunk_size_mb: Optional[int] = None,
		max_chunks: Optional[int] = None,
		on_status: Callable[[str], None] = None,
		on_progress: Callable[[int], None] = None,
		on_speed: Callable[[float], None] = None,
		on_done: Callable[[Dict], None] = None,
		on_error: Callable[[str], None] = None,
	):
		self.api_key = api_key
		self.base_url = base_url.rstrip("/")
		self.file_pairs = file_pairs
		self.create_share = create_share
		self.share_expiry_hours = share_expiry
		self.share_max_downloads = share_max_downloads
		mb = int(chunk_size_mb) if chunk_size_mb is not None else DEFAULT_CHUNK_SIZE_MB
		self._chunk_size = max(1, min(mb, 100)) * 1024 * 1024
		mc = int(max_chunks) if max_chunks is not None else DEFAULT_MAX_CHUNKS
		self._max_chunks = max(1, min(mc, 20))
		self._cancel = False
		self._on_status = on_status or (lambda msg: None)
		self._on_progress = on_progress or (lambda pct: None)
		self._on_speed = on_speed or (lambda bps: None)
		self._on_done = on_done or (lambda result: None)
		self._on_error = on_error or (lambda msg: None)

	def cancel(self):
		self._cancel = True

	def run(self):
		"""Execute the upload. Call this directly or from a thread."""
		total_files = len(self.file_pairs)
		last_file_id = None
		last_share_url = None

		# Create destination directories
		dest_dirs = sorted(
			{"/".join(dest.rstrip("/").split("/")[:-1]) or "/" for _, dest in self.file_pairs}
		)
		for d in dest_dirs:
			if d == "/":
				continue
			self._on_status(f"[DEBUG] Creating folder: {d}")
			try:
				self._ensure_folder(d)
			except Exception as e:
				self._on_error(f"Failed to create folder {d!r}: {e}")
				return

		# Upload each file
		for idx, (local_path, dest_path) in enumerate(self.file_pairs, 1):
			if self._cancel:
				return

			file_name = os.path.basename(local_path)
			prefix = f"[{idx}/{total_files}] " if total_files > 1 else ""

			try:
				file_size = os.path.getsize(local_path)

				if file_size == 0:
					self._on_status(f"{prefix}{file_name}  ⊘ Skipped (empty file)")
					self._on_status(f"[DEBUG] Skipped empty file: {local_path}")
					continue

				self._on_status(f"{prefix}{file_name}  ({self._fmt_size(file_size)})")
				self._on_status(f"[DEBUG] Local path: {local_path}")
				self._on_status(f"[DEBUG] Remote dest: {dest_path}")
				self._on_status(f"[DEBUG] File size (bytes): {file_size}")

				self._on_status("[DEBUG] Strategy: multipart upload")
				file_id = self._multipart_upload(file_size, local_path, dest_path)

				if self._cancel or file_id is None:
					return

				self._on_status(f"[DEBUG] File ID returned: {file_id}")
				last_file_id = file_id

				if self.create_share and idx == total_files:
					self._on_status("Creating share link…")
					last_share_url = self._create_share(file_id)
					self._on_status(f"Share: {last_share_url}")

			except Exception as e:
				self._on_error(f"{prefix}{file_name}: {e}")
				return

		self._on_done({"file_id": last_file_id, "share_url": last_share_url})

	def _headers(self, file_name: Optional[str] = None) -> Dict[str, str]:
		headers = {"Authorization": f"Bearer {self.api_key}"}
		if file_name:
			headers["x-file-name"] = file_name
		return headers

	def _multipart_upload(self, file_size: int, local_path: str, dest_path: str) -> Optional[str]:
		"""Execute multipart upload for a single file."""
		import mimetypes

		file_name = os.path.basename(local_path)
		dest_dir = "/".join(dest_path.rstrip("/").split("/")[:-1]) or "/"
		dest_dir = dest_dir.rstrip("/") + "/"
		mime_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

		# Step 1: Init multipart
		url = f"{self.base_url}/api/files/multipart/init"
		payload = {
			"originalName": file_name,
			"path": dest_dir,
			"size": file_size,
			"mimeType": mime_type,
		}
		self._on_status(f"[DEBUG] Multipart init URL: {url}")
		self._on_status(f"[DEBUG] Payload: {payload}")

		try:
			init_resp = requests.post(
				url,
				headers={**self._headers(), "Content-Type": "application/json"},
				json=payload,
				timeout=30,
			)
			init_resp.raise_for_status()
		except requests.HTTPError as e:
			self._on_status(f"[DEBUG] HTTPError: {e}")
			self._on_status(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
			self._on_status(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
			raise
		except Exception as e:
			self._on_status(f"[DEBUG] Exception: {e}")
			raise

		init_data = init_resp.json()
		self._on_status(f"[DEBUG] Init response: {init_data}")

		strategy = init_data.get("strategy")
		upload_id = init_data.get("uploadId")
		key = init_data.get("key")
		node_id = init_data.get("nodeId")
		direct = init_data.get("directUploadEnabled") is not False

		if strategy not in ("s3", "webdav") or not upload_id or not key or not node_id:
			raise RuntimeError(f"Invalid multipart init response: {init_data}")

		session = {
			"strategy": strategy,
			"uploadId": upload_id,
			"key": key,
			"nodeId": node_id,
			"originalName": init_data.get("originalName") or file_name,
			"path": dest_dir,
			"size": file_size,
			"mimeType": mime_type,
		}

		chunk_size = self._chunk_size
		total_parts = math.ceil(file_size / chunk_size)
		mode = "direct S3" if strategy == "s3" and direct else "server relay"
		concurrency = self._multipart_concurrency(init_data, total_parts, mode, self._max_chunks)

		self._on_status(
			f"[DEBUG] Multipart upload: {total_parts} parts… "
			f"(strategy={strategy}, mode={mode}, partSize={self._fmt_size(chunk_size)}, concurrency={concurrency})"
		)
		self._on_status(f"[DEBUG] Session: {upload_id}")

		# Shared progress tracker
		tracker = ProgressTracker(
			file_size,
			on_progress=self._on_progress,
			on_speed=self._on_speed,
		)

		parts = []
		active_parts: set = set()
		active_lock = threading.Lock()

		def upload_part(part_num: int) -> Optional[Dict]:
			with active_lock:
				active_parts.add(part_num)
			offset = (part_num - 1) * chunk_size
			read_size = min(chunk_size, file_size - offset)
			if self._cancel:
				return None
			with open(local_path, "rb") as part_file:
				part_file.seek(offset)
				chunk = part_file.read(read_size)
			if self._cancel:
				return None
			self._on_status(f"[DEBUG] Chunk size for part {part_num}: {len(chunk)} bytes")

			if strategy == "s3" and direct:
				etag = self._upload_part_s3(session, part_num, chunk, tracker)
			else:
				etag = self._upload_part_relay(session, part_num, chunk, tracker)

			return {"partNumber": part_num, "etag": etag, "size": len(chunk)}

		with ThreadPoolExecutor(max_workers=concurrency) as executor:
			futures = {executor.submit(upload_part, part_num): part_num for part_num in range(1, total_parts + 1)}
			time.sleep(0.05)
			with active_lock:
				current = sorted(active_parts)
			if current:
				parts_str = " & ".join(f"part {p}" for p in current)
				self._on_status(f"[DEBUG] Uploading {parts_str} out of {total_parts} total…")

			for future in as_completed(futures):
				if self._cancel:
					self._stop_multipart_futures(futures, session, total_parts)
					return None

				try:
					result = future.result()
				except Exception:
					self._cancel = True
					self._stop_multipart_futures(futures, session, total_parts)
					raise

				if result is None or result["etag"] is None:
					self._stop_multipart_futures(futures, session, total_parts)
					return None

				parts.append({"partNumber": result["partNumber"], "etag": result["etag"]})
				done = len(parts)

				with active_lock:
					active_parts.discard(result["partNumber"])

		# Step 3: Complete
		complete_payload = {**session, "parts": sorted(parts, key=lambda p: p["partNumber"])}
		j = self._complete_multipart_upload(complete_payload)
		file_id = j.get("fileId") or j.get("id") or (j.get("file") or {}).get("id")
		self._on_status(f"[DEBUG] Multipart complete. File ID: {file_id}")
		tracker.finish()
		return file_id

	def _complete_multipart_upload(self, payload: Dict) -> Dict:
		"""Retry-able multipart complete."""
		url = f"{self.base_url}/api/files/multipart/complete"
		last_error = None
		for attempt in range(1, 9):
			if self._cancel:
				return {}
			try:
				self._on_status(f"[DEBUG] Completing multipart upload… attempt {attempt}/8")
				resp = requests.post(
					url,
					headers={**self._headers(), "Content-Type": "application/json"},
					json=payload,
					timeout=180,
				)
				self._on_status(f"[DEBUG] Complete response status: {resp.status_code}")
				self._on_status(f"[DEBUG] Complete response body: {resp.text[:500]}")
				resp.raise_for_status()
				return resp.json()
			except requests.HTTPError as e:
				last_error = e
				status = getattr(e.response, "status_code", None)
				body = getattr(e.response, "text", "") or ""
				if status not in (409, 423, 429, 500, 502, 503, 504, 524) and "524" not in body:
					raise
				self._on_status(f"[DEBUG] Multipart complete still pending/retryable ({status}): {body[:200]}")
			except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
				last_error = e
				self._on_status(f"[DEBUG] Multipart complete connection issue: {e}")

			wait_seconds = min(10 * attempt, 60)
			self._on_status(f"[DEBUG] Waiting {wait_seconds}s before checking complete again…")
			time.sleep(wait_seconds)

		raise last_error

	@staticmethod
	def _multipart_concurrency(init_data: Dict, total_parts: int, mode: str, user_max_chunks: Optional[int] = None) -> int:
		"""Determine concurrency level for uploads."""
		default = S3_DEFAULT_CONCURRENCY if mode == "direct S3" else RELAY_DEFAULT_CONCURRENCY
		maximum = S3_MAX_CONCURRENCY if mode == "direct S3" else RELAY_MAX_CONCURRENCY
		if user_max_chunks is not None:
			maximum = user_max_chunks
		value = init_data.get("partUploadConcurrency", default)
		try:
			parsed = int(value)
		except (TypeError, ValueError):
			parsed = default
		return max(1, min(parsed, total_parts, maximum))

	@staticmethod
	def _cancel_futures(futures):
		for future in futures:
			future.cancel()

	def _abort_all_parts(self, session: Dict, total_parts: int):
		self._abort(session, list(range(1, total_parts + 1)))

	def _stop_multipart_futures(self, futures, session: Dict, total_parts: int):
		self._cancel_futures(futures)
		self._abort_all_parts(session, total_parts)

	def _wait_before_part_retry(self, label: str, part_num: int, attempt: int, error: Exception):
		if attempt >= PART_UPLOAD_RETRIES or not self._is_retryable_upload_error(error):
			raise error

		delay = min(2 ** (attempt - 1), 10)
		self._on_status(f"[DEBUG] Retrying {label} part {part_num} after transient failure in {delay}s…")
		time.sleep(delay)

	def _upload_part_relay(self, session: Dict, part_num: int, chunk: bytes, tracker: ProgressTracker) -> Optional[str]:
		"""Upload part via Mocha relay."""
		part_url = f"{self.base_url}/api/files/multipart/part"
		part_params = {
			"strategy": session["strategy"],
			"uploadId": session["uploadId"],
			"key": session["key"],
			"nodeId": session["nodeId"],
			"originalName": session["originalName"],
			"path": session["path"],
			"partNumber": part_num,
		}
		self._on_status(f"[DEBUG] Part upload URL: {part_url}")
		self._on_status(f"[DEBUG] Params: {part_params}")

		last_error = None
		with requests.Session() as http:
			for attempt in range(1, PART_UPLOAD_RETRIES + 1):
				if self._cancel:
					return None
				try:
					resp = http.put(
						part_url,
						headers=self._headers(),
						params=part_params,
						data=tracker.make_streaming_body(chunk),
						timeout=PART_UPLOAD_TIMEOUT,
					)
					resp.raise_for_status()
					data = resp.json()
					etag = data.get("etag") or resp.headers.get("ETag", "")
					if not etag:
						raise RuntimeError(f"No ETag returned for part {part_num}: {data}")
					return etag
				except requests.HTTPError as e:
					self._on_status(f"[DEBUG] HTTPError: {e}")
					self._on_status(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
					self._on_status(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
					last_error = e
				except Exception as e:
					self._on_status(f"[DEBUG] Exception: {e}")
					last_error = e

				self._wait_before_part_retry("relay", part_num, attempt, last_error)

		raise last_error

	def _presign_part_url(self, session: Dict, part_num: int, http=None) -> str:
		"""Get presigned URL for S3 upload."""
		presign_url = f"{self.base_url}/api/files/multipart/presigned"
		presign_payload = {**session, "partNumbers": [part_num]}
		self._on_status(f"[DEBUG] Presign URL: {presign_url}")
		self._on_status(f"[DEBUG] Presign payload: {presign_payload}")

		request_client = http or requests
		try:
			presign_resp = request_client.post(
				presign_url,
				headers={**self._headers(), "Content-Type": "application/json"},
				json=presign_payload,
				timeout=30,
			)
			presign_resp.raise_for_status()
		except requests.HTTPError as e:
			self._on_status(f"[DEBUG] HTTPError (presign): {e}")
			self._on_status(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
			self._on_status(f"[DEBUG] Response content: {getattr(e.response, 'text', None)}")
			raise
		except Exception as e:
			self._on_status(f"[DEBUG] Exception (presign): {e}")
			raise

		presign_data = presign_resp.json()
		signed_url = None
		if "url" in presign_data:
			signed_url = presign_data["url"]
		elif "presignedUrl" in presign_data:
			signed_url = presign_data["presignedUrl"]
		elif "urls" in presign_data and isinstance(presign_data["urls"], list):
			for entry in presign_data["urls"]:
				if entry.get("partNumber") == part_num and "url" in entry:
					signed_url = entry["url"]
					break
		if not signed_url:
			raise RuntimeError(f"No presigned URL in response: {presign_data}")
		return signed_url

	@staticmethod
	def _is_retryable_upload_error(error: Exception) -> bool:
		"""Check if error is transient and should be retried."""
		if isinstance(error, (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
			return True
		if not isinstance(error, requests.HTTPError):
			return False
		response = error.response
		status = getattr(response, "status_code", None)
		content = getattr(response, "text", "") if response is not None else ""
		retryable_codes = ("RequestTimeout", "SlowDown", "InternalError", "ServiceUnavailable")
		return status in (408, 429, 500, 502, 503, 504) or any(code in content for code in retryable_codes)

	def _upload_part_s3(self, session: Dict, part_num: int, chunk: bytes, tracker: ProgressTracker) -> Optional[str]:
		"""Upload part directly to S3 via presigned URL."""
		last_error = None
		with requests.Session() as http:
			for attempt in range(1, PART_UPLOAD_RETRIES + 1):
				if self._cancel:
					return None
				try:
					signed_url = self._presign_part_url(session, part_num, http)
					s3_resp = http.put(
						signed_url,
						data=tracker.make_streaming_body(chunk),
						timeout=PART_UPLOAD_TIMEOUT,
					)
					s3_resp.raise_for_status()
					etag = s3_resp.headers.get("ETag", "")
					if not etag:
						raise RuntimeError(f"No ETag returned for S3 part {part_num}")
					return etag
				except requests.HTTPError as e:
					content = getattr(e.response, "text", "")
					self._on_status(f"[DEBUG] HTTPError (S3 PUT): {e}")
					self._on_status(f"[DEBUG] Response status: {getattr(e.response, 'status_code', None)}")
					self._on_status(f"[DEBUG] Response content: {content}")
					if e.response is not None and "NoSuchUpload" in content:
						self._abort(session)
						self._on_error("S3 upload session expired or invalid (NoSuchUpload). Please retry the upload.")
						return None
					last_error = e
				except Exception as e:
					self._on_status(f"[DEBUG] Exception (S3 PUT): {e}")
					last_error = e

				self._wait_before_part_retry("S3", part_num, attempt, last_error)

		raise last_error

	def _abort(self, session: Dict, part_numbers: Optional[List[int]] = None):
		"""Abort a multipart upload session."""
		try:
			payload = dict(session)
			if part_numbers:
				payload["partNumbers"] = part_numbers
			requests.post(
				f"{self.base_url}/api/files/multipart/abort",
				headers={**self._headers(), "Content-Type": "application/json"},
				json=payload,
				timeout=15,
			)
		except Exception:
			pass
		self._on_status("[DEBUG] Upload aborted.")

	def _ensure_folder(self, path: str):
		"""Create a folder and all missing parents."""
		parts = path.strip("/").split("/")
		for depth in range(1, len(parts) + 1):
			name = parts[depth - 1]
			parent = ("/" + "/".join(parts[: depth - 1])).rstrip("/") or "/"
			try:
				resp = requests.post(
					f"{self.base_url}/api/files/folders",
					headers={**self._headers(), "Content-Type": "application/json"},
					json={"path": parent, "name": name},
					timeout=15,
				)
				if resp.status_code == 409:
					self._on_status(f"[DEBUG] Folder already exists: {parent}/{name}")
				else:
					resp.raise_for_status()
					self._on_status(f"[DEBUG] Created folder: {parent}/{name}")
			except requests.HTTPError as e:
				self._on_status(f"[DEBUG] Folder create error {parent}/{name}: {e}")
				raise

	def _create_share(self, file_id: str) -> str:
		"""Create a share link for a file."""
		payload = {"fileId": file_id}
		if self.share_expiry_hours is not None:
			payload["expiresInHours"] = self.share_expiry_hours
		if self.share_max_downloads > 0:
			payload["maxDownloads"] = self.share_max_downloads

		share_url_endpoint = f"{self.base_url}/api/shares"
		self._on_status(f"[DEBUG] Share URL: {share_url_endpoint}")
		self._on_status(f"[DEBUG] Share payload: {payload}")

		try:
			resp = requests.post(
				share_url_endpoint,
				headers={**self._headers(), "Content-Type": "application/json"},
				json=payload,
				timeout=30,
			)
			self._on_status(f"[DEBUG] Share response status: {resp.status_code}")
			self._on_status(f"[DEBUG] Share response body: {resp.text[:500]}")
			resp.raise_for_status()
		except requests.HTTPError as e:
			self._on_status(f"[DEBUG] Share HTTPError: {e}")
			self._on_status(f"[DEBUG] Share response status: {getattr(e.response, 'status_code', None)}")
			self._on_status(f"[DEBUG] Share response content: {getattr(e.response, 'text', None)}")
			raise
		except Exception as e:
			self._on_status(f"[DEBUG] Share exception: {e}")
			raise

		data = resp.json()
		token = data.get("token") or data.get("share", {}).get("token", "")
		self._on_status(f"[DEBUG] Share token: {token!r}  full JSON: {data}")
		return f"{self.base_url}/share/{token}" if token else "(no share URL returned)"

	@staticmethod
	def _fmt_size(b: int) -> str:
		"""Format bytes as human-readable string."""
		for unit in ("B", "KB", "MB", "GB", "TB"):
			if b < 1024:
				return f"{b:.1f} {unit}"
			b /= 1024
		return f"{b:.1f} PB"
