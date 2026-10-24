#!/usr/bin/env python3
"""Remove whole expired, unused items from explicitly supplied Linux roots."""

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import glob
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import uuid


RETENTION_SECONDS = 30 * 24 * 60 * 60
TRASH_NAME = ".runs-cleaner-trash"
DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def is_expired(path, cutoff):
    """lstat every entry, stopping at the first new or unreadable entry."""
    pending = [path]
    try:
        while pending:
            entry = pending.pop()
            details = entry.lstat()
            if details.st_mtime >= cutoff or details.st_ctime >= cutoff:
                return False
            if stat.S_ISDIR(details.st_mode):
                pending.extend(entry.iterdir())
    except OSError:
        return False
    return True


def expand_roots(patterns, errors):
    roots = {}
    for pattern in patterns:
        absolute = os.path.abspath(os.path.expanduser(pattern))
        matches = glob.glob(absolute)
        if not matches:
            print(f"warning: root does not exist: {absolute}", file=errors)
        for match in matches:
            root = Path(match)
            if any(part.is_symlink() for part in (root, *root.parents)):
                print(f"warning: root crosses a symlink: {root}", file=errors)
            elif root.is_dir():
                roots[root] = root.lstat()
            else:
                print(f"warning: root is not a directory: {root}", file=errors)
    return {root: roots[root] for root in sorted(roots, key=lambda root: (len(root.parts), str(root)))}


def process_paths(process):
    """Unreadable references of non-dumpable processes are deliberately skipped."""
    for name in ("cwd", "root", "exe"):
        try:
            yield os.readlink(process / name)
        except OSError:
            pass
    try:
        descriptors = list((process / "fd").iterdir())
    except OSError:
        descriptors = []
    for descriptor in descriptors:
        try:
            yield os.readlink(descriptor)
        except OSError:
            pass
    try:
        with (process / "maps").open(encoding="utf-8", errors="surrogateescape") as mappings:
            for line in mappings:
                fields = line.rstrip("\n").split(None, 5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    yield fields[5]
    except OSError:
        pass


def is_in_use(item):
    path = str(item)
    command_path = re.compile(r"(?<![\w/.\-])" + re.escape(path) + r"(?=[/\s\x00]|$)")
    user = os.getuid()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            # /proc/<pid> ownership changes for non-dumpable processes. The
            # real UID in status remains readable, so do not use stat.st_uid.
            status = (process / "status").read_bytes()
            owner = next(line.split()[1] for line in status.splitlines()
                         if line.startswith(b"Uid:"))
            if int(owner) != user:
                continue
        except (OSError, StopIteration, ValueError):
            continue
        try:
            command = os.fsdecode((process / "cmdline").read_bytes())
            if command_path.search(command):
                return True
        except OSError:
            pass
        for reference in process_paths(process):
            reference = reference.removesuffix(" (deleted)")
            if reference == path or reference.startswith(path + "/"):
                return True
    return False


@contextmanager
def checked_directory(root, expected):
    """Open every path component without following links, then verify identity."""
    descriptor = os.open(root.anchor, DIRECTORY_OPEN_FLAGS)
    try:
        for component in root.parts[1:]:
            child = os.open(component, DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if not os.path.samestat(expected, os.fstat(descriptor)):
            raise OSError(f"root changed since expansion: {root}")
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def root_lock(root, expected):
    """Writers and cleaners flock the root directory itself, never a lock file."""
    with checked_directory(root, expected) as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor


def remove_item(path):
    if stat.S_ISDIR(path.lstat().st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def clean_roots(patterns, mode, listed, cutoff, output, errors):
    """Use an explicit cutoff internally; the CLI always keeps thirty days."""
    approved = None
    if mode == "delete-listed":
        try:
            with open(listed, encoding="utf-8") as review:
                approved = set()
                for number, line in enumerate(review, 1):
                    path = json.loads(line)
                    if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
                        raise ValueError(f"record {number} must be a JSON absolute-path string")
                    approved.add(path)
        except (OSError, ValueError) as error:
            print(f"error: {listed}: {error}", file=errors)
            return 1
    status = 0
    roots = expand_roots(patterns, errors)
    outer_roots = {root: next(outer for outer in roots if root.is_relative_to(outer))
                   for root in roots}
    if mode != "list":
        for outer in dict.fromkeys(outer_roots.values()):
            trash = outer / TRASH_NAME
            try:
                with checked_directory(outer, roots[outer]) as outer_descriptor, ExitStack() as handles:
                    try:
                        trash_descriptor = os.open(TRASH_NAME, DIRECTORY_OPEN_FLAGS,
                                                   dir_fd=outer_descriptor)
                    except FileNotFoundError:
                        continue
                    handles.callback(os.close, trash_descriptor)
                    for name in sorted(os.listdir(trash_descriptor)):
                        leftover = Path(f"/proc/self/fd/{trash_descriptor}") / name
                        try:
                            remove_item(leftover)
                        except OSError as error:
                            print(f"error: {trash / name}: {error}", file=errors)
                            status = 1
            except OSError as error:
                print(f"error: {trash}: {error}", file=errors)
                status = 1
    for root in roots:
        # A previously removed ancestor may have contained this nested root.
        if not os.path.lexists(root):
            continue
        outer = outer_roots[root]
        try:
            with checked_directory(root, roots[root]) as root_descriptor:
                for name in sorted(os.listdir(root_descriptor)):
                    item = root / name
                    if name == TRASH_NAME or (approved is not None and str(item) not in approved):
                        continue
                    if not is_expired(Path(f"/proc/self/fd/{root_descriptor}") / name, cutoff):
                        continue
                    if mode == "list":
                        if not is_in_use(item):
                            print(json.dumps(str(item), ensure_ascii=True), file=output)
                        continue
                    try:
                        # Keep the trash handle alive until recursive removal has
                        # finished outside the lock. No parent path is re-resolved.
                        with ExitStack() as handles:
                            with root_lock(outer, roots[outer]) as outer_descriptor, \
                                    checked_directory(root, roots[root]) as checked_descriptor:
                                entry = Path(f"/proc/self/fd/{checked_descriptor}") / name
                                if not is_expired(entry, cutoff) or is_in_use(item):
                                    continue
                                try:
                                    os.mkdir(TRASH_NAME, dir_fd=outer_descriptor)
                                except FileExistsError:
                                    pass
                                trash_descriptor = os.open(TRASH_NAME, DIRECTORY_OPEN_FLAGS,
                                                           dir_fd=outer_descriptor)
                                handles.callback(os.close, trash_descriptor)
                                destination = Path(f"/proc/self/fd/{trash_descriptor}") / uuid.uuid4().hex
                                while os.path.lexists(destination):
                                    destination = destination.parent / uuid.uuid4().hex
                                os.rename(name, destination.name, src_dir_fd=checked_descriptor,
                                          dst_dir_fd=trash_descriptor)
                            remove_item(destination)
                            print(json.dumps(str(item), ensure_ascii=True), file=output)
                    except OSError as error:
                        print(f"error: {item}: {error}", file=errors)
                        status = 1
        except OSError as error:
            print(f"error: {root}: {error}", file=errors)
            status = 1
    return status


def main(arguments):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True,
                        choices=("list", "delete", "delete-listed"))
    parser.add_argument("--listed")
    parser.add_argument("roots", nargs="+", help="root directories or quoted globs")
    options = parser.parse_args(arguments)
    if (options.mode == "delete-listed") != (options.listed is not None):
        parser.error("--listed is required only with --mode delete-listed")
    return clean_roots(options.roots, options.mode, options.listed,
                       time.time() - RETENTION_SECONDS, sys.stdout, sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
