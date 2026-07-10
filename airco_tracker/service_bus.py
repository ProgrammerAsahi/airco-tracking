from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Callable, Iterable, Iterator

from .azure_auth import default_azure_credential
from .config import Config


LOG = logging.getLogger(__name__)


def fully_qualified_namespace(value: str) -> str:
    namespace = value.strip()
    if not namespace:
        raise ValueError("SERVICE_BUS_NAMESPACE is required")
    if "." not in namespace:
        namespace += ".servicebus.windows.net"
    return namespace


@contextmanager
def service_bus_client(config: Config):
    try:
        from azure.servicebus import ServiceBusClient
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to use Azure Service Bus") from exc
    client = ServiceBusClient(
        fully_qualified_namespace(config.service_bus_namespace),
        credential=default_azure_credential(),
    )
    try:
        yield client
    finally:
        client.close()


def send_json_messages(
    sender,
    messages: Iterable[tuple[str, str, str] | tuple[str, str, str, str]],
) -> int:
    """Send JSON payloads in size-aware Service Bus batches.

    Each tuple is ``(message_id, subject, payload)`` or includes a fourth,
    deterministic ``partition_key``. Business messages remain independent;
    batching only reduces network round trips. A partition-key change flushes
    the current batch because Service Bus partitioned entities reject one batch
    containing multiple partition keys.
    """
    try:
        from azure.servicebus import ServiceBusMessage
        from azure.servicebus.exceptions import MessageSizeExceededError
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to use Azure Service Bus") from exc

    sent = 0
    batch = sender.create_message_batch()
    batch_count = 0
    batch_partition_key: str | None = None
    for item in messages:
        if len(item) == 3:
            message_id, subject, payload = item
            partition_key = None
        else:
            message_id, subject, payload, partition_key = item
        if batch_count and partition_key != batch_partition_key:
            sender.send_messages(batch)
            sent += batch_count
            batch = sender.create_message_batch()
            batch_count = 0
        batch_partition_key = partition_key
        message = ServiceBusMessage(
            payload,
            message_id=message_id,
            subject=subject,
            content_type="application/json",
            partition_key=partition_key,
        )
        try:
            batch.add_message(message)
            batch_count += 1
        except MessageSizeExceededError:
            if batch_count == 0:
                raise ValueError(f"Service Bus message {message_id} exceeds the entity limit")
            sender.send_messages(batch)
            sent += batch_count
            batch = sender.create_message_batch()
            batch_partition_key = partition_key
            batch.add_message(message)
            batch_count = 1
    if batch_count:
        sender.send_messages(batch)
        sent += batch_count
    return sent


def message_body(message) -> bytes:
    chunks: list[bytes] = []
    for chunk in message.body:
        if isinstance(chunk, bytes):
            chunks.append(chunk)
        elif isinstance(chunk, bytearray):
            chunks.append(bytes(chunk))
        else:
            chunks.append(str(chunk).encode("utf-8"))
    return b"".join(chunks)


class PermanentMessageError(RuntimeError):
    pass


def process_receiver(
    receiver,
    handler: Callable[[bytes, object], None],
    *,
    max_messages: int,
    max_wait_time: int,
) -> int:
    messages = receiver.receive_messages(
        max_message_count=max_messages,
        max_wait_time=max_wait_time,
    )
    processed = 0
    for message in messages:
        try:
            handler(message_body(message), message)
        except PermanentMessageError as exc:
            LOG.error("Dead-lettering invalid/permanent message: %s", exc)
            receiver.dead_letter_message(
                message,
                reason=type(exc).__name__[:128],
                error_description=str(exc)[:1024],
            )
        except Exception:
            LOG.exception("Message processing failed; returning it to Service Bus for retry")
            receiver.abandon_message(message)
        else:
            receiver.complete_message(message)
        processed += 1
    return processed
