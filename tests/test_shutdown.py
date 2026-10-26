"""The real worker program stops cleanly on SIGTERM, the way Kubernetes stops it.

Needs Postgres and the Pub/Sub emulator; skipped without them.
"""

import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from core.config import get_settings

ROOT = Path(__file__).resolve().parents[1]


def emulator_running() -> bool:
    host = get_settings().pubsub_emulator_host
    if not host:
        return False
    name, port = host.split(":")
    try:
        socket.create_connection((name, int(port)), timeout=1).close()
        return True
    except OSError:
        return False


def test_the_worker_exits_cleanly_on_sigterm(database_url):
    if not emulator_running():
        pytest.skip("Pub/Sub emulator isn't running: docker compose up -d")

    suffix = uuid.uuid4().hex[:8]
    env = {
        **os.environ,
        "RUNS_TOPIC": f"test-runs-{suffix}",  # its own empty queue: it must not take
        "RUNS_SUBSCRIPTION": f"test-workers-{suffix}",  # real runs off your real queue
        "PUBSUB_EMULATOR_HOST": get_settings().pubsub_emulator_host,
    }
    worker = subprocess.Popen(
        [sys.executable, "-m", "worker.main"], cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        # Wait until it's ready and waiting for messages.
        started = time.monotonic()
        output = ""
        while "worker started" not in output:
            line = worker.stdout.readline()
            assert line, f"the worker exited early:\n{output}"
            output += line
            assert time.monotonic() - started < 30, "the worker took too long to start"

        worker.send_signal(signal.SIGTERM)  # what Kubernetes sends before a deploy
        rest, _ = worker.communicate(timeout=25)
        output += rest
    finally:
        if worker.poll() is None:
            worker.kill()

    assert worker.returncode == 0, output  # 0 = a clean exit, not a crash
    assert "SIGTERM received" in output
    assert "worker stopped cleanly" in output
