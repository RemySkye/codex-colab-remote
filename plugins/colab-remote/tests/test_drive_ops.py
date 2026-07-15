import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

DRIVE_OPS_PATH = Path(__file__).resolve().parents[1] / "mcp" / "drive_ops.py"
SPEC = importlib.util.spec_from_file_location("colab_remote_drive_ops", DRIVE_OPS_PATH)
drive_ops = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = drive_ops
SPEC.loader.exec_module(drive_ops)


class DriveOperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.content = self.root / "content"
        self.mount = self.content / "drive"
        (self.mount / "MyDrive").mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def perform(self, **payload):
        return drive_ops.perform(
            payload, mount_root=self.mount, content_root=self.content
        )

    def test_bootstrap_creates_only_dedicated_workspace(self):
        result = self.perform(action="bootstrap")
        self.assertEqual(result["drive_path"], "MyDrive/codex-colab")
        self.assertTrue((self.mount / "MyDrive" / "codex-colab").is_dir())

    def test_file_and_folder_round_trip(self):
        source = self.content / "training"
        source.mkdir()
        (source / "checkpoint.bin").write_bytes(b"checkpoint")

        saved = self.perform(
            action="save",
            remote_path=str(source),
            drive_path="runs/model-1",
            overwrite=False,
        )
        self.assertEqual(saved["drive_path"], "MyDrive/codex-colab/runs/model-1")

        listing = self.perform(
            action="list",
            drive_path="runs",
            recursive=True,
            max_entries=20,
        )
        self.assertEqual(listing["entry_count"], 2)
        self.assertTrue(
            any(
                item["relative_path"] == "runs/model-1/checkpoint.bin"
                for item in listing["entries"]
            )
        )

        restored = self.content / "restored"
        self.perform(
            action="restore",
            drive_path="runs/model-1",
            remote_path=str(restored),
            overwrite=False,
        )
        self.assertEqual((restored / "checkpoint.bin").read_bytes(), b"checkpoint")

    def test_move_and_confirmed_delete_stay_inside_workspace(self):
        self.perform(action="mkdir", drive_path="runs/old")
        moved = self.perform(
            action="move",
            source_drive_path="runs/old",
            destination_drive_path="archive/new",
            overwrite=False,
        )
        self.assertEqual(moved["drive_path"], "MyDrive/codex-colab/archive/new")
        with self.assertRaises(PermissionError):
            self.perform(action="delete", drive_path="archive/new", confirm=False)
        deleted = self.perform(
            action="delete", drive_path="archive/new", confirm=True
        )
        self.assertTrue(deleted["deleted"])

    def test_paths_cannot_escape_codex_colab(self):
        private = self.mount / "MyDrive" / "private.txt"
        private.write_text("private", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.perform(action="mkdir", drive_path="../private")
        with self.assertRaises(PermissionError):
            self.perform(
                action="save",
                remote_path=str(private),
                drive_path="stolen.txt",
            )
        workspace_file = self.mount / "MyDrive" / "codex-colab" / "safe.txt"
        workspace_file.write_text("safe", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.perform(
                action="restore",
                drive_path="safe.txt",
                remote_path=str(private),
            )
        with self.assertRaises(ValueError):
            self.perform(action="delete", drive_path=".", confirm=True)

    def test_content_root_cannot_be_copied_into_drive(self):
        with self.assertRaises(PermissionError):
            self.perform(
                action="save",
                remote_path=str(self.content),
                drive_path="content-copy",
            )

    def test_absolute_and_ambiguous_drive_paths_are_rejected(self):
        for value in ("/private", "folder/../private", "folder//file", "folder/"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                drive_ops.normalize_drive_path(value)

    def test_symlink_cannot_escape_workspace(self):
        self.perform(action="bootstrap")
        outside = self.mount / "MyDrive" / "private"
        outside.mkdir()
        link = self.mount / "MyDrive" / "codex-colab" / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlinks are unavailable: {exc}")
        with self.assertRaises(PermissionError):
            self.perform(action="list", drive_path="link")

    def test_content_root_alias_uses_one_path_spelling(self):
        alias = self.root / "root-alias"
        try:
            alias.symlink_to(self.root, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlinks are unavailable: {exc}")
        source = self.content / "checkpoint.bin"
        source.write_bytes(b"checkpoint")
        lexical_content = alias / "content"
        result = drive_ops.perform(
            {
                "action": "save",
                "remote_path": str(lexical_content / source.name),
                "drive_path": "runs/checkpoint.bin",
            },
            mount_root=self.mount,
            content_root=lexical_content,
        )
        self.assertEqual(
            result["drive_path"], "MyDrive/codex-colab/runs/checkpoint.bin"
        )

    def test_folder_cannot_be_moved_inside_itself(self):
        self.perform(action="mkdir", drive_path="runs/current")
        with self.assertRaises(ValueError):
            self.perform(
                action="move",
                source_drive_path="runs",
                destination_drive_path="runs/current/nested",
            )

    def test_item_cannot_overwrite_a_parent_containing_it(self):
        self.perform(action="mkdir", drive_path="runs/current")
        with self.assertRaises(ValueError):
            self.perform(
                action="move",
                source_drive_path="runs/current",
                destination_drive_path="runs",
                overwrite=True,
            )
        self.assertTrue(
            (self.mount / "MyDrive" / "codex-colab" / "runs" / "current").is_dir()
        )


if __name__ == "__main__":
    unittest.main()
