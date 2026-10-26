"""The queue: Google Cloud Pub/Sub. Messages carry ONE thing, a run ID, and nothing else.

The CV and job ad stay in Postgres. A message is just a ticket saying "run 1234 is
waiting", so it's tiny and never holds personal data.

Create the topic and subscription on your laptop (the emulator must be running):

    uv run python -m core.queue
"""

import json
import os
from functools import lru_cache
from uuid import UUID

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1

from core.config import get_settings

# How long a worker has to finish a run before Pub/Sub gives the message to someone else.
# 600 seconds is the longest Pub/Sub allows; a run takes about a minute.
ACK_DEADLINE_SECONDS = 600


def _use_emulator_if_configured() -> None:
    # The Google library reads PUBSUB_EMULATOR_HOST from the environment, not from .env,
    # so copy it across. With it set, no Google login is needed and nothing costs money.
    host = get_settings().pubsub_emulator_host
    if host:
        os.environ["PUBSUB_EMULATOR_HOST"] = host


@lru_cache  # one client for the whole program: creating one is slow
def publisher() -> pubsub_v1.PublisherClient:
    _use_emulator_if_configured()
    return pubsub_v1.PublisherClient()


@lru_cache
def subscriber() -> pubsub_v1.SubscriberClient:
    _use_emulator_if_configured()
    return pubsub_v1.SubscriberClient()


def topic_path() -> str:
    settings = get_settings()
    return publisher().topic_path(settings.gcp_project, settings.runs_topic)


def subscription_path() -> str:
    settings = get_settings()
    return subscriber().subscription_path(settings.gcp_project, settings.runs_subscription)


def publish_run(run_id: UUID) -> None:
    """Put a run ID on the queue. Waits up to 10 seconds for Pub/Sub to confirm it has it."""
    data = json.dumps({"run_id": str(run_id)}).encode()
    publisher().publish(topic_path(), data).result(timeout=10)


def setup() -> None:
    """Create the topic and subscription if they don't exist yet. Safe to run again.

    On Google Cloud you'd create these once with gcloud or Terraform instead.
    """
    try:
        publisher().create_topic(request={"name": topic_path()})
    except AlreadyExists:
        pass
    try:
        subscriber().create_subscription(
            request={
                "name": subscription_path(),
                "topic": topic_path(),
                "ack_deadline_seconds": ACK_DEADLINE_SECONDS,
            }
        )
    except AlreadyExists:
        pass


if __name__ == "__main__":
    setup()
    print(f"Queue ready: topic {topic_path()}, subscription {subscription_path()}")
