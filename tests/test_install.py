"""install.sh, run against stand-ins for curl and uv: what it runs, and what it says when it can't."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script")


def stub(folder: Path, name: str, script: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + script)
    path.chmod(0o755)


def run(tmp_path: Path, source: str | None = None, **stubs: str) -> subprocess.CompletedProcess:
    """install.sh, from the repo's folder, with a PATH of only the basic tools it
    needs plus the stand-ins given, and USDASH_SOURCE if `source`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("uname", "mktemp", "rm", "sh"):
        if tool not in stubs:
            (bin_dir / tool).symlink_to(shutil.which(tool))
    for name, script in stubs.items():
        stub(bin_dir, name, script)
    env = {"HOME": str(tmp_path / "home"), "PATH": str(bin_dir), **({"USDASH_SOURCE": source} if source else {})}
    return subprocess.run([shutil.which("sh"), str(ROOT / "install.sh")], env=env, capture_output=True, text=True,
                          cwd=ROOT)


def test_a_failed_download_says_so(tmp_path):
    result = run(tmp_path, curl='echo "curl: (6) Could not resolve host: astral.sh" >&2\nexit 6\n')
    assert result.returncode == 1
    assert "Could not resolve host" in result.stderr
    assert "usdash: couldn't download uv's installer" in result.stderr and "not found" not in result.stderr


def test_an_installer_that_installs_nothing_says_so(tmp_path):
    # curl -LsSf <url> -o <file>: writes an installer that does nothing.
    result = run(tmp_path, curl='while [ "$1" != "-o" ]; do shift; done\necho "exit 0" > "$2"\n')
    assert result.returncode == 1
    assert "usdash: uv isn't on the PATH after installing it" in result.stderr


def test_it_installs_usdash_from_github_without_git(tmp_path):
    log = tmp_path / "uv.log"
    result = run(tmp_path, uv=f'echo "$*" >> "{log}"\n')
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines() == [
        "tool install --force --reinstall-package usdash "
        "usdash @ https://github.com/pdudotdev/usdash/archive/refs/heads/master.tar.gz",
        "tool update-shell",
    ]
    assert "usdash installed" in result.stdout


def test_usdash_source_installs_from_elsewhere(tmp_path):
    log = tmp_path / "uv.log"
    result = run(tmp_path, source=".", uv=f'echo "$*" >> "{log}"\n')  # CI installs from its checkout
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[0] == "tool install --force --reinstall-package usdash ."


def test_another_system_is_turned_away(tmp_path):
    log = tmp_path / "uv.log"
    result = run(tmp_path, uname="echo FreeBSD\n", uv=f'echo "$*" >> "{log}"\n')
    assert result.returncode == 1 and "this installer is for macOS and Linux" in result.stderr
    assert not log.exists()  # nothing installed
