from __future__ import annotations

from dataclasses import dataclass, field

from amazon_ops.listing import (
    CopyIssue,
    CopyValidationReport,
    KeywordEvidence,
    KeywordObservation,
    ListingDraft,
    ListingWorkflowServices,
    build_listing_agent_graph,
)


def evidence(index: int, *, dual_source: bool = True) -> KeywordEvidence:
    observations = [
        KeywordObservation(
            observation_id=f"ss-{index}",
            source="seller_sprite",
            tool="keyword_miner",
            query_ref=f"query-ss-{index}",
            fetched_at="2026-08-13T00:00:00Z",
            metrics={"searches": 1000 - index},
        )
    ]
    if dual_source:
        observations.append(
            KeywordObservation(
                observation_id=f"sif-{index}",
                source="sif",
                tool="market_get_asin_keyword_signals",
                query_ref=f"query-sif-{index}",
                fetched_at="2026-08-13T00:00:00Z",
                metrics={"traffic_contribution": 0.8},
            )
        )
    return KeywordEvidence(
        keyword_id=f"kw-{index}",
        keyword=f"phone stand keyword {index}",
        normalized_keyword=f"phone stand keyword {index}",
        marketplace="US",
        relevance=0.9,
        confidence=0.8,
        score=max(0.01, 1 - index / 100),
        observations=observations,
        evidence_refs=[item.observation_id for item in observations],
        selection_reason="与产品事实相关",
    )


def request() -> dict:
    return {
        "marketplace": "US",
        "language": "en-US",
        "category": "Cell Phone Stands",
        "product_brief": {
            "product_name": "Adjustable Phone Stand",
            "product_type": "Desktop phone holder",
            "brand": "Example",
            "variant_attributes": {"color": "Space Gray", "pack_quantity": "1"},
            "core_features": [
                {"name": "adjustment", "value": "angle adjustable", "source": "user_brief"},
                {"name": "folding", "value": "foldable", "source": "user_brief"},
                {"name": "base", "value": "non-slip base", "source": "user_brief"},
            ],
            "materials": [
                {"name": "material", "value": "aluminum", "source": "user_brief"}
            ],
            "specifications": [
                {"name": "compatibility", "value": "4–7 inch phones", "source": "user_brief"},
                {"name": "weight", "value": "180 g", "source": "user_brief"},
            ],
            "target_audiences": ["smartphone users"],
            "use_cases": ["office desk", "video calls"],
            "package_contents": ["phone stand", "user guide"],
        },
        "seed_keywords": ["phone stand"],
    }


def draft(title: str = "Example Adjustable Phone Stand") -> ListingDraft:
    return ListingDraft(
        title=title,
        subtitle="Stable aluminum desktop holder",
        bullet_points=[f"Benefit {index}" for index in range(1, 6)],
        description="An adjustable aluminum stand for everyday desk use.",
        search_terms="desktop holder adjustable cradle",
    )


@dataclass
class FakeResearcher:
    initial_count: int
    supplement_counts: list[int]
    supplement_calls: int = 0
    next_index: int = 0

    def _items(self, count: int) -> list[KeywordEvidence]:
        items = [evidence(index) for index in range(self.next_index, self.next_index + count)]
        self.next_index += count
        return items

    def initial_search(self, state) -> list[KeywordEvidence]:
        return self._items(self.initial_count)

    def supplement_search(self, state) -> list[KeywordEvidence]:
        count = self.supplement_counts[self.supplement_calls]
        self.supplement_calls += 1
        return self._items(count)


@dataclass
class FakeCopywriter:
    generate_calls: int = 0
    revise_calls: int = 0

    def generate(self, state) -> ListingDraft:
        self.generate_calls += 1
        return draft()

    def revise(self, state) -> ListingDraft:
        self.revise_calls += 1
        return draft("Example Phone Stand, Revised")


@dataclass
class FakeValidator:
    reports: list[CopyValidationReport] = field(
        default_factory=lambda: [CopyValidationReport(passed=True, keyword_coverage=1.0)]
    )
    calls: int = 0

    def validate(self, state) -> CopyValidationReport:
        report = self.reports[min(self.calls, len(self.reports) - 1)]
        self.calls += 1
        return report


def graph_with(researcher, copywriter=None, validator=None):
    return build_listing_agent_graph(
        services=ListingWorkflowServices(
            researcher=researcher,
            copywriter=copywriter or FakeCopywriter(),
            validator=validator or FakeValidator(),
        )
    )


