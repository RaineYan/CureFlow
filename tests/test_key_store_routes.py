"""Local key-status reads work without Origin; writes remain guarded."""
import json
from io import BytesIO

from cureflow_app.key_store import KeyStore
from cureflow_app.server import make_handler


def _request(tmp_path, *, method="GET", path="/api/keys/status", peer="127.0.0.1", host="127.0.0.1:5173", origin=None):
    handler = object.__new__(make_handler(
        tmp_path, allow_local_provider_setup=True,
        key_store=KeyStore(tmp_path / "providers.json"),
    ))
    body = b'{"confirm":true}' if method == "POST" else b""
    handler.path = path
    handler.client_address = (peer, 12345)
    handler.headers = {"Host": host, "Content-Type": "application/json", "Content-Length": str(len(body))}
    if origin is not None:
        handler.headers["Origin"] = origin
    handler.rfile, handler.wfile = BytesIO(body), BytesIO()
    result = {}
    handler.send_response = lambda status: result.update(status=status)
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    getattr(handler, f"do_{method}")()
    result["body"] = json.loads(handler.wfile.getvalue())
    return result


def test_local_browser_can_read_key_status_without_origin(tmp_path):
    result = _request(tmp_path)
    assert result["status"] == 200
    assert result["body"] == {"optedIn": False, "providers": {}, "lastError": None}


def test_remote_peer_cannot_read_key_status(tmp_path):
    assert _request(tmp_path, peer="192.0.2.10")["status"] == 403


def test_wrong_host_cannot_read_key_status(tmp_path):
    assert _request(tmp_path, host="evil.example")["status"] == 403


def test_key_mutation_still_requires_same_origin(tmp_path):
    result = _request(tmp_path, method="POST", path="/api/keys/opt-in")
    assert result["status"] == 403
    assert not (tmp_path / "providers.json").exists()
