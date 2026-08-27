from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .mcp import (
    MCPGatewayError,
    MCPProvider,
    MCPProviderClient,
    MCPToolResult,
    seller_sprite_mcp_config,
    sif_mcp_config,
)
from .mcp_transport import StreamableHTTPMCPTransport
from .models import KeywordEvidence, KeywordObservation, KeywordResearchBatch, ListingTaskRequest
from .state import ListingAgentState


KEYWORD_FIELDS = (
    "keyword",
    "searchTerm",
    "search_term",
    "keywordText",
    "keyword_text",
    "word",
)
RELEVANCE_FIELDS = ("relevancy", "relevance", "relevanceScore", "relevance_score")
PERIOD_FIELDS = ("month", "date", "dataPeriod", "data_period", "time_value")


@dataclass(frozen=True)
class KeywordResearchQuery:
    provider: MCPProvider
    tool: str
    arguments: dict[str, Any]
    default_relevance: float

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "provider": self.provider.value,
                "tool": self.tool,
                "arguments": self.arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]


class KeywordQueryPlanner:
    """Build bounded calls using the schemas discovered from both MCP servers."""

    def initial(self, state: ListingAgentState) -> list[KeywordResearchQuery]:
        request = self._request(state)
        asins = list(dict.fromkeys(
            [item for item in [request.current_asin, *request.competitor_asins[:2]] if item]
        ))
        seeds = request.seed_keywords[:2]
        queries: list[KeywordResearchQuery] = []
        for asin in asins:
            queries.extend(self._asin_queries(request.marketplace, asin))
        for seed in seeds:
            queries.extend(self._seed_queries(request.marketplace, seed))
        return self._new_queries(queries, state)

    def supplement(self, state: ListingAgentState) -> list[KeywordResearchQuery]:
        request = self._request(state)
        round_index = state.get("supplement_search_round", 0)
        queries: list[KeywordResearchQuery] = []

        # Initial research uses at most two competitors. Each supplement round
        # adds one remaining competitor, keeping provider cost and latency bounded.
        remaining_asins = request.competitor_asins[2:]
        if round_index < len(remaining_asins):
            queries.extend(self._asin_queries(request.marketplace, remaining_asins[round_index]))

        expansion_seeds = [
            *request.seed_keywords[2:],
            request.product_brief.product_type,
            request.product_brief.product_name,
        ]
        if round_index < len(expansion_seeds):
            queries.extend(
                self._seed_queries(request.marketplace, expansion_seeds[round_index])
            )
        return self._new_queries(queries, state)

    @staticmethod
    def _request(state: ListingAgentState) -> ListingTaskRequest:
        return ListingTaskRequest.model_validate(
            state.get("validated_request") or state.get("request") or {}
        )

    @staticmethod
    def _asin_queries(marketplace: str, asin: str) -> list[KeywordResearchQuery]:
        return [
            KeywordResearchQuery(
                provider=MCPProvider.SELLER_SPRITE,
                tool="traffic_keyword",
                arguments={
                    "request": {
                        "marketplace": marketplace,
                        "asin": asin,
                        "page": 1,
                        "size": 50,
                    }
                },
                default_relevance=0.78,
            ),
            KeywordResearchQuery(
                provider=MCPProvider.SIF,
                tool="market_get_asin_keyword_signals",
                arguments={"asin": asin, "country": marketplace, "topN": 100},
                default_relevance=0.80,
            ),
        ]

    @staticmethod
    def _seed_queries(marketplace: str, seed: str) -> list[KeywordResearchQuery]:
        return [
            KeywordResearchQuery(
                provider=MCPProvider.SELLER_SPRITE,
                tool="keyword_miner",
                arguments={
                    "request": {
                        "marketplace": marketplace,
                        "keyword": seed,
                        "page": 1,
                        "size": 50,
                        "returnFields": "keyword,searches,purchases,purchaseRate,relevancy",
                    }
                },
                default_relevance=0.75,
            ),
            KeywordResearchQuery(
                provider=MCPProvider.SIF,
                tool="market_screen_keyword_opportunities",
                arguments={"keyword_root": seed, "country": marketplace, "topN": 100},
                default_relevance=0.75,
            ),
        ]

    @staticmethod
    def _new_queries(
        queries: list[KeywordResearchQuery], state: ListingAgentState
    ) -> list[KeywordResearchQuery]:
        executed = set(state.get("executed_query_fingerprints", []))
        return [query for query in queries if query.fingerprint not in executed]


