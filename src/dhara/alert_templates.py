from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from importlib.resources import files
from string import Formatter

from dhara.community import DepthOrdinal
from dhara.fusion import AlertTier

_REQUIRED_SLOTS = {"place", "road", "depth_class", "valid_until", "confidence_word"}
_DEPTH_WORDS = {
    "en-IN": {
        DepthOrdinal.ANKLE: "ankle",
        DepthOrdinal.KNEE: "knee",
        DepthOrdinal.WAIST: "waist",
        DepthOrdinal.ABOVE_WAIST: "above-waist",
    },
    "hi-IN": {
        DepthOrdinal.ANKLE: "टखने",
        DepthOrdinal.KNEE: "घुटने",
        DepthOrdinal.WAIST: "कमर",
        DepthOrdinal.ABOVE_WAIST: "कमर से ऊपर",
    },
    "mr-IN": {
        DepthOrdinal.ANKLE: "घोटा",
        DepthOrdinal.KNEE: "गुडघा",
        DepthOrdinal.WAIST: "कंबर",
        DepthOrdinal.ABOVE_WAIST: "कंबरेच्या वर",
    },
}
_CONFIDENCE_WORDS = {
    "en-IN": {AlertTier.WARNING: "high"},
    "hi-IN": {AlertTier.WARNING: "उच्च"},
    "mr-IN": {AlertTier.WARNING: "उच्च"},
}


class TemplateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AlertTemplate:
    template_id: str
    language: str
    tier: AlertTier
    channels: tuple[str, ...]
    approval_scope: str
    human_reviewed: bool
    dlt_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class RenderedAlert:
    template_id: str
    language: str
    tier: AlertTier
    approval_scope: str
    human_reviewed: bool
    dlt_id: str | None
    body: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["tier"] = self.tier.name.lower()
        return result


class AlertTemplateCatalog:
    def __init__(self, templates: tuple[AlertTemplate, ...] | None = None) -> None:
        loaded = templates or _load_templates()
        self._templates = {(item.template_id, item.language): item for item in loaded}

    def languages(self, template_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(language for item_id, language in self._templates if item_id == template_id)
        )

    def render(
        self,
        *,
        template_id: str,
        language: str,
        place: str,
        road: str,
        depth: DepthOrdinal,
        valid_until: str,
    ) -> RenderedAlert:
        try:
            template = self._templates[(template_id, language)]
            depth_word = _DEPTH_WORDS[language][depth]
            confidence_word = _CONFIDENCE_WORDS[language][template.tier]
        except KeyError as exc:
            raise TemplateError("unsupported template, language, depth, or tier") from exc
        fields = {
            field_name
            for _, field_name, _, _ in Formatter().parse(template.text)
            if field_name is not None
        }
        if fields != _REQUIRED_SLOTS:
            raise TemplateError("template slot contract does not match the controlled vocabulary")
        slots = {
            "place": _safe_slot(place, "place"),
            "road": _safe_slot(road, "road"),
            "depth_class": depth_word,
            "valid_until": _safe_slot(valid_until, "valid_until"),
            "confidence_word": confidence_word,
        }
        return RenderedAlert(
            template_id=template.template_id,
            language=template.language,
            tier=template.tier,
            approval_scope=template.approval_scope,
            human_reviewed=template.human_reviewed,
            dlt_id=template.dlt_id,
            body=template.text.format_map(slots),
        )


def _load_templates() -> tuple[AlertTemplate, ...]:
    source = files("dhara").joinpath("templates/alerts.json")
    records = json.loads(source.read_text(encoding="utf-8"))
    return tuple(
        AlertTemplate(
            template_id=item["template_id"],
            language=item["language"],
            tier=AlertTier[item["tier"].upper()],
            channels=tuple(item["channels"]),
            approval_scope=item["approval_scope"],
            human_reviewed=item["human_reviewed"],
            dlt_id=item["dlt_id"],
            text=item["text"],
        )
        for item in records
    )


def _safe_slot(value: str, name: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > 120 or "{" in cleaned or "}" in cleaned:
        raise TemplateError(f"invalid {name} slot")
    return cleaned
