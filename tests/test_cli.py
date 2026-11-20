import contextlib
import io
import os
import tempfile
import unittest

from srtrack.cli import main


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "t.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--db", self.db, *args])
        return rc, out.getvalue(), err.getvalue()

    def test_workflow(self):
        rc, out, _ = self.run_cli("add", "3-38123456789", "ORA-600 on PROD", "-s", "1", "-f", "today")
        self.assertEqual(rc, 0)
        self.assertIn("Added 3-38123456789", out)
        self.assertEqual(self.run_cli("st", "6789", "cus", "-n", "Send AWR")[0], 0)
        self.assertEqual(self.run_cli("note", "6789", "uploaded", "AWR")[0], 0)
        rc, out, _ = self.run_cli("today")
        self.assertIn("3-38123456789", out)
        rc, out, _ = self.run_cli("show", "6789")
        self.assertIn("uploaded AWR", out)
        self.assertIn("Customer Working", out)
        rc, out, _ = self.run_cli("close", "6789", "-r", "patched")
        self.assertIn("Closed", out)
        rc, out, _ = self.run_cli("ls")
        self.assertIn("No SRs", out)
        rc, out, _ = self.run_cli("ls", "--closed")
        self.assertIn("3-38123456789", out)
        rc, out, _ = self.run_cli("stats")
        self.assertIn("Closed: 1", out)

    def test_errors_return_2(self):
        rc, _, err = self.run_cli("show", "1234")
        self.assertEqual(rc, 2)
        self.assertIn("no SR matching", err)

    def test_default_command_is_today(self):
        rc, out, _ = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertIn("SR agenda", out)


if __name__ == "__main__":
    unittest.main()
