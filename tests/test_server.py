"""API tests against a real socket - the phone and dashboard both depend on it."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from calist import server


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.Handler.token = ""
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=10) as r:
            return r.status, r.read()

    def post(self, path, payload):
        req = urllib.request.Request(
            self.url(path), data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_dashboard_html_is_served(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Calist</title>", body)

    def test_now_endpoint_returns_text_for_the_phone(self):
        status, body = self.get("/api/now?format=text")
        self.assertEqual(status, 200)
        self.assertTrue(body.strip())

    def test_today_payload_has_blocks_and_anchors(self):
        status, body = self.get("/api/today")
        data = json.loads(body)
        self.assertEqual(status, 200)
        for key in ("date", "blocks", "anchors", "free_windows", "work_minutes"):
            self.assertIn(key, data)

    def test_status_payload_shape(self):
        status, body = self.get("/api/status")
        data = json.loads(body)
        self.assertEqual(status, 200)
        for key in ("essays", "stats", "habits", "streak", "late", "unplaceable"):
            self.assertIn(key, data)

    def test_usage_accepts_a_batch_from_the_phone(self):
        status, data = self.post("/api/usage", [
            {"ts": "2026-08-30T21:00:00", "app": "instagram", "event": "open"},
            {"ts": "2026-08-30T21:40:00", "app": "instagram", "event": "close"},
        ])
        self.assertEqual(status, 200)
        self.assertEqual(data["written"], 2)

    def test_done_requires_a_task_id(self):
        status, data = self.post("/api/done", {})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_unknown_endpoint_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/nonsense")
        self.assertEqual(ctx.exception.code, 404)

    def test_static_path_traversal_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/../calist/store.py")
        self.assertEqual(ctx.exception.code, 404)


class TestTokenAuth(unittest.TestCase):
    """A token matters because LAN mode exposes this with no login."""

    @classmethod
    def setUpClass(cls):
        server.Handler.token = "s3cret"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        server.Handler.token = ""

    def test_api_without_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status", timeout=10)
        self.assertEqual(ctx.exception.code, 403)

    def test_api_with_token_is_allowed(self):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/status?t=s3cret", timeout=10
        ) as r:
            self.assertEqual(r.status, 200)

    def test_dashboard_itself_stays_reachable(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=10) as r:
            self.assertEqual(r.status, 200)


if __name__ == "__main__":
    unittest.main()
