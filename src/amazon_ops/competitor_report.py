"""Evidence-bounded competitor report generation and deterministic rendering."""

from __future__ import annotations

import re
from typing import Any

from .models import CompetitorAdvertisingReport, CompetitorReportSection


SECTION_TITLES = {
    "traffic_keyword_lookup": "01｜模块：查流量词",
    "traffic_keyword_reverse_lookup": "02｜模块：反查流量词",
    "multi_variant_organic_position": "03｜模块：查多变体自然位",
    "recommendation_placement": "04｜模块：查推荐专栏",
}
_PRIVATE_COMPETITOR_METRIC = re.compile(
    r"竞品[^\n。！？]{0,30}(?:花费|竞价|ACOS|ROAS|订单|CVR)[^\n。！？]{0,20}\d",
    re.IGNORECASE,
)
_PRIVATE_METRIC_UNAVAILABLE = re.compile(
    r"不可得|无法获取|未取得|未提供|不提供|不作|不做|不推断|不估算|缺失",
    re.IGNORECASE,
)
_UNAVAILABLE_MODULE_TABLES = {
    "traffic_keyword_lookup": "\n".join(
        [
            "| 对象 | 父 ASIN | 自然流量 | 广告流量 | 广告流量分布 |",
            "| --- | --- | --- | --- | --- |",
            "",
            "| 对象 | 父 ASIN | 变体 ASIN | 总流量占比 | 自然流量占比 | 广告流量占比 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    ),
    "traffic_keyword_reverse_lookup": "\n".join(
        [
            "| 对象 | 父 ASIN | 流量词 | 全部流量占比 | 自然流量占比 | 广告流量占比 | 相比上升期变化（仅自有） |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    ),
    "multi_variant_organic_position": "\n".join(
        [
            "| 对象 | 父 ASIN | 关键词 | 给 Listing 的自然流量 | 自然流量占比 | 多自然位额外自然流量 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    ),
    "recommendation_placement": "\n".join(
        [
            "| 对象 | 父 ASIN | 周期 | 推荐专栏名称 | 流量占比 | 广告活动数量 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    ),
}


def validate_competitor_report(
    report: CompetitorAdvertisingReport, state: dict[str, Any]
) -> CompetitorAdvertisingReport:
    allowed = _evidence_ids(state)
    supplied = {section.key: section for section in report.sections if section.key in SECTION_TITLES}
    sections: list[CompetitorReportSection] = []
    for key in SECTION_TITLES:
        section = supplied.get(key)
        if section is None:
            sections.append(
                CompetitorReportSection(
                    key=key,
                    status="unavailable",
                    content="本期未生成该章节所需的可验证数据。",
                    missing_reasons=["REPORT_SECTION_NOT_GENERATED"],
                )
            )
            continue
        refs = [item for item in section.evidence_refs if item in allowed]
        if _asserts_private_competitor_metric(section.content):
            sections.append(
                CompetitorReportSection(
                    key=key,
                    status="partial",
                    content="竞品私有花费、竞价、ACOS、ROAS、订单和 CVR 不可得，本章不作相关数值判断。",
                    missing_reasons=["COMPETITOR_PRIVATE_METRIC_NOT_AVAILABLE"],
                )
            )
            continue
        if section.status == "available" and not refs:
            section = section.model_copy(
                update={
                    "status": "partial",
                    "missing_reasons": [*section.missing_reasons, "EVIDENCE_REFERENCE_MISSING"],
                }
            )
        sections.append(section.model_copy(update={"evidence_refs": refs}))
    findings = [
        item.model_copy(update={"evidence_refs": [ref for ref in item.evidence_refs if ref in allowed]})
        for item in report.confirmed_findings
        if item.evidence_refs and set(item.evidence_refs).issubset(allowed)
    ]
    hypotheses = [
        item.model_copy(update={"evidence_refs": [ref for ref in item.evidence_refs if ref in allowed]})
        for item in report.open_hypotheses
        if item.evidence_refs and set(item.evidence_refs).issubset(allowed)
    ]
    return report.model_copy(
        update={"sections": sections, "confirmed_findings": findings, "open_hypotheses": hypotheses}
    )


def validate_competitor_report_section(
    section: CompetitorReportSection,
    section_key: str,
    state: dict[str, Any],
) -> CompetitorReportSection:
    """Validate one generated section with the same policy as a full report."""

    if section.key != section_key or section_key not in SECTION_TITLES:
        raise ValueError("REPORT_SECTION_KEY_MISMATCH")
    report = validate_competitor_report(
        CompetitorAdvertisingReport(title="section-validation", sections=[section]), state
    )
    return next(item for item in report.sections if item.key == section_key)


def competitor_report_title(state: dict[str, Any]) -> str:
    scope = state.get("scope") if isinstance(state.get("scope"), dict) else {}
    own = scope.get("own_asin") or "本品"
    competitors = scope.get("competitor_asins") or []
    competitor = "、".join(str(item) for item in competitors) or "竞品"
    return f"美国站竞品广告对标报告：{own} vs {competitor}"


def render_competitor_report_section(section: CompetitorReportSection) -> str:
    content = section.content
    if section.status == "unavailable":
        content = _with_unavailable_module_table(section.key, content)
    lines = [
        f"## {SECTION_TITLES[section.key]}",
        "",
        content,
    ]
    return "\n\n" + "\n".join(lines)


def _with_unavailable_module_table(section_key: str, content: str) -> str:
    """Keep each requested module table visible without inventing values."""
    table = _UNAVAILABLE_MODULE_TABLES.get(section_key)
    if table is None or "|" in content:
        return content
    return f"{content}\n\n{table}"


def render_competitor_report(report: CompetitorAdvertisingReport) -> str:
    rendered = f"# {report.title}" + "".join(
        render_competitor_report_section(section) for section in report.sections
    )
    return rendered


def _evidence_ids(state: dict[str, Any]) -> set[str]:
    found: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            evidence_id = value.get("evidence_id")
            if isinstance(evidence_id, str):
                found.add(evidence_id)
            evidence_ids = value.get("evidence_ids")
            if isinstance(evidence_ids, list):
                found.update(item for item in evidence_ids if isinstance(item, str))
            artifact_id = value.get("artifact_id")
            if isinstance(artifact_id, str):
                found.add(artifact_id)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(state.get("specialist_results", []))
    collect(state.get("competitor_data_modules", {}))
    return found


def _asserts_private_competitor_metric(content: str) -> bool:
    """Reject numeric private metrics, without swallowing valid plans nearby.

    The old expression could span from an explicit "unavailable" disclaimer to
    an unrelated percentage later in the paragraph and replace the whole
    section.  Evaluate sentence-sized matches and exempt explicit absence
    statements instead.
    """

    for sentence in re.split(r"[\n。！？]+", content):
        if _PRIVATE_COMPETITOR_METRIC.search(sentence) and not _PRIVATE_METRIC_UNAVAILABLE.search(
            sentence
        ):
            return True
    return False
