"""
Shared API helper functions (platform-agnostic).
No threading callbacks — just plain request wrappers.
"""

from typing import Optional, Dict, Any, Callable, List
import requests


def api_get(
	api_key: str,
	base_url: str,
	path: str,
	params: Optional[Dict] = None,
	timeout: int = 15,
) -> Dict[str, Any]:
	"""GET request to Mocha API."""
	resp = requests.get(
		f"{base_url.rstrip('/')}{path}",
		headers={"Authorization": f"Bearer {api_key}"},
		params=params or {},
		timeout=timeout,
	)
	resp.raise_for_status()
	return resp.json()


def api_post(
	api_key: str,
	base_url: str,
	path: str,
	payload: Dict,
	timeout: int = 15,
) -> Dict[str, Any]:
	"""POST request to Mocha API."""
	resp = requests.post(
		f"{base_url.rstrip('/')}{path}",
		headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
		json=payload,
		timeout=timeout,
	)
	resp.raise_for_status()
	return resp.json()


def api_delete(
	api_key: str,
	base_url: str,
	path: str,
	params: Optional[Dict] = None,
	timeout: int = 15,
) -> Dict[str, Any]:
	"""DELETE request to Mocha API."""
	resp = requests.delete(
		f"{base_url.rstrip('/')}{path}",
		headers={"Authorization": f"Bearer {api_key}"},
		params=params or {},
		timeout=timeout,
	)
	resp.raise_for_status()
	return resp.json() if resp.content else {}


def api_put(
	api_key: str,
	base_url: str,
	path: str,
	data: bytes,
	timeout: int = 7200,
	extra_headers: Optional[Dict] = None,
) -> Dict[str, Any]:
	"""PUT request to Mocha API (for direct file operations)."""
	headers = {"Authorization": f"Bearer {api_key}"}
	if extra_headers:
		headers.update(extra_headers)
	resp = requests.put(
		f"{base_url.rstrip('/')}{path}",
		headers=headers,
		data=data,
		timeout=timeout,
	)
	resp.raise_for_status()
	return resp.json()
