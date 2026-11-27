"""Every external command of the kit goes through `run`, so that the tests can replace it in one place."""
import subprocess


def run(cmd: list[str], timeout: int = 120, **kw) -> tuple[int, str]:
    """Run a command; (exit code, output). -1 if it could not run."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, **kw)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)
