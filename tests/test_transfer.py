import json
import tempfile
import unittest
from pathlib import Path

from app import TransferStore, create_server, sanitize_filename


class FilenameTests(unittest.TestCase):
    def test_filename_discards_paths_and_control_characters(self):
        self.assertEqual(sanitize_filename("../../foto\x00\nfinal.jpg"), "foto_final.jpg")

    def test_empty_filename_gets_a_safe_fallback(self):
        self.assertEqual(sanitize_filename("../.."), "arquivo")


class StoreTests(unittest.TestCase):
    def test_uploaded_files_are_saved_and_metadata_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TransferStore(directory)
            record = store.add_file("minha foto.jpg", b"conteudo", "image/jpeg")

            self.assertEqual(store.read_file(record["id"]), b"conteudo")
            self.assertEqual(store.list_files()[0]["name"], "minha foto.jpg")

            reopened = TransferStore(directory)
            self.assertEqual(reopened.list_files()[0]["id"], record["id"])

    def test_uploaded_file_size_is_limited(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TransferStore(directory, max_file_bytes=4)
            with self.assertRaises(ValueError):
                store.add_file("large.bin", b"12345", "application/octet-stream")

    def test_manifest_never_loads_paths_outside_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "do-not-read.bin"
            outside.write_bytes(b"secret")
            manifest = [{"id": "../do-not-read", "name": "secret", "size": 6, "uploaded": 0, "type": "x"}]
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            store = TransferStore(directory)
            self.assertEqual(store.list_files(), [])
            outside.unlink()


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = TransferStore(self.temp.name)
        self.server = create_server("127.0.0.1", 0, self.store, "test-session-token", "test-admin-token")
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_admin_panel_and_status_are_protected(self):
        import json
        from urllib.error import HTTPError
        from urllib.request import urlopen

        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        url = self.base + "/?admin=test-admin-token"
        with urlopen(url) as response:
            self.assertIn(b"PAINEL DO NOTEBOOK", response.read())
        with urlopen(self.base + "/api/admin-status?admin=test-admin-token") as response:
            status = json.loads(response.read())
        self.assertIn("/s/test-session-token", status["url"])

    def test_qr_endpoint_returns_a_png(self):
        import shutil
        from urllib.request import urlopen

        if not shutil.which("qrencode"):
            self.skipTest("qrencode is unavailable")
        with urlopen(self.base + "/qr.png?admin=test-admin-token") as response:
            self.assertEqual(response.headers.get_content_type(), "image/png")
            self.assertTrue(response.read().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_share_page_requires_the_session_token(self):
        from urllib.error import HTTPError
        from urllib.request import urlopen

        with self.assertRaises(HTTPError) as error:
            urlopen(self.base + "/s/wrong-token")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        with urlopen(self.base + "/s/test-session-token") as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"Enviar arquivos", response.read())

    def test_share_page_embedded_javascript_is_valid(self):
        import re
        import shutil
        import subprocess
        from urllib.request import urlopen

        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable for JavaScript syntax validation")
        with urlopen(self.base + "/s/test-session-token") as response:
            page = response.read().decode("utf-8")
        script = re.search(r"<script>(.*?)</script>", page, re.DOTALL).group(1)
        checked = subprocess.run([node, "--check"], input=script, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_upload_and_download_work_over_http(self):
        from urllib.request import Request, urlopen

        boundary = "----test-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="nota.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "ola\r\n"
            f"--{boundary}--\r\n"
        ).encode()
        request = Request(
            self.base + "/api/upload/test-session-token",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 201)
            uploaded = json.loads(response.read())
        file_id = uploaded["files"][0]["id"]

        with urlopen(self.base + f"/download/test-session-token/{file_id}") as response:
            self.assertEqual(response.read(), b"ola")

    def test_upload_is_rejected_without_the_session_token(self):
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen

        request = Request(self.base + "/api/upload/wrong", data=b"", method="POST")
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
