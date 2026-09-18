"""Evidence-bounded competitor report generation and deterministic rendering."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .models import (
    CompetitorAdvertisingReport,
    CompetitorReportSection,
    TrafficKeywordLookupModule,
    TrafficKeywordLookupRecord,
)


SECTION_TITLES = {
    "traffic_keyword_lookup": "01｜模块：查流量词",
    "traffic_keyword_reverse_lookup": "02｜模块：反查流量词",
    "multi_variant_organic_position": "03｜模块：查多变体自然位",
    "recommendation_placement": "04｜模块：查推荐专栏",
}
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
    if section_key == "traffic_keyword_lookup":
        deterministic = build_traffic_keyword_lookup_section(state)
        if deterministic is not None:
            return deterministic
    report = validate_competitor_report(
        CompetitorAdvertisingReport(title="section-validation", sections=[section]), state
    )
    return next(item for item in report.sections if item.key == section_key)


def build_traffic_keyword_lookup_section(
    state: dict[str, Any],
) -> CompetitorReportSection | None:
    """Render the fixed, user-approved module-one layout from module JSON."""
    modules = state.get("competitor_data_modules")
    if not isinstance(modules, dict) or not isinstance(modules.get("traffic_keyword_lookup"), dict):
        return None
    module = TrafficKeywordLookupModule.model_validate(modules["traffic_keyword_lookup"])
    records = {record.asin_role: record for record in module.records}
    own, competitor = records.get("own"), records.get("competitor")

    blocks: list[str] = []
    summary = _parent_distribution_summary(own, competitor)
    if summary:
        blocks.append(summary)
    blocks.append(_parent_advertising_table(own, competitor))
    blocks.append(_variant_traffic_table(module.records))
    blocks.append(_traffic_keyword_lookup_analysis(own, competitor))

    return CompetitorReportSection(
        key="traffic_keyword_lookup",
        status=module.status,
        content="\n\n".join(block for block in blocks if block),
        evidence_refs=list(dict.fromkeys(module.evidence_ids)),
    )


def _parent_distribution_summary(
    own: TrafficKeywordLookupRecord | None,
    competitor: TrafficKeywordLookupRecord | None,
) -> str:
    clauses = [_parent_distribution_clause("自有 ASIN", own), _parent_distribution_clause("竞品 ASIN", competitor)]
    return "父 ASIN Listing 自然-广告流量分布：" + "；".join(
        clause for clause in clauses if clause
    ) + "。" if any(clauses) else ""


def _parent_distribution_clause(label: str, record: TrafficKeywordLookupRecord | None) -> str:
    if record is None:
        return ""
    natural, advertising = record.listing_natural_traffic, record.listing_ad_traffic
    if None in {natural.score, natural.ratio, advertising.score, advertising.ratio}:
        return ""
    return (
        f"{label}（{record.parent_asin}）自然流量得分为 {_whole_number(natural.score)}，"
        f"占比 {_percentage(natural.ratio)}；广告流量得分为 {_whole_number(advertising.score)}，"
        f"占比 {_percentage(advertising.ratio)}"
    )


def _parent_advertising_table(
    own: TrafficKeywordLookupRecord | None, competitor: TrafficKeywordLookupRecord | None
) -> str:
    header = (
        "### 父 ASIN 广告流量对比\n\n"
        "| ASIN 角色 | 父 ASIN | SP（常规）广告流量得分 | SP（常规）流量占比 | "
        "SP（推荐）广告流量得分 | SP（推荐）流量占比 | SB（常规）广告流量得分 | "
        "SB（常规）流量占比 | SBV 广告流量得分 | SBV 流量占比 |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    )
    rows = [_parent_advertising_row("自有 ASIN", own), _parent_advertising_row("竞品 ASIN", competitor)]
    return "\n".join([header, *(row for row in rows if row)])


def _parent_advertising_row(label: str, record: TrafficKeywordLookupRecord | None) -> str:
    if record is None:
        return ""
    distribution = record.advertising_traffic_distribution
    values: list[str] = [label, record.parent_asin]
    for metric in (distribution.sp, distribution.sp_recommend, distribution.sb, distribution.sbv):
        values.extend([_whole_number(metric.score), _percentage(metric.ratio)])
    return "| " + " | ".join(values) + " |"


def _variant_traffic_table(records: list[TrafficKeywordLookupRecord]) -> str:
    header = (
        "### 子 ASIN 流量分布\n\n"
        "| # | ASIN 角色 | 父 ASIN | 变体 ASIN | 总流量占比 | 自然-广告流量分布 | "
        "自然流量占比 | SP（常规）流量占比 | SP（推荐）流量占比 | SB（常规）流量占比 | SBV 流量占比 |\n"
        "| ---: | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |"
    )
    rows: list[str] = [header]
    for role, label in (("own", "自有 ASIN"), ("competitor", "竞品 ASIN")):
        record = next((item for item in records if item.asin_role == role), None)
        if record is None:
            continue
        for index, variant in enumerate(record.variants, start=1):
            distribution = _natural_ad_distribution(variant.natural_traffic_ratio, variant.ad_traffic_ratio)
            rows.append(
                "| " + " | ".join(
                    [
                        str(index), label, record.parent_asin, variant.variant_asin,
                        _percentage(variant.total_traffic_ratio), distribution,
                        _percentage(variant.natural_traffic_ratio), _percentage(variant.sp_ratio),
                        _percentage(variant.sp_recommend_ratio), _percentage(variant.sb_ratio),
                        _percentage(variant.sbv_ratio),
                    ]
                ) + " |"
            )
    return "\n".join(rows)


def _traffic_keyword_lookup_analysis(
    own: TrafficKeywordLookupRecord | None, competitor: TrafficKeywordLookupRecord | None
) -> str:
    lines = ["**数据分析**"]
    if own is None or competitor is None:
        return "\n".join(lines)
    for label, own_metric, competitor_metric in (
        ("自然流量", own.listing_natural_traffic, competitor.listing_natural_traffic),
        ("广告流量", own.listing_ad_traffic, competitor.listing_ad_traffic),
        ("SP（常规）广告流量", own.advertising_traffic_distribution.sp, competitor.advertising_traffic_distribution.sp),
        ("SP（推荐）广告流量", own.advertising_traffic_distribution.sp_recommend, competitor.advertising_traffic_distribution.sp_recommend),
        ("SB（常规）广告流量", own.advertising_traffic_distribution.sb, competitor.advertising_traffic_distribution.sb),
        ("SBV 广告流量", own.advertising_traffic_distribution.sbv, competitor.advertising_traffic_distribution.sbv),
    ):
        if own_metric.score is not None and competitor_metric.score is not None and competitor_metric.score > own_metric.score:
            lines.append(
                f"- 竞品 {label}得分更高（竞品 {_whole_number(competitor_metric.score)}，自有 {_whole_number(own_metric.score)}）。"
            )
    return "\n".join(lines)


def _natural_ad_distribution(natural_ratio: float | None, advertising_ratio: float | None) -> str:
    if natural_ratio is None or advertising_ratio is None:
        return ""
    return f"自然 {_percentage(natural_ratio)} / 广告 {_percentage(advertising_ratio)}"


def _whole_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def _percentage(value: float | None) -> str:
    if value is None:
        return ""
    percent = value * 100 if 0 <= value <= 1 else value
    return f"{percent:.2f}%"


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
