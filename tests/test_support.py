"""Portable paths and expected input diagnostics shared by host test CLIs."""
from pathlib import Path
import shutil
import sys


class TestInputError(Exception):
    """An input or dependency is unavailable before a test can start."""


def project_path(path, root):
    """Resolve CLI paths against the checkout, independently of the caller's cwd."""
    path = Path(path)
    return (path if path.is_absolute() else root / path).resolve()


def source_label(path, root, external_label):
    """Report project paths or semantic labels, never workstation directory names."""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "external/" + external_label


def require_files(paths, hint):
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise TestInputError("Missing required input: " + ", ".join(missing) + ". " + hint)


def require_command(command, hint="Install the host test dependencies."):
    if not command or not shutil.which(command):
        raise TestInputError(f"Required command is unavailable: {command or '(not set)'}. {hint}")
    return command


def run_cli(main):
    try:
        main()
    except TestInputError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from None