def test_supplements_until_exactly_thirty_then_generates_copy():
    researcher = FakeResearcher(initial_count=10, supplement_counts=[10, 10, 10])
    copywriter = FakeCopywriter()
    result = graph_with(researcher, copywriter).invoke({"request_id": "run-1", "request": request()})

    assert result["supplement_search_round"] == 2
    assert researcher.supplement_calls == 2
    assert result["valid_keyword_count"] == 30
    assert len(result["selected_keywords"]) == 30
    assert [item["tier"] for item in result["selected_keywords"]].count("primary") == 2
    assert [item["tier"] for item in result["selected_keywords"]].count("secondary") == 8
    assert [item["tier"] for item in result["selected_keywords"]].count("long_tail") == 12
    assert [item["tier"] for item in result["selected_keywords"]].count("backend_only") == 8
    assert copywriter.generate_calls == 1
    assert result["final_result"]["status"] == "completed"


def test_stops_after_three_supplement_rounds_without_generating_copy():
    researcher = FakeResearcher(initial_count=5, supplement_counts=[2, 2, 2])
    copywriter = FakeCopywriter()
    result = graph_with(researcher, copywriter).invoke({"request_id": "run-2", "request": request()})

    assert researcher.supplement_calls == 3
    assert result["supplement_search_round"] == 3
    assert result["valid_keyword_count"] == 11
    assert result["research_status"] == "exhausted"
    assert result["final_result"]["status"] == "insufficient_keyword_evidence"
    assert "19" in result["final_result"]["summary"]
    assert copywriter.generate_calls == 0


def test_invalid_input_ends_in_needs_input_without_research():
    researcher = FakeResearcher(initial_count=30, supplement_counts=[])
    result = graph_with(researcher).invoke({"request_id": "run-3", "request": {}})

    assert result["final_result"]["status"] == "needs_input"
    assert result["research_status"] == "waiting_input"
    assert result["final_result"]["clarification_question"]
    assert researcher.next_index == 0


def test_incomplete_product_brief_returns_specific_fields_before_research():
    researcher = FakeResearcher(initial_count=30, supplement_counts=[])
    incomplete = request()
    incomplete["product_brief"] = {
        "product_name": "Adjustable Phone Stand",
        "product_type": "Desktop phone holder",
        "brand": "Example",
    }

    result = graph_with(researcher).invoke({"request_id": "run-input", "request": incomplete})

    assert result["final_result"]["status"] == "needs_input"
    assert "product_brief.core_features" in result["missing_fields"]
    assert "product_brief.materials" in result["missing_fields"]
    assert "product_brief.specifications" in result["missing_fields"]
    assert "核心功能" in result["clarification_question"]
    assert researcher.next_index == 0


def test_failed_copy_validation_revises_once():
    researcher = FakeResearcher(initial_count=30, supplement_counts=[])
    copywriter = FakeCopywriter()
    validator = FakeValidator(
        reports=[
            CopyValidationReport(
                passed=False,
                keyword_coverage=0.9,
                issues=[CopyIssue(code="TITLE_LENGTH", field="title", message="标题过长")],
            ),
            CopyValidationReport(passed=True, keyword_coverage=1.0),
        ]
    )
    result = graph_with(researcher, copywriter, validator).invoke(
        {"request_id": "run-4", "request": request()}
    )

    assert copywriter.generate_calls == 1
    assert copywriter.revise_calls == 1
    assert validator.calls == 2
    assert result["final_result"]["status"] == "completed"
    assert result["final_result"]["draft"]["title"].endswith("Revised")


def test_supplements_when_thirty_keywords_exist_but_primary_dual_source_is_missing():
    class PrimaryEvidenceResearcher:
        supplement_calls = 0

        def initial_search(self, state):
            return [evidence(index, dual_source=False) for index in range(30)]

        def supplement_search(self, state):
            self.supplement_calls += 1
            return [evidence(30), evidence(31)]

    researcher = PrimaryEvidenceResearcher()
    result = graph_with(researcher).invoke({"request_id": "run-5", "request": request()})

    assert researcher.supplement_calls == 1
    assert result["valid_keyword_count"] == 32
    primary = [item for item in result["selected_keywords"] if item["tier"] == "primary"]
    assert len(primary) == 2
    assert all(
        {observation["source"] for observation in item["observations"]}
        == {"seller_sprite", "sif"}
        for item in primary
    )
