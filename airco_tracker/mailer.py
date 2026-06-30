from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage

from .config import Config
from .models import Product


def build_message(config: Config, products: list[Product], *, test: bool = False) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config.email_from
    message["To"] = config.email_to
    message["Subject"] = (
        "[Airco tracker] 邮件测试成功"
        if test
        else f"🔥 {len(products)} 台便携空调恢复库存"
    )
    if test:
        message.set_content("Airco Tracker NL 邮件配置正常。")
        message.add_alternative("<p><strong>Airco Tracker NL</strong> 邮件配置正常。</p>", subtype="html")
        return message

    lines = ["检测到以下可配送到荷兰的便携空调：", ""]
    cards: list[str] = []
    for product in products:
        price = f"€{product.price_eur:,.2f}" if product.price_eur is not None else "价格未知"
        power = f" · {product.btu} BTU" if product.btu else ""
        delivery = product.delivery or "页面显示可购买"
        lines.extend([f"{product.site} — {product.name}", f"{price}{power} · {delivery}", product.url, ""])
        cards.append(
            "<li style='margin-bottom:18px'>"
            f"<strong>{html.escape(product.site)} — {html.escape(product.name)}</strong><br>"
            f"{html.escape(price + power)} · {html.escape(delivery)}<br>"
            f"<a href='{html.escape(product.url, quote=True)}'>立即查看并下单</a></li>"
        )
    lines.append("库存变化很快，请在购买前再次确认价格和配送日期。")
    message.set_content("\n".join(lines))
    message.add_alternative(
        "<h2>便携空调恢复库存</h2><ul>" + "".join(cards) + "</ul>"
        "<p>库存变化很快，请在购买前再次确认价格和配送日期。</p>",
        subtype="html",
    )
    return message


def send_message(config: Config, message: EmailMessage) -> None:
    config.validate_email()
    if config.email_backend == "azure_communication":
        _send_azure_communication(config, message)
        return
    _send_smtp(config, message)


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


def _send_azure_communication(config: Config, message: EmailMessage) -> None:
    try:
        from azure.communication.email import EmailClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise RuntimeError("Install the 'azure' extra to use Azure Communication Services") from exc
    client = EmailClient(config.acs_endpoint, DefaultAzureCredential())
    client.begin_send(_acs_payload(config, message)).result()


def _acs_payload(config: Config, message: EmailMessage) -> dict:
    plain = message.get_body(preferencelist=("plain",))
    html_body = message.get_body(preferencelist=("html",))
    content = {
        "subject": str(message["Subject"]),
        "plainText": plain.get_content() if plain else "",
    }
    if html_body:
        content["html"] = html_body.get_content()
    return {
        "senderAddress": config.email_from,
        "recipients": {"to": [{"address": config.email_to}]},
        "content": content,
    }
