from __future__ import annotations

from datetime import datetime, timezone

from amazon_ops.listing import (
    KeywordEvidenceMapper,
    KeywordQueryPlanner,
    KeywordResearchGateway,
    MCPGatewayError,
    MCPProvider,
    MCPToolResult,
)


def request_payload():
    fact = lambda name, value: {"name": name, "value": value, "source": "user"}
    return {
        "marketplace": "US",
        "language": "en-US",
        "category": "Cell Phone Stands",
        "product_brief": {
            "product_name": "Foldable Aluminum Phone Stand",
            "product_type": "phone stand",
            "brand": "Example",
            "core_features": [
                fact("foldable", "yes"),
                fact("angle", "adjustable"),
                fact("base", "anti-slip"),
            ],
            "materials": [fact("material", "aluminum")],
            "specifications": [fact("height", "6 in"), fact("weight", "8 oz")],
            "target_audiences": ["desk users"],
            "use_cases": ["video calls", "watching videos"],
            "package_contents": ["phone stand"],
        },
        "current_asin": "B000000001",
        "competitor_asins": ["B000000002", "B000000003", "B000000004"],
        "seed_keywords": ["phone stand", "desk phone holder", "foldable phone stand"],
    }


def tool_result(provider, tool, payload, call_id="mcp-test"):
    now = datetime.now(timezone.utc)
    return MCPToolResult(
        call_id=call_id,
        provider=provider,
        tool=tool,
        arguments={},
        payload=payload,
        started_at=now,
        finished_at=now,
        duration_ms=4,
    )


def test_planner_uses_live_discovered_provider_schemas_and_bounded_initial_scope():
    planner = KeywordQueryPlanner()

    queries = planner.initial({"validated_request": request_payload()})

    seller_queries = [q for q in queries if q.provider == MCPProvider.SELLER_SPRITE]
    sif_queries = [q for q in queries if q.provider == MCPProvider.SIF]
    assert len(seller_queries) == len(sif_queries) == 5
    miner = next(q for q in seller_queries if q.tool == "keyword_miner")
    assert set(miner.arguments) == {"request"}
    assert isinstance(miner.arguments["request"]["returnFields"], str)
    signal = next(q for q in sif_queries if q.tool == "market_get_asin_keyword_signals")
    assert "request" not in signal.arguments
    assert signal.arguments["country"] == "US"


def test_supplement_planner_uses_remaining_competitor_and_avoids_fingerprint_repeats():
    planner = KeywordQueryPlanner()
    state = {"validated_request": request_payload(), "supplement_search_round": 0}
    first = planner.supplement(state)
    state["executed_query_fingerprints"] = [query.fingerprint for query in first]

    repeated = planner.supplement(state)

    assert {q.arguments.get("asin") or q.arguments.get("request", {}).get("asin") for q in first}
    assert repeated == []


def test_mapper_reads_structured_and_text_payloads_without_losing_traceability():
    planner = KeywordQueryPlanner()
    seller_query = next(
        q
        for q in planner.initial({"validated_request": request_payload()})
        if q.tool == "keyword_miner"
    )
    payload = {
        "structuredContent": {
            "data": {"items": [{"keyword": " Phone  Stand ", "relevancy": 86, "searches": 1200}]}
        },
        "content": [{"type": "text", "text": '{"items":[{"keyword":"desk holder","relevance":0.65}]}'}],
    }

    mapped = KeywordEvidenceMapper().map_result(
        tool_result(MCPProvider.SELLER_SPRITE, "keyword_miner", payload),
        seller_query,
        marketplace="US",
    )

    assert [item.normalized_keyword for item in mapped] == ["phone stand", "desk holder"]
    assert mapped[0].relevance == 0.86
    assert mapped[0].evidence_refs[0].startswith("seller_sprite:keyword_miner:mcp-test")
    assert mapped[1].exclusion_reason == "相关性低于 0.70"


def test_gateway_keeps_one_provider_results_when_the_other_provider_fails():
    class StubClient:
        def __init__(self, provider, fail=False):
            self.provider = provider
            self.fail = fail

        def call(self, tool, arguments):
            if self.fail:
                raise MCPGatewayError(
                    "temporary outage",
                    provider=self.provider,
                    tool=tool,
                    code="MCP_TEMPORARY_FAILURE",
                    retryable=True,
                )
            return tool_result(
                self.provider,
                tool,
                {"data": {"items": [{"keyword": "phone stand", "relevance": 0.9}]}},
                call_id=f"mcp-{self.provider.value}",
            )

    gateway = KeywordResearchGateway(
        seller_sprite_client=StubClient(MCPProvider.SELLER_SPRITE),
        sif_client=StubClient(MCPProvider.SIF, fail=True),
    )

    batch = gateway.initial_search({"validated_request": request_payload()})

    assert batch.evidence
    assert {item.observations[0].source for item in batch.evidence} == {"seller_sprite"}
    assert batch.warnings
    assert {warning["provider"] for warning in batch.warnings} == {"sif"}
    assert len(batch.executed_query_fingerprints) == 10
