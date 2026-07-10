from __future__ import annotations

import html
import hashlib
import json
import smtplib
import ssl
from email.utils import format_datetime, getaddresses
from urllib.parse import quote
from datetime import datetime, timezone
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache

from .azure_auth import default_azure_credential
from .config import Config
from .i18n import translate
from .models import Product


def build_message(
    config: Config,
    products: list[Product],
    *,
    test: bool = False,
    unsubscribe_token: str | None = None,
) -> EmailMessage:
    lang = config.email_lang
    message = EmailMessage()
    message["From"] = config.email_from
    message["To"] = config.email_to
    reply_to = getattr(config, "email_reply_to", "").strip()
    if reply_to:
        message["Reply-To"] = reply_to
    if test:
        message["Subject"] = translate(lang, "subject_test")
        message.set_content(translate(lang, "test_body"))
        message.add_alternative(translate(lang, "test_body_html"), subtype="html")
        return message

    message["Subject"] = translate(lang, "subject_alert", count=len(products))
    price_unknown = translate(lang, "price_unknown")
    view_link = translate(lang, "view_link")
    delivery_fallback = translate(lang, "delivery_in_stock")

    lines = [translate(lang, "body_intro"), ""]
    cards: list[str] = []
    for product in products:
        price = f"€{product.price_eur:,.2f}" if product.price_eur is not None else price_unknown
        power = f" · {product.btu} BTU" if product.btu else ""
        delivery = product.delivery or delivery_fallback
        lines.extend([f"{product.site} — {product.name}", f"{price}{power} · {delivery}", product.url, ""])
        cards.append(
            "<li style='margin-bottom:18px'>"
            f"<strong>{html.escape(product.site)} — {html.escape(product.name)}</strong><br>"
            f"{html.escape(price + power)} · {html.escape(delivery)}<br>"
            f"<a href='{html.escape(product.url, quote=True)}'>{html.escape(view_link)}</a></li>"
        )
    footer = translate(lang, "body_footer")
    lines.append(footer)
    unsubscribe_url = ""
    app_base_url = getattr(config, "app_base_url", "").strip().rstrip("/")
    if unsubscribe_token and app_base_url:
        unsubscribe_url = f"{app_base_url}/unsubscribe?token={quote(unsubscribe_token, safe='')}"
        lines.extend(
            [
                "",
                translate(lang, "unsubscribe_text"),
                unsubscribe_url,
            ]
        )
    message.set_content("\n".join(lines))
    message.add_alternative(
        f"<h2>{html.escape(translate(lang, 'html_title'))}</h2><ul>"
        + "".join(cards)
        + "</ul>"
        + f"<p>{html.escape(footer)}</p>"
        + (
            "<p style='color:#607789;font-size:13px'>"
            f"{html.escape(translate(lang, 'unsubscribe_text'))} "
            f"<a href='{html.escape(unsubscribe_url, quote=True)}'>"
            f"{html.escape(translate(lang, 'unsubscribe_link'))}</a></p>"
            if unsubscribe_url
            else ""
        ),
        subtype="html",
    )
    if unsubscribe_url:
        api_url = f"{app_base_url}/api/alerts/unsubscribe?token={quote(unsubscribe_token, safe='')}"
        message["List-Unsubscribe"] = f"<{api_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    return message


@dataclass(frozen=True)
class SendResult:
    operation_id: str | None
    status: str


class PermanentEmailError(RuntimeError):
    """The provider reached a final non-success state for this operation."""


def message_fingerprint(
    config: Config,
    message: EmailMessage,
    *,
    delivery_id: str,
) -> str:
    """Hash the exact ACS payload without persisting any recipient PII.

    The delivery ID acts as a per-delivery salt, so fingerprints cannot be
    correlated across stock events. A retry may reuse its ACS operation ID
    only while sender, recipient, subject, and both bodies remain identical.
    """
    canonical = json.dumps(
        _acs_payload(config, message),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{delivery_id}\0{canonical}".encode("utf-8")).hexdigest()


def send_message(
    config: Config,
    message: EmailMessage,
    *,
    operation_id: str | None = None,
    repeatability_first_sent: str | None = None,
) -> SendResult:
    config.validate_email()
    if config.email_backend == "azure_communication":
        return _send_azure_communication(
            config,
            message,
            operation_id=operation_id,
            repeatability_first_sent=repeatability_first_sent,
        )
    _send_smtp(config, message)
    return SendResult(operation_id=None, status="sent")


def _send_smtp(config: Config, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    if config.smtp_security == "ssl":
        with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, context=context, timeout=30) as smtp:
            _login_and_send(smtp, config, message)
    else:
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
            if config.smtp_security == "starttls":
                smtp.starttls(context=context)
            _login_and_send(smtp, config, message)


def _login_and_send(smtp: smtplib.SMTP, config: Config, message: EmailMessage) -> None:
    if config.smtp_username:
        smtp.login(config.smtp_username, config.smtp_password)
    smtp.send_message(message)


def _send_azure_communication(
    config: Config,
    message: EmailMessage,
    *,
    operation_id: str | None,
    repeatability_first_sent: str | None,
) -> SendResult:
    try:
        from azure.communication.email import EmailClient
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to use Azure Communication Services") from exc
    client = _cached_email_client(config.acs_endpoint)
    kwargs = {"operation_id": operation_id} if operation_id else {}
    if operation_id and repeatability_first_sent:
        kwargs["headers"] = {
            "Repeatability-Request-ID": operation_id,
            "Repeatability-First-Sent": _http_datetime(repeatability_first_sent),
        }
    # Keep the delivery-ledger lease longer than the client-side wait. If ACS
    # accepted the operation but the poll timed out, the worker can retry with
    # the same operation/repeatability IDs without a concurrent second sender.
    result = client.begin_send(_acs_payload(config, message), **kwargs).result(timeout=180)
    if isinstance(result, dict):
        status = str(result.get("status") or "").strip()
        if status.lower() not in {"succeeded", "accepted"}:
            error = result.get("error")
            error_code = str(error.get("code") or "unknown") if isinstance(error, dict) else "unknown"
            raise PermanentEmailError(f"ACS email operation failed ({error_code[:80]})")
        return SendResult(
            operation_id=str(result.get("id") or operation_id or "") or None,
            status=status,
        )
    return SendResult(operation_id=operation_id, status="accepted")


@lru_cache(maxsize=4)
def _cached_email_client(endpoint: str):
    try:
        from azure.communication.email import EmailClient
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to use Azure Communication Services") from exc
    return EmailClient(endpoint, default_azure_credential())


def _http_datetime(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return format_datetime(parsed.astimezone(timezone.utc), usegmt=True)


def _acs_payload(config: Config, message: EmailMessage) -> dict:
    plain = message.get_body(preferencelist=("plain",))
    html_body = message.get_body(preferencelist=("html",))
    content = {
        "subject": str(message["Subject"]),
        "plainText": plain.get_content() if plain else "",
    }
    if html_body:
        content["html"] = html_body.get_content()
    payload = {
        "senderAddress": config.email_from,
        "recipients": {"to": [{"address": config.email_to}]},
        "content": content,
        "userEngagementTrackingDisabled": True,
    }
    reply_to = [address for _name, address in getaddresses(message.get_all("Reply-To", [])) if address]
    if reply_to:
        payload["replyTo"] = [{"address": address} for address in reply_to]
    custom_headers = {
        name: str(message[name])
        for name in ("List-Unsubscribe", "List-Unsubscribe-Post")
        if message[name]
    }
    if custom_headers:
        payload["headers"] = custom_headers
    return payload
