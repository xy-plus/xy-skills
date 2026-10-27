"""Run with python3 -m unittest discover -s /absolute/path/to/tests -v."""

from pathlib import Path
import fcntl
import io
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import clean

SCRIPT = Path(__file__).resolve().parents[1] / "clean.py"


def descriptor_directory(descriptor):
    return Path(os.readlink(f"/proc/self/fd/{descriptor}"))

# All test paths travel through the environment, except command-line fixtures.
# This prevents cwd/fd/maps tests from accidentally passing via argv matches.
PROCESS_CODE = r"""
import ctypes
import os
import sys
from pathlib import Path

item = Path(os.environ["CLEANER_TEST_ITEM"])
action = os.environ["CLEANER_TEST_ACTION"]
if action == "executable":
    os.environ["CLEANER_TEST_ACTION"] = "idle"
    os.execve(str(item / "python"), ["python", "-c", os.environ["CLEANER_TEST_CODE"]], os.environ)
if action in ("cwd", "hidden-cwd"):
    os.chdir(item)
if action == "file":
    descriptor = os.open(item / "data", os.O_RDONLY)
if action == "directory":
    descriptor = os.open(item, os.O_RDONLY | os.O_DIRECTORY)
if action == "handshake":
    import fcntl
    root_descriptor = os.open(os.environ["CLEANER_TEST_ROOT"], os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(root_descriptor, fcntl.LOCK_EX)
    descriptor = os.open(item, os.O_RDONLY | os.O_DIRECTORY)
    os.close(root_descriptor)
if action == "mapping":
    library = ctypes.CDLL(None, use_errno=True)
    library.mmap.restype = ctypes.c_void_p
    descriptor = os.open(item / "data", os.O_RDONLY)
    address = library.mmap(None, 4096, 1, 2, descriptor, 0)
    if address == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_errno(), "mmap")
    os.close(descriptor)
if action.startswith("hidden"):
    if ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) != 0:
        raise RuntimeError("PR_SET_DUMPABLE failed")
print("ready", flush=True)
sys.stdin.read(1)
"""


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def command(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            text=True, capture_output=True, check=False,
        )

    # Omitting --mode must not silently select a destructive mode.
    def test_mode_is_required(self):
        result = self.command(str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--mode", result.stderr)

    # An explicit list of a fresh root succeeds without changing its contents.
    def test_list_keeps_fresh_items(self):
        item = self.root / "fresh"
        item.write_text("keep")
        result = self.command("--mode", "list", str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(item.read_text(), "keep")

    # A missing root argument must not select an implicit cleanup location.
    def test_root_is_required(self):
        result = self.command("--mode", "list")
        self.assertEqual(result.returncode, 2)
        self.assertIn("roots", result.stderr)

    # Retention is not user configurable through a second CLI policy.
    def test_days_argument_is_rejected(self):
        result = self.command("--mode", "list", "--days", "1", str(self.root))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--days", result.stderr)


class CleanerTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "root"
        self.root.mkdir()
        self.cutoff = time.time() + 60

    def run_cleaner(self, roots, mode, cutoff):
        output, errors = io.StringIO(), io.StringIO()
        status = clean.clean_roots(roots, mode, cutoff, output, errors)
        return status, [json.loads(line) for line in output.getvalue().splitlines()], errors.getvalue()

    def start_process(self, item, action, arguments):
        environment = os.environ.copy()
        environment.update(CLEANER_TEST_ITEM=str(item), CLEANER_TEST_ACTION=action,
                           CLEANER_TEST_CODE=PROCESS_CODE, CLEANER_TEST_ROOT=str(self.root))
        process = subprocess.Popen(
            [sys.executable, "-c", PROCESS_CODE, *arguments], env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=self.temporary.name,
        )
        self.addCleanup(self.stop_process, process)
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, "child did not become ready")
        self.assertEqual(process.stdout.readline(), "ready\n")
        return process

    @staticmethod
    def stop_process(process):
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def roots(self):
        return [str(self.root), str(self.root / "-*"),
                str(self.root / "-*" / "xy-goal-inbox")]


class ListingTests(CleanerTestCase):
    # Omitting recursive age checks would list a partially fresh item.
    def test_lists_whole_expired_item_but_keeps_deep_new_file(self):
        expired = self.root / "expired"
        (expired / "nested").mkdir(parents=True)
        (expired / "nested" / "old.txt").write_text("old")
        fresh = self.root / "fresh"
        (fresh / "nested").mkdir(parents=True)
        new_file = fresh / "nested" / "new.txt"
        new_file.write_text("new")
        os.utime(new_file, (self.cutoff + 1, self.cutoff + 1))
        status, paths, errors = self.run_cleaner(
            [str(self.root)], "list", self.cutoff
        )
        self.assertEqual(paths, [str(expired)])
        self.assertEqual(status, 0)
        self.assertEqual(errors, "")
        self.assertTrue((expired / "nested" / "old.txt").exists())

    # A fresh ctime must keep an old file that just arrived by rename.
    def test_newly_moved_old_file_is_kept_by_ctime(self):
        expired = self.root / "expired"
        expired.write_text("old")
        source = Path(self.temporary.name) / "source"
        source.write_text("old contents")
        old = time.time() - 1000
        os.utime(source, (old, old))
        cutoff = time.time()
        time.sleep(0.02)
        moved = self.root / "moved"
        source.rename(moved)
        status, paths, errors = self.run_cleaner(
            [str(self.root)], "list", cutoff
        )
        self.assertEqual(paths, [str(expired)])
        self.assertTrue(moved.exists())
        self.assertEqual((status, errors), (0, ""))

    # Equality is not "earlier than" the cutoff.
    def test_mtime_at_cutoff_is_not_expired(self):
        expired = self.root / "expired"
        expired.touch()
        boundary = self.root / "boundary"
        boundary.touch()
        os.utime(boundary, (self.cutoff, self.cutoff))
        _, paths, _ = self.run_cleaner([str(self.root)], "list", self.cutoff)
        self.assertEqual(paths, [str(expired)])

    # Symlink targets, even fresh directories, are not part of an item's age.
    def test_symlink_is_judged_without_following_target(self):
        target = Path(self.temporary.name) / "target"
        target.mkdir()
        os.utime(target, (self.cutoff + 1, self.cutoff + 1))
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        dangling = self.root / "dangling"
        dangling.symlink_to(target / "missing")
        _, paths, _ = self.run_cleaner([str(self.root)], "list", self.cutoff)
        self.assertEqual(paths, [str(dangling), str(link)])
        self.assertTrue(link.is_symlink())

    # The reserved trash directory must never be emitted as a cleanup unit.
    def test_list_neither_lists_nor_clears_trash(self):
        expired = self.root / "expired"
        expired.touch()
        trash = self.root / ".runs-cleaner-trash"
        trash.mkdir()
        leftover = trash / "leftover"
        leftover.write_text("from interrupted delete")
        _, paths, _ = self.run_cleaner([str(self.root)], "list", self.cutoff)
        self.assertEqual(paths, [str(expired)])
        self.assertTrue(leftover.exists())

    # Quoting globs in the shell must still expand to all matching roots.
    def test_script_expands_root_globs(self):
        first = self.root / "-first"
        second = self.root / "-second"
        first.mkdir()
        second.mkdir()
        (first / "one").touch()
        (second / "two").touch()
        _, paths, _ = self.run_cleaner(
            [str(self.root / "-*")], "list", self.cutoff
        )
        self.assertEqual(paths, [str(first / "one"), str(second / "two")])

    # One missing root must not prevent processing another root.
    def test_missing_root_warns_and_continues(self):
        expired = self.root / "expired"
        expired.touch()
        missing = Path(self.temporary.name) / "missing"
        status, paths, errors = self.run_cleaner(
            [str(missing), str(self.root)], "list", self.cutoff
        )
        self.assertEqual(paths, [str(expired)])
        self.assertIn(str(missing), errors)
        self.assertEqual(status, 0)


class DeletionTests(CleanerTestCase):
    # Deleting just descendants would leave half of the expired unit behind.
    def test_delete_removes_an_entire_expired_item(self):
        item = self.root / "expired"
        (item / "nested").mkdir(parents=True)
        (item / "nested" / "data").write_text("old")
        status, paths, errors = self.run_cleaner(
            [str(self.root)], "delete", self.cutoff
        )
        self.assertFalse(item.exists())
        self.assertEqual(paths, [str(item)])
        self.assertEqual((status, errors), (0, ""))

    # Removing a directory symlink must leave its target untouched.
    def test_delete_unlinks_symlink_and_preserves_target(self):
        target = Path(self.temporary.name) / "target"
        target.mkdir()
        data = target / "data"
        data.write_text("keep")
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertFalse(link.is_symlink())
        self.assertEqual(data.read_text(), "keep")

    # An interrupted deletion must be completed before scanning ordinary units.
    def test_delete_clears_previous_trash(self):
        trash = self.root / ".runs-cleaner-trash"
        (trash / "leftover" / "nested").mkdir(parents=True)
        (trash / "leftover" / "nested" / "data").touch()
        _, paths, errors = self.run_cleaner(
            [str(self.root)], "delete", self.cutoff
        )
        self.assertEqual(list(trash.iterdir()), [])
        self.assertEqual(paths, [])
        self.assertEqual(errors, "")

    # A per-item rename failure must be reported while a later item is deleted.
    def test_item_error_reports_and_continues(self):
        first, second = self.root / "first", self.root / "second"
        first.touch()
        second.touch()
        real_rename = os.rename

        def fail_first(source, destination, *, src_dir_fd, dst_dir_fd):
            if descriptor_directory(src_dir_fd) / source == first:
                raise PermissionError("test rename denied")
            return real_rename(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        with mock.patch("clean.os.rename", side_effect=fail_first):
            status, paths, errors = self.run_cleaner(
                [str(self.root)], "delete", self.cutoff
            )
        self.assertFalse(second.exists())
        self.assertTrue(first.exists())
        self.assertEqual(paths, [str(second)])
        self.assertNotEqual(status, 0)
        self.assertIn(str(first), errors)
        self.assertIn("test rename denied", errors)

    # Even a generated-name collision must not overwrite a previous moved unit.
    def test_trash_name_collision_preserves_both_items(self):
        first, second = self.root / "first", self.root / "second"
        first.mkdir()
        second.mkdir()
        (first / "data").write_text("first")
        (second / "data").write_text("second")
        real_remove = clean.remove_item

        def interrupt_first(path):
            if path.name == "occupied":
                raise PermissionError("test interrupted first removal")
            real_remove(path)

        names = [SimpleNamespace(hex="occupied"), SimpleNamespace(hex="occupied"),
                 SimpleNamespace(hex="available")]
        with mock.patch("clean.uuid.uuid4", side_effect=names), \
                mock.patch("clean.remove_item", side_effect=interrupt_first):
            status, paths, errors = self.run_cleaner(
                [str(self.root)], "delete", self.cutoff
            )
        self.assertFalse(second.exists())
        self.assertFalse(first.exists())
        self.assertEqual((self.root / ".runs-cleaner-trash" / "occupied" / "data").read_text(),
                         "first")
        self.assertEqual(paths, [str(second)])
        self.assertNotEqual(status, 0)
        self.assertIn("test interrupted first removal", errors)


class ListingIdentityTests(CleanerTestCase):
    # Escapes must preserve control characters, quotes and non-UTF-8 filename bytes.
    def test_list_uses_one_json_string_per_arbitrary_filename(self):
        names = ["line\nbreak", "carriage\rreturn", 'quote"back\\slash',
                 os.fsdecode(b"byte-\xff")]
        items = [self.root / name for name in names]
        for item in items:
            item.touch()
        output, errors = io.StringIO(), io.StringIO()
        status = clean.clean_roots(
            [str(self.root)], "list", self.cutoff, output, errors
        )
        try:
            encoded = output.getvalue().encode("ascii")
            paths = [json.loads(line) for line in encoded.splitlines()]
        except (UnicodeError, ValueError) as error:
            self.fail(f"each path must have an unambiguous ASCII JSON record: {error}")
        self.assertEqual(set(paths), {str(item) for item in items})
        self.assertEqual((status, errors.getvalue()), (0, ""))


class ProcessTests(CleanerTestCase):
    def make_item(self, name):
        item = self.root / name
        item.mkdir()
        (item / "data").write_bytes(b"x" * 4096)
        return item

    # Each /proc reference must protect an item, without argv mentioning it.
    def test_cwd_open_files_directory_handles_and_mappings_keep_items(self):
        for action in ("cwd", "file", "directory", "mapping", "executable"):
            with self.subTest(action=action):
                item = self.make_item(action)
                if action == "executable":
                    shutil.copy2(sys.executable, item / "python")
                process = self.start_process(item, action, [])
                self.run_cleaner([str(self.root)], "delete", self.cutoff)
                self.assertTrue(item.exists(), action)
                self.stop_process(process)

    # The real argv match must accept descendants, equals options and whitespace.
    def test_complete_command_paths_keep_items(self):
        for suffix in ("", "/data", " extra"):
            with self.subTest(suffix=suffix):
                item = self.make_item("command")
                process = self.start_process(item, "idle", [f"--output={item}{suffix}"])
                self.run_cleaner([str(self.root)], "delete", self.cutoff)
                self.assertTrue(item.exists())
                self.stop_process(process)
                shutil.rmtree(item)

    # A prefix sibling is not the item, even if the full path is a substring.
    def test_command_path_prefix_does_not_keep_item(self):
        item = self.make_item("command")
        self.start_process(item, "idle", [str(item) + "-other"])
        self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertFalse(item.exists())

    # A path preceded by a different absolute path is not a complete path match.
    def test_embedded_command_path_does_not_keep_item(self):
        item = self.make_item("command")
        self.start_process(item, "idle", ["/elsewhere" + str(item)])
        self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertFalse(item.exists())

    # A non-dumpable process still exposes its own user's command line.
    def test_hidden_process_with_command_path_keeps_item(self):
        item = self.make_item("hidden")
        self.start_process(item, "hidden-cwd", [str(item)])
        self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertTrue(item.exists())

    # Hidden references without a command path are the explicitly accepted blind spot.
    def test_hidden_process_without_command_path_does_not_block_delete(self):
        item = self.make_item("hidden")
        process = self.start_process(item, "hidden-cwd", [])
        with self.assertRaises(PermissionError):
            os.readlink(f"/proc/{process.pid}/cwd")
        self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertFalse(item.exists())

    # The candidate list omits items in use by this user's processes.
    def test_list_keeps_in_use_item(self):
        item = self.make_item("busy")
        self.start_process(item, "file", [])
        _, paths, _ = self.run_cleaner([str(self.root)], "list", self.cutoff)
        self.assertEqual(paths, [])
        self.assertTrue(item.exists())


class NestedRootTests(CleanerTestCase):
    def keep_fresh(self, directory):
        file = directory / "fresh"
        file.touch()
        os.utime(file, (self.cutoff + 1, self.cutoff + 1))

    # Every rename, including inbox sessions, must hold the outermost root lock.
    def test_all_three_levels_use_outer_lock_and_outer_trash(self):
        old_project = self.root / "-old-project"
        old_project.mkdir()
        (old_project / "data").touch()
        project = self.root / "-project"
        project.mkdir()
        self.keep_fresh(project)
        matter = project / "matter"
        matter.mkdir()
        (matter / "data").touch()
        inbox = project / "xy-goal-inbox"
        inbox.mkdir()
        self.keep_fresh(inbox)
        session = inbox / "session"
        session.mkdir()
        (session / "quote").touch()
        moved = []
        real_rename = os.rename
        real_remove = clean.remove_item

        def verify_move(source, destination, *, src_dir_fd, dst_dir_fd):
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
            self.assertEqual(descriptor_directory(dst_dir_fd), self.root / ".runs-cleaner-trash")
            moved.append(descriptor_directory(src_dir_fd) / source)
            return real_rename(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

        def verify_remove(path):
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(descriptor)
            real_remove(path)

        with mock.patch("clean.os.rename", side_effect=verify_move), \
                mock.patch("clean.remove_item", side_effect=verify_remove):
            status, _, errors = self.run_cleaner(self.roots(), "delete", self.cutoff)
        self.assertEqual(set(moved), {old_project, matter, session})
        self.assertEqual((status, errors), (0, ""))
        self.assertFalse((project / ".runs-cleaner-trash").exists())
        self.assertFalse((inbox / ".runs-cleaner-trash").exists())

    # A same-named item in two roots must survive an interrupted first removal.
    def test_same_names_do_not_collide_and_leftovers_clear_next_round(self):
        items = []
        for name in ("-one", "-two"):
            project = self.root / name
            project.mkdir()
            self.keep_fresh(project)
            item = project / "same"
            item.mkdir()
            (item / "data").touch()
            items.append(item)
        destinations = []

        def interrupt_remove(path):
            destinations.append(path)
            raise PermissionError("test interrupted removal")

        with mock.patch("clean.remove_item", side_effect=interrupt_remove):
            status, _, errors = self.run_cleaner(
                self.roots()[:2], "delete", self.cutoff
            )
        trash = self.root / ".runs-cleaner-trash"
        self.assertTrue(trash.is_dir(), "trash belongs in the outermost root")
        self.assertEqual(len(list(trash.iterdir())), 2)
        self.assertEqual(len(set(destinations)), 2)
        self.assertTrue(all(not item.exists() for item in items))
        self.assertNotEqual(status, 0)
        self.assertIn("test interrupted removal", errors)
        status, _, errors = self.run_cleaner(self.roots()[:2], "delete", self.cutoff)
        self.assertEqual(list((self.root / ".runs-cleaner-trash").iterdir()), [])
        self.assertEqual((status, errors), (0, ""))

    # A legacy flat item is an indivisible unit, not a second-level root.
    def test_legacy_flat_item_is_not_split_by_project_glob(self):
        flat = self.root / "legacy"
        flat.mkdir()
        self.keep_fresh(flat)
        old_child = flat / "old-child"
        old_child.mkdir()
        (old_child / "data").touch()
        old_flat = self.root / "old-legacy"
        old_flat.mkdir()
        (old_flat / "data").touch()
        status, paths, _ = self.run_cleaner(self.roots(), "delete", self.cutoff)
        self.assertTrue(old_child.exists())
        self.assertFalse(old_flat.exists())
        self.assertEqual(paths, [str(old_flat)])
        self.assertEqual(status, 0)


class LockAndBoundaryTests(CleanerTestCase):
    def replacement_fixture(self):
        project = self.root / "-project"
        project.mkdir()
        (project / "matter").mkdir()
        (project / "matter" / "data").touch()
        fresh = project / "fresh"
        fresh.touch()
        os.utime(fresh, (self.cutoff + 1, self.cutoff + 1))
        outside = Path(self.temporary.name) / "outside"
        victim = outside / "matter" / "data"
        victim.parent.mkdir(parents=True)
        victim.write_text("outside must survive")
        return project, outside, victim

    def replace_project_with_symlink(self, project, outside):
        project.rename(Path(self.temporary.name) / "saved-project")
        project.symlink_to(outside, target_is_directory=True)
        os.utime(project, (self.cutoff + 1, self.cutoff + 1), follow_symlinks=False)

    # Root expansion is a snapshot: a later symlink cannot become a cleanup root.
    def test_project_replaced_with_symlink_after_expansion_is_reported(self):
        project, outside, victim = self.replacement_fixture()
        real_expand = clean.expand_roots

        def expand_then_replace(patterns, errors):
            roots = real_expand(patterns, errors)
            self.replace_project_with_symlink(project, outside)
            return roots

        with mock.patch("clean.expand_roots", side_effect=expand_then_replace):
            status, paths, errors = self.run_cleaner(
                self.roots()[:2], "delete", self.cutoff
            )
        self.assertTrue(victim.exists())
        self.assertEqual(paths, [])
        self.assertNotEqual(status, 0)
        self.assertIn(str(project), errors)

    # Checking before locking is insufficient if the root changes at acquisition.
    def test_project_replaced_with_symlink_at_lock_is_reported(self):
        project, outside, victim = self.replacement_fixture()
        real_flock = fcntl.flock

        def lock_then_replace(descriptor, operation):
            real_flock(descriptor, operation)
            self.replace_project_with_symlink(project, outside)

        with mock.patch("clean.fcntl.flock", side_effect=lock_then_replace):
            status, paths, errors = self.run_cleaner(
                self.roots()[:2], "delete", self.cutoff
            )
        self.assertTrue(victim.exists())
        self.assertEqual(paths, [])
        self.assertNotEqual(status, 0)
        self.assertIn(str(project), errors)

    # A replacement real directory is also a different, unverified cleanup root.
    def test_project_replaced_with_different_directory_is_reported(self):
        project, outside, victim = self.replacement_fixture()
        real_expand = clean.expand_roots

        def expand_then_replace(patterns, errors):
            roots = real_expand(patterns, errors)
            project.rename(Path(self.temporary.name) / "saved-project")
            outside.rename(project)
            fresh = project / "fresh"
            fresh.touch()
            os.utime(fresh, (self.cutoff + 1, self.cutoff + 1))
            return roots

        with mock.patch("clean.expand_roots", side_effect=expand_then_replace):
            status, paths, errors = self.run_cleaner(
                self.roots()[:2], "delete", self.cutoff
            )
        self.assertTrue((project / "matter" / "data").exists())
        self.assertEqual(paths, [])
        self.assertNotEqual(status, 0)
        self.assertIn(str(project), errors)

    # A swap after validation must not redirect the actual rename to the target.
    def test_project_replaced_at_rename_does_not_move_symlink_target(self):
        project, outside, victim = self.replacement_fixture()
        real_rename = os.rename

        def replace_then_rename(source, destination, **options):
            real_rename(project, Path(self.temporary.name) / "saved-project")
            project.symlink_to(outside, target_is_directory=True)
            os.utime(project, (self.cutoff + 1, self.cutoff + 1), follow_symlinks=False)
            return real_rename(source, destination, **options)

        with mock.patch("clean.os.rename", side_effect=replace_then_rename):
            clean.clean_roots(self.roots()[:2], "delete", self.cutoff,
                              io.StringIO(), io.StringIO())
        self.assertTrue(victim.exists())

    # A writer's change between initial selection and the locked recheck wins.
    def test_new_modification_at_lock_acquisition_prevents_delete(self):
        item = self.root / "matter"
        item.mkdir()
        (item / "old").touch()
        real_flock = fcntl.flock

        def change_at_lock(descriptor, operation):
            real_flock(descriptor, operation)
            new_file = item / "new"
            new_file.touch()
            os.utime(new_file, (self.cutoff + 1, self.cutoff + 1))

        with mock.patch("clean.fcntl.flock", side_effect=change_at_lock):
            status, paths, errors = self.run_cleaner(
                [str(self.root)], "delete", self.cutoff
            )
        self.assertTrue((item / "new").exists())
        self.assertEqual((status, paths, errors), (0, [], ""))

    # A handshake must release the lock while the directory handle protects both levels.
    def test_writer_handshake_keeps_matter_and_project(self):
        project = self.root / "-project"
        item = project / "matter"
        item.mkdir(parents=True)
        (item / "data").touch()
        self.start_process(item, "handshake", [])
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(descriptor)
        status, paths, _ = self.run_cleaner(self.roots()[:2], "delete", self.cutoff)
        self.assertTrue(item.exists())
        self.assertTrue(project.exists())
        self.assertEqual((status, paths), (0, []))

    # The inherited handle must remain sufficient after the wrapper has exited.
    def test_child_inherits_handle_after_writer_exits(self):
        project = self.root / "-project"
        item = project / "matter"
        item.mkdir(parents=True)
        (item / "data").touch()
        code = r"""
import fcntl, os, subprocess, sys
root = os.open(os.environ["CLEANER_TEST_ROOT"], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(root, fcntl.LOCK_EX)
handle = os.open(os.environ["CLEANER_TEST_ITEM"], os.O_RDONLY | os.O_DIRECTORY)
os.close(root)
subprocess.Popen([sys.executable, "-c", "import os,sys; print(os.getpid(), flush=True); sys.stdin.read(1)"],
                 pass_fds=(handle,), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
os.close(handle)
"""
        environment = os.environ.copy()
        environment.update(CLEANER_TEST_ROOT=str(self.root), CLEANER_TEST_ITEM=str(item))
        wrapper = subprocess.Popen(
            [sys.executable, "-c", code], env=environment, cwd=self.temporary.name,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(lambda: wrapper.communicate("x", timeout=5))
        ready, _, _ = select.select([wrapper.stdout], [], [], 5)
        self.assertTrue(ready)
        child = int(wrapper.stdout.readline())
        self.assertEqual(wrapper.wait(timeout=5), 0)
        self.assertTrue(Path(f"/proc/{child}").exists())
        _, paths, _ = self.run_cleaner(self.roots()[:2], "delete", self.cutoff)
        self.assertTrue(item.exists())
        self.assertTrue(project.exists())
        self.assertEqual(paths, [])

    # The writer starts after rename while the cleaner still holds the root lock.
    def test_cleaner_renames_first_so_writer_exits_without_writing(self):
        item = self.root / "matter"
        item.mkdir()
        (item / "data").touch()
        code = r"""
import fcntl, os, sys
root = os.open(os.environ["CLEANER_TEST_ROOT"], os.O_RDONLY | os.O_DIRECTORY)
print("waiting", flush=True)
fcntl.flock(root, fcntl.LOCK_EX)
try:
    handle = os.open(os.environ["CLEANER_TEST_ITEM"], os.O_RDONLY | os.O_DIRECTORY)
except FileNotFoundError:
    print("missing", flush=True)
    sys.exit(2)
with open(os.environ["CLEANER_TEST_ITEM"] + "/unexpected-write", "w") as stream:
    stream.write("written")
os.close(handle)
"""
        environment = os.environ.copy()
        environment.update(CLEANER_TEST_ROOT=str(self.root), CLEANER_TEST_ITEM=str(item))
        writers = []
        real_rename = os.rename

        def rename_then_start_writer(source, destination, *, src_dir_fd, dst_dir_fd):
            real_rename(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            writer = subprocess.Popen(
                [sys.executable, "-c", code], env=environment, cwd=self.temporary.name,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            writers.append(writer)
            self.addCleanup(self.stop_process, writer)
            ready, _, _ = select.select([writer.stdout], [], [], 5)
            self.assertTrue(ready)
            self.assertEqual(writer.stdout.readline(), "waiting\n")

        with mock.patch("clean.os.rename", side_effect=rename_then_start_writer):
            status, paths, errors = self.run_cleaner(
                [str(self.root)], "delete", self.cutoff
            )
        self.assertEqual(len(writers), 1)
        self.assertEqual(writers[0].wait(timeout=5), 2)
        self.assertEqual(writers[0].stdout.read(), "missing\n")
        self.assertFalse(item.exists())
        self.assertEqual(list((self.root / ".runs-cleaner-trash").iterdir()), [])
        self.assertEqual((status, paths, errors), (0, [str(item)], ""))

    # Recursive read failures keep the whole item, while other items still delete.
    def test_unreadable_subdirectory_keeps_whole_item(self):
        item = self.root / "unreadable"
        blocked = item / "blocked"
        blocked.mkdir(parents=True)
        (blocked / "data").touch()
        blocked.chmod(0)
        self.addCleanup(lambda: blocked.chmod(0o700) if blocked.exists() else None)
        other = self.root / "other"
        other.touch()
        _, paths, _ = self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertTrue(blocked.exists())
        self.assertFalse(other.exists())
        self.assertEqual(paths, [str(other)])

    # A project symlink must not become a traversal route to an outside inbox.
    def test_nested_glob_does_not_follow_project_symlink(self):
        outside = Path(self.temporary.name) / "outside"
        session = outside / "xy-goal-inbox" / "session"
        session.mkdir(parents=True)
        (session / "data").touch()
        link = self.root / "-link"
        link.symlink_to(outside, target_is_directory=True)
        os.utime(link, (self.cutoff + 1, self.cutoff + 1), follow_symlinks=False)
        self.run_cleaner(self.roots(), "delete", self.cutoff)
        self.assertTrue((session / "data").exists())
        self.assertTrue(link.is_symlink())

    # A pre-existing trash symlink must never expose its target to recovery deletion.
    def test_trash_symlink_is_reported_without_following_target(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        data = outside / "data"
        data.write_text("keep")
        (self.root / ".runs-cleaner-trash").symlink_to(outside, target_is_directory=True)
        item = self.root / "item"
        item.touch()
        status, _, errors = self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertTrue(data.exists())
        self.assertTrue(item.exists())
        self.assertNotEqual(status, 0)
        self.assertIn(".runs-cleaner-trash", errors)


class ProcessBoundaryTests(CleanerTestCase):
    def process_fixture(self, uid):
        process = Path(self.temporary.name) / "proc" / "123"
        process.mkdir(parents=True)
        (process / "status").write_text(f"Name:\tfixture\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n")
        (process / "cmdline").write_bytes(b"python\0")
        (process / "fd").mkdir()
        (process / "maps").write_text("")
        real_iterdir = Path.iterdir

        def fixture_processes(path):
            return iter([process]) if path == Path("/proc") else real_iterdir(path)

        return process, mock.patch("clean.Path.iterdir", fixture_processes)

    # Chroot needs privileges; a proc fixture verifies the root-reference boundary.
    def test_process_root_reference_keeps_item(self):
        item = self.root / "matter"
        item.mkdir()
        process, fixture = self.process_fixture(os.getuid())
        (process / "root").symlink_to(item, target_is_directory=True)
        with fixture:
            self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertTrue(item.exists())

    # References from another account, including root, must not prevent deletion.
    def test_other_uid_command_and_handle_do_not_keep_item(self):
        item = self.root / "matter"
        item.mkdir()
        process, fixture = self.process_fixture(0)
        (process / "cmdline").write_bytes(os.fsencode(item) + b"\0")
        (process / "fd" / "3").symlink_to(item, target_is_directory=True)
        with fixture:
            self.run_cleaner([str(self.root)], "delete", self.cutoff)
        self.assertFalse(item.exists())

    # Process names are bytes; a non-UTF-8 name must not hide the UID or abort a run.
    def test_non_utf8_process_name_does_not_break_uid_detection(self):
        item = self.root / "matter"
        item.mkdir()
        process, fixture = self.process_fixture(os.getuid())
        (process / "status").write_bytes(
            b"Name:\t\xff\n" + f"Uid:\t{os.getuid()}\n".encode("ascii")
        )
        (process / "fd" / "3").symlink_to(item, target_is_directory=True)
        try:
            with fixture:
                self.run_cleaner([str(self.root)], "delete", self.cutoff)
        except UnicodeError as error:
            self.fail(f"reading the UID must not decode the process name: {error}")
        self.assertTrue(item.exists())


if __name__ == "__main__":
    unittest.main()
