import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "mcp" / "process_utils.py"
SPEC = importlib.util.spec_from_file_location("colab_remote_process_utils", MODULE_PATH)
process_utils = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(process_utils)


class ProcessUtilsTests(unittest.TestCase):
    def test_windows_background_process_uses_only_no_window_flag(self):
        process = MagicMock()
        startupinfo = MagicMock()
        no_window = 0x08000000
        detached = 0x00000008
        with (
            patch.object(process_utils, "_is_windows", return_value=True),
            patch.object(
                process_utils, "windowless_python", return_value="C:/Python/pythonw.exe"
            ),
            patch.object(
                process_utils, "_windows_startupinfo", return_value=startupinfo
            ),
            patch.object(
                process_utils.subprocess,
                "CREATE_NO_WINDOW",
                no_window,
                create=True,
            ),
            patch.object(
                process_utils.subprocess,
                "DETACHED_PROCESS",
                detached,
                create=True,
            ),
            patch.object(process_utils.subprocess, "Popen", return_value=process) as popen,
        ):
            result = process_utils.background_popen(
                ["C:/Python/python.exe", "worker.py"],
                windowless_python_entrypoint=True,
            )

        self.assertIs(result, process)
        self.assertEqual(popen.call_args.args[0][0], "C:/Python/pythonw.exe")
        self.assertEqual(popen.call_args.kwargs["creationflags"], no_window)
        self.assertFalse(popen.call_args.kwargs["creationflags"] & detached)
        self.assertIs(popen.call_args.kwargs["startupinfo"], startupinfo)
        self.assertNotIn("start_new_session", popen.call_args.kwargs)
        self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_posix_background_process_starts_new_session(self):
        process = MagicMock()
        with (
            patch.object(process_utils, "_is_windows", return_value=False),
            patch.object(process_utils.subprocess, "Popen", return_value=process) as popen,
        ):
            result = process_utils.background_popen(["python3", "worker.py"])

        self.assertIs(result, process)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("creationflags", popen.call_args.kwargs)
        self.assertNotIn("startupinfo", popen.call_args.kwargs)

    def test_windowless_python_uses_sibling_pythonw(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / "python.exe"
            pythonw = root / "pythonw.exe"
            pythonw.write_bytes(b"")
            with patch.object(process_utils, "_is_windows", return_value=True):
                selected = process_utils.windowless_python(str(python))
        self.assertEqual(selected, str(pythonw))

    def test_empty_background_command_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            process_utils.background_popen([])

    @unittest.skipUnless(os.name == "nt", "Windows console integration test")
    def test_real_windows_helper_has_no_console_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "console-window.txt"
            code = (
                "import ctypes; from pathlib import Path; "
                f"Path({str(marker)!r}).write_text("
                "str(ctypes.windll.kernel32.GetConsoleWindow()), encoding='utf-8')"
            )
            process = process_utils.background_popen(
                [sys.executable, "-c", code],
                windowless_python_entrypoint=True,
            )
            self.assertEqual(process.wait(timeout=15), 0)
            self.assertEqual(marker.read_text(encoding="utf-8"), "0")


if __name__ == "__main__":
    unittest.main()
