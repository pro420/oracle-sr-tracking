import base64
import os
import tempfile
import unittest

try:
    from srtrack.web import create_app
except ImportError:  # Flask not installed
    create_app = None


@unittest.skipIf(create_app is None, "Flask not installed")
class WebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")
        self.app = create_app(self.db, password="")
        self.c = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, number="3-38123456789", **extra):
        data = {"number": number, "title": "ORA-600 on PROD", "severity": "1", "status": "NEW",
                "follow_up": "today", **extra}
        return self.c.post("/srs/new", data=data)

    def test_pages_render(self):
        self.add()
        for url in ["/", "/srs", "/srs?scope=all&q=ORA", "/srs/3-38123456789", "/stats", "/import", "/srs/new", "/healthz"]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)

    def test_add_update_note_close(self):
        r = self.add()
        self.assertEqual(r.status_code, 302)
        self.assertIn(b"3-38123456789", self.c.get("/").data)
        r = self.c.post("/srs/3-38123456789/note", data={"text": "Sent AWR", "status": "RVW", "follow_up": "+2d"},
                        follow_redirects=True)
        self.assertIn(b"Sent AWR", r.data)
        self.assertIn(b"Review Update", r.data)
        r = self.c.post("/srs/3-38123456789/edit", data={"title": "New title", "severity": "2"}, follow_redirects=True)
        self.assertIn(b"New title", r.data)
        r = self.c.post("/srs/3-38123456789/close", data={"resolution": "patched"}, follow_redirects=True)
        self.assertIn(b"Closed 3-38123456789", r.data)
        self.assertIn(b"No SRs match", self.c.get("/srs").data)

    def test_validation_errors(self):
        r = self.add(number="bad")
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"invalid SR number", r.data)
        self.assertEqual(self.c.get("/srs/nope").status_code, 404)

    def test_jump_box(self):
        self.add()
        r = self.c.get("/go?q=6789")
        self.assertTrue(r.headers["Location"].endswith("/srs/3-38123456789"))
        r = self.c.get("/go?q=hang")
        self.assertIn("/srs?", r.headers["Location"])

    def test_export_import(self):
        self.add()
        csv_data = self.c.get("/export.csv").data
        self.assertIn(b"3-38123456789", csv_data)
        self.assertIn(b"events", self.c.get("/export.json").data)
        import io
        r = self.c.post("/import", data={"file": (io.BytesIO(b"SR Number,Summary,Sev\n3-40000000001,Imported,2\n"), "x.csv")},
                        content_type="multipart/form-data")
        self.assertIn(b"<b>1</b> added", r.data)

    def test_delete_requires_confirm(self):
        self.add()
        self.c.post("/srs/3-38123456789/delete", data={})
        self.assertEqual(self.c.get("/srs/3-38123456789").status_code, 200)
        self.c.post("/srs/3-38123456789/delete", data={"confirm": "yes"})
        self.assertEqual(self.c.get("/srs/3-38123456789").status_code, 404)

    def test_cross_site_post_rejected(self):
        r = self.c.post("/srs/new", data={"number": "3-38123456789", "title": "x"},
                        headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)


@unittest.skipIf(create_app is None, "Flask not installed")
class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app = create_app(os.path.join(self.tmp.name, "t.db"), username="me", password="s3cret")
        self.c = app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def hdr(self, user, pw):
        return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}

    def test_auth(self):
        self.assertEqual(self.c.get("/").status_code, 401)
        self.assertEqual(self.c.get("/", headers=self.hdr("me", "wrong")).status_code, 401)
        self.assertEqual(self.c.get("/", headers=self.hdr("me", "s3cret")).status_code, 200)
        self.assertEqual(self.c.get("/healthz").status_code, 200)


if __name__ == "__main__":
    unittest.main()
