from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from utils.process_lock import SingleInstanceLockError, single_instance_lock


def test_single_instance_lock_blocks_second_process(tmp_path):
    lock_path = tmp_path / "process.lock"
    repo_root = Path(__file__).resolve().parents[1]
    child_code = f"""
from pathlib import Path
import sys
from utils.process_lock import single_instance_lock
from utils.process_lock import SingleInstanceLockError

try:
    with single_instance_lock(Path(r"{lock_path}"), label="pytest-process-lock"):
        sys.exit(0)
except SingleInstanceLockError:
    sys.exit(7)
"""
    with single_instance_lock(lock_path, label="pytest-process-lock"):
        child = subprocess.run(
            [sys.executable, "-c", child_code],
            cwd=str(repo_root),
            check=False,
        )
        assert child.returncode == 7
        assert lock_path.read_text(encoding="utf-8").strip() != "0"

    with single_instance_lock(lock_path, label="pytest-process-lock"):
        pass
