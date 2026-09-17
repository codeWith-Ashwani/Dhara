from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum

from dhara.alert_templates import RenderedAlert
from dhara.safety import SafetyControls

CAP_NAMESPACE = "urn:oasis:names:tc:emergency:cap:1.2"
ET.register_namespace("", CAP_NAMESPACE)


class DeliveryChannel(StrEnum):
    PUSH = "push"
    SMS = "sms"
    IVR = "ivr"


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    channel: DeliveryChannel
    status: str
    recipient: str
    message_sha256: str
    body: str
    provider_reference: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DeliveryBundle:
    message: RenderedAlert
    receipts: tuple[DeliveryReceipt, ...]
    cap_xml: str

    def to_dict(self) -> dict[str, object]:
        return {
            "message": self.message.to_dict(),
            "receipts": [item.to_dict() for item in self.receipts],
            "cap_xml": self.cap_xml,
        }


class SandboxDeliveryAdapter:
    """Records an intended delivery without contacting a real provider."""

    def __init__(self, channel: DeliveryChannel) -> None:
        self.channel = channel
        self.outbox: list[DeliveryReceipt] = []

    def send(self, alert_id: str, recipient: str, body: str) -> DeliveryReceipt:
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        receipt = DeliveryReceipt(
            channel=self.channel,
            status="sandbox_recorded",
            recipient=recipient,
            message_sha256=digest,
            body=body,
            provider_reference=f"sandbox:{self.channel.value}:{alert_id}",
        )
        self.outbox.append(receipt)
        return receipt


class CapV12Composer:
    def compose(
        self,
        *,
        alert_id: str,
        sender: str,
        sent_at: datetime,
        cell_id: str,
        area_description: str,
        message: RenderedAlert,
    ) -> str:
        if sent_at.tzinfo is None:
            raise ValueError("CAP sent_at must include a timezone")
        root = ET.Element(_cap("alert"))
        _child(root, "identifier", alert_id)
        _child(root, "sender", sender)
        _child(root, "sent", sent_at.astimezone(UTC).isoformat().replace("+00:00", "Z"))
        _child(root, "status", "Test")
        _child(root, "msgType", "Alert")
        _child(root, "scope", "Restricted")
        _child(root, "restriction", "D.H.A.R.A. shadow-mode sandbox only")

        info = ET.SubElement(root, _cap("info"))
        _child(info, "language", message.language)
        _child(info, "category", "Met")
        _child(info, "event", "Urban flooding")
        _child(info, "responseType", "Avoid")
        _child(info, "urgency", "Immediate")
        _child(info, "severity", "Severe")
        _child(info, "certainty", "Observed")
        event_code = ET.SubElement(info, _cap("eventCode"))
        _child(event_code, "valueName", "D.H.A.R.A. hazard code")
        _child(event_code, "value", "FL")
        _child(info, "headline", f"D.H.A.R.A. {message.tier.name.title()} flood alert")
        _child(info, "description", message.body)
        _child(info, "instruction", message.body)
        area = ET.SubElement(info, _cap("area"))
        _child(area, "areaDesc", area_description)
        geocode = ET.SubElement(area, _cap("geocode"))
        _child(geocode, "valueName", "H3-R9")
        _child(geocode, "value", cell_id)
        return ET.tostring(root, encoding="unicode", xml_declaration=True)


class SandboxDeliveryOrchestrator:
    def __init__(
        self,
        adapters: tuple[SandboxDeliveryAdapter, ...] | None = None,
        cap_composer: CapV12Composer | None = None,
        safety: SafetyControls | None = None,
    ) -> None:
        self.adapters = adapters or tuple(
            SandboxDeliveryAdapter(channel) for channel in DeliveryChannel
        )
        self.cap_composer = cap_composer or CapV12Composer()
        self.safety = safety or SafetyControls.from_environment()

    def send_all(
        self,
        *,
        alert_id: str,
        recipient: str,
        sender: str,
        sent_at: datetime,
        cell_id: str,
        area_description: str,
        message: RenderedAlert,
    ) -> DeliveryBundle:
        self.safety.assert_sandbox_delivery()
        if message.approval_scope != "sandbox_only":
            raise ValueError("only sandbox-scoped templates may use sandbox delivery")
        receipts: list[DeliveryReceipt] = []
        for adapter in self.adapters:
            try:
                receipt = adapter.send(alert_id, recipient, message.body)
            except Exception:
                receipt = DeliveryReceipt(
                    channel=adapter.channel,
                    status="sandbox_failed",
                    recipient=recipient,
                    message_sha256=hashlib.sha256(message.body.encode("utf-8")).hexdigest(),
                    body=message.body,
                    provider_reference=f"sandbox:{adapter.channel.value}:{alert_id}:failed",
                )
            receipts.append(receipt)
        cap_xml = self.cap_composer.compose(
            alert_id=alert_id,
            sender=sender,
            sent_at=sent_at,
            cell_id=cell_id,
            area_description=area_description,
            message=message,
        )
        return DeliveryBundle(message=message, receipts=tuple(receipts), cap_xml=cap_xml)


def _cap(name: str) -> str:
    return f"{{{CAP_NAMESPACE}}}{name}"


def _child(parent: ET.Element, name: str, value: str) -> ET.Element:
    child = ET.SubElement(parent, _cap(name))
    child.text = value
    return child
