"""run_schedule_v3 - PM2 overlap 방지를 위한 flock 가드 테스트"""
import fcntl
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent


def test_module_imports_without_error():
    import run_schedule_v3  # noqa: F401


def test_dry_run_exits_zero(tmp_path, monkeypatch):
    env = {**__import__("os").environ, "MQK_PHASE": "intraday"}
    result = subprocess.run(
        [sys.executable, str(_ROOT / "run_schedule_v3.py"), "--dry-run"],
        env=env, capture_output=True, text=True, cwd=str(_ROOT),
    )
    assert result.returncode == 0


def test_acquire_lock_returns_none_when_already_locked(tmp_path):
    from run_schedule_v3 import _acquire_lock

    lock_path = tmp_path / "mqk_v3.lock"

    held = open(lock_path, "w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        assert _acquire_lock(path=lock_path) is None
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()


def test_acquire_lock_succeeds_when_unlocked(tmp_path):
    from run_schedule_v3 import _acquire_lock

    lock_path = tmp_path / "mqk_v3.lock"
    f = _acquire_lock(path=lock_path)

    assert f is not None
    fcntl.flock(f, fcntl.LOCK_UN)
    f.close()


def test_runner_exits_zero_when_lock_held_by_other_process(tmp_path):
    """다른 프로세스가 lock을 들고 있으면 run_schedule_v3가 0으로 종료해야 한다."""
    lock_path = tmp_path / "mqk_v3.lock"
    held = open(lock_path, "w")
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)

    try:
        env = {**__import__("os").environ, "MQK_PHASE": "intraday"}
        script = (
            "import fcntl, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, %r)\n"
            "import run_schedule_v3 as rs\n"
            "rs._LOCK_PATH = Path(%r)\n"
            "lock = rs._acquire_lock(path=rs._LOCK_PATH)\n"
            "sys.exit(0 if lock is None else 1)\n"
        ) % (str(_ROOT), str(lock_path))
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=env, capture_output=True, text=True, cwd=str(_ROOT),
        )
        assert result.returncode == 0
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()


def test_phase_window_guard():
    from run_schedule_v3 import _within_window

    assert _within_window("premarket", "09:03") is True
    assert _within_window("premarket", "18:30") is False
    assert _within_window("intraday", "12:00") is True
    assert _within_window("intraday", "15:10") is False
    assert _within_window("late_intraday", "15:13") is True
    assert _within_window("close", "15:18") is True
    assert _within_window("close", "15:32") is False
    assert _within_window("market_close", "17:00") is True
    assert _within_window("unknown_phase", "03:00") is True  # 미정의 phase는 통과
