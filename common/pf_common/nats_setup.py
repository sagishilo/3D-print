"""Create or update the JetStream streams defined in subjects.STREAMS (idempotent)."""
import logging

from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError

from .subjects import STREAMS

log = logging.getLogger("pf.nats")


async def ensure_streams(js) -> None:
    for name, spec in STREAMS.items():
        cfg = StreamConfig(
            name=name,
            subjects=spec["subjects"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=spec["max_age_s"],  # seconds; nats-py converts to ns
            duplicate_window=120,      # dedupe by Nats-Msg-Id for 2 minutes
        )
        try:
            await js.stream_info(name)
            await js.update_stream(cfg)
            log.info("stream %s updated", name)
        except NotFoundError:
            await js.add_stream(cfg)
            log.info("stream %s created", name)
