"""Copy the review target into an isolated temporary Git repository."""
from pathlib import Path
import shutil
import subprocess


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixture_repo"


def create_fixture_repo(destination):
    """Create an indexed fixture repository at a new destination path."""
    root = Path(destination).resolve()
    shutil.copytree(FIXTURE_ROOT, root)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    return root