class KeywordEvidenceMapper:
    """Convert provider-specific MCP payloads into auditable domain evidence."""

    def map_result(
        self,
        result: MCPToolResult,
        query: KeywordResearchQuery,
        *,
        marketplace: str,
    ) -> list[KeywordEvidence]:
        evidence: list[KeywordEvidence] = []
        for index, record in enumerate(self._records(result.payload), start=1):
            keyword = self._keyword(record)
            if not keyword:
                continue
            normalized = self.normalize_keyword(keyword)
            if not normalized:
                continue
            relevance = self._relevance(record, query.default_relevance)
            confidence = 0.78 if result.provider == MCPProvider.SIF else 0.76
            score = min(1.0, relevance * 0.75 + confidence * 0.25)
            evidence_ref = (
                f"{result.provider.value}:{result.tool}:{result.call_id}:record-{index}"
            )
            observation = KeywordObservation(
                observation_id=f"{result.call_id}-obs-{index}",
                source=result.provider.value,
                tool=result.tool,
                query_ref=query.fingerprint,
                fetched_at=result.finished_at.isoformat(),
                data_period=self._period(record),
                metrics=self._metrics(record),
            )
            keyword_id = hashlib.sha256(
                f"{marketplace.casefold()}:{normalized}".encode()
            ).hexdigest()[:20]
            evidence.append(
                KeywordEvidence(
                    keyword_id=f"kw-{keyword_id}",
                    keyword=keyword.strip(),
                    normalized_keyword=normalized,
                    marketplace=marketplace,
                    relevance=relevance,
                    confidence=confidence,
                    score=score,
                    observations=[observation],
                    evidence_refs=[evidence_ref],
                    selection_reason=(
                        "MCP 返回的关键词记录，相关性达到入选阈值"
                        if relevance >= 0.7
                        else None
                    ),
                    exclusion_reason=("相关性低于 0.70" if relevance < 0.7 else None),
                )
            )
        return evidence

    @staticmethod
    def normalize_keyword(value: str) -> str:
        value = unicodedata.normalize("NFKC", value).casefold().strip()
        return re.sub(r"\s+", " ", value)

    def _records(self, payload: Any) -> Iterable[dict[str, Any]]:
        roots: list[Any] = []
        if isinstance(payload, dict):
            if "structuredContent" in payload:
                roots.append(payload["structuredContent"])
            for block in payload.get("content", []):
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    try:
                        roots.append(json.loads(block["text"]))
                    except json.JSONDecodeError:
                        continue
            roots.append(payload)
        else:
            roots.append(payload)

        seen: set[str] = set()
        for root in roots:
            for record in self._walk(root):
                marker = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
                if marker not in seen:
                    seen.add(marker)
                    yield record

    def _walk(self, value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            if self._keyword(value):
                yield value
                return
            for child in value.values():
                yield from self._walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk(child)

    @staticmethod
    def _keyword(record: dict[str, Any]) -> str | None:
        for field in KEYWORD_FIELDS:
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                return value
        return None

    @staticmethod
    def _relevance(record: dict[str, Any], default: float) -> float:
        for field in RELEVANCE_FIELDS:
            value = record.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
                if numeric > 1:
                    numeric /= 100
                return min(1.0, max(0.0, numeric))
        return default

    @staticmethod
    def _metrics(record: dict[str, Any]) -> dict[str, int | float | str | bool | None]:
        metrics: dict[str, int | float | str | bool | None] = {}
        for key, value in record.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    continue
                metrics[key] = value
        return metrics

    @staticmethod
    def _period(record: dict[str, Any]) -> str | None:
        for field in PERIOD_FIELDS:
            value = record.get(field)
            if isinstance(value, (str, int)):
                return str(value)
        return None


class KeywordResearchGateway:
    """Run two providers in parallel, but queries within each provider in order."""

    def __init__(
        self,
        *,
        seller_sprite_client: MCPProviderClient,
        sif_client: MCPProviderClient,
        planner: KeywordQueryPlanner | None = None,
        mapper: KeywordEvidenceMapper | None = None,
    ) -> None:
        self.clients = {
            MCPProvider.SELLER_SPRITE: seller_sprite_client,
            MCPProvider.SIF: sif_client,
        }
        self.planner = planner or KeywordQueryPlanner()
        self.mapper = mapper or KeywordEvidenceMapper()

    def initial_search(self, state: ListingAgentState) -> KeywordResearchBatch:
        return self._execute(self.planner.initial(state), state)

    def supplement_search(self, state: ListingAgentState) -> KeywordResearchBatch:
        return self._execute(self.planner.supplement(state), state)

    def _execute(
        self, queries: list[KeywordResearchQuery], state: ListingAgentState
    ) -> KeywordResearchBatch:
        request = ListingTaskRequest.model_validate(
            state.get("validated_request") or state.get("request") or {}
        )
        by_provider = {
            provider: [query for query in queries if query.provider == provider]
            for provider in self.clients
        }
        batches: list[tuple[list[KeywordEvidence], list[dict[str, Any]], list[dict[str, Any]]]] = []
        active = [(provider, items) for provider, items in by_provider.items() if items]
        with ThreadPoolExecutor(max_workers=min(2, len(active) or 1)) as executor:
            futures = [
                executor.submit(self._execute_provider, provider, items, request.marketplace)
                for provider, items in active
            ]
            for future in futures:
                batches.append(future.result())

        evidence = [item for batch, _, _ in batches for item in batch]
        artifacts = [item for _, batch, _ in batches for item in batch]
        warnings = [item for _, _, batch in batches for item in batch]
        return KeywordResearchBatch(
            evidence=evidence,
            executed_query_fingerprints=[query.fingerprint for query in queries],
            artifacts=artifacts,
            warnings=warnings,
        )

    def _execute_provider(
        self,
        provider: MCPProvider,
        queries: list[KeywordResearchQuery],
        marketplace: str,
    ) -> tuple[list[KeywordEvidence], list[dict[str, Any]], list[dict[str, Any]]]:
        evidence: list[KeywordEvidence] = []
        artifacts: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        client = self.clients[provider]
        for query in queries:
            try:
                result = client.call(query.tool, query.arguments)
                mapped = self.mapper.map_result(result, query, marketplace=marketplace)
                evidence.extend(mapped)
                artifacts.append(
                    {
                        "kind": "mcp_call",
                        "call_id": result.call_id,
                        "provider": provider.value,
                        "tool": query.tool,
                        "query_ref": query.fingerprint,
                        "record_count": len(mapped),
                        "duration_ms": result.duration_ms,
                        "fetched_at": result.finished_at.isoformat(),
                    }
                )
            except MCPGatewayError as exc:
                warnings.append(
                    {
                        "code": exc.code,
                        "provider": provider.value,
                        "tool": query.tool,
                        "query_ref": query.fingerprint,
                        "retryable": exc.retryable,
                        "message": str(exc),
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
        return evidence, artifacts, warnings


def build_keyword_research_gateway(
    *, env_file: str = ".env", trust_env: bool = False
) -> KeywordResearchGateway:
    """Build the production researcher that can be injected into the graph services."""

    transport = StreamableHTTPMCPTransport(env_file=env_file, trust_env=trust_env)
    return KeywordResearchGateway(
        seller_sprite_client=MCPProviderClient(
            config=seller_sprite_mcp_config(), transport=transport
        ),
        sif_client=MCPProviderClient(config=sif_mcp_config(), transport=transport),
    )
