import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "drive_mount_worker.py"


@unittest.skipIf(os.name == "nt", "The PTY worker runs inside WSL on Windows")
class DriveMountWorkerTests(unittest.TestCase):
    def _wait_for(self, path: Path, event: str, timeout: float = 10) -> dict:
        deadline = time.monotonic() + timeout
        latest = {}
        while time.monotonic() < deadline:
            if path.is_file():
                try:
                    latest = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    time.sleep(0.05)
                    continue
                if latest.get("event") == event:
                    return latest
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {event}; latest state: {latest}")

    def test_worker_keeps_tty_mount_alive_until_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_colab = root / "fake-colab"
            fake_colab.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "print('Google Drive Authorization needed.')\n"
                "print('https://accounts.google.com/o/oauth2/auth?state=test')\n"
                "sys.stdout.flush()\n"
                "with open('/dev/tty') as tty:\n"
                "    tty.readline()\n"
                "print('Credentials propagated. Mounted at /content/drive')\n",
                encoding="utf-8",
            )
            fake_colab.chmod(0o700)
            state = root / "state"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(WORKER),
                    "--colab",
                    str(fake_colab),
                    "--session",
                    "test-session",
                    "--mount-path",
                    "/content/drive",
                    "--state-dir",
                    str(state),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                waiting = self._wait_for(
                    state / "status.json", "authorization_required"
                )
                self.assertIsNone(process.poll())
                self.assertGreater(waiting["child_pid"], 0)
                authorization_url = (state / "authorization.url").read_text(
                    encoding="utf-8"
                )
                self.assertTrue(authorization_url.startswith("https://accounts.google.com/"))

                (state / "resume.request").write_text("resume\n", encoding="utf-8")
                completed = self._wait_for(state / "status.json", "completed")
                self.assertEqual(completed["exit_code"], 0)
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    (state / "cancel.request").write_text("cancel\n", encoding="utf-8")
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
