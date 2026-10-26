"""The real queue, against the Pub/Sub emulator. Skipped if the emulator isn't running."""

import json
import socket
import uuid

import pytest

from core import queue
from core.config import get_settings


@pytest.fixture
def emulator(monkeypatch):
    host = get_settings().pubsub_emulator_host
    if not host:
        pytest.skip("PUBSUB_EMULATOR_HOST isn't set in .env")
    name, port = host.split(":")
    try:
        socket.create_connection((name, int(port)), timeout=1).close()
    except OSError:
        pytest.skip("Pub/Sub emulator isn't running: docker compose up -d")

    # A fresh topic and subscription for each test, so tests never touch the real queue.
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("RUNS_TOPIC", f"test-runs-{suffix}")
    monkeypatch.setenv("RUNS_SUBSCRIPTION", f"test-workers-{suffix}")
    get_settings.cache_clear()
    queue.setup()
    yield
    queue.subscriber().delete_subscription(request={"subscription": queue.subscription_path()})
    queue.publisher().delete_topic(request={"topic": queue.topic_path()})


def pull_one():
    for _ in range(10):  # the emulator can need a moment before a new message shows up
        response = queue.subscriber().pull(
            request={"subscription": queue.subscription_path(), "max_messages": 1}, timeout=5
        )
        if response.received_messages:
            return response.received_messages[0]
    pytest.fail("no message arrived")


def test_a_published_run_id_can_be_pulled_and_acked(emulator):
    run_id = uuid.uuid4()
    queue.publish_run(run_id)

    received = pull_one()
    assert json.loads(received.message.data) == {"run_id": str(run_id)}  # only the ID
    queue.subscriber().acknowledge(
        request={"subscription": queue.subscription_path(), "ack_ids": [received.ack_id]}
    )


def test_setup_is_safe_to_run_again(emulator):
    queue.setup()  # the topic and subscription already exist: no error
