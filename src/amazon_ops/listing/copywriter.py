from __future__ import annotations

from amazon_ops.llm import StructuredLLM

from .models import ListingDraft
from .prompts import (
    LISTING_COPYWRITER_SYSTEM_PROMPT,
    LISTING_REVISION_SYSTEM_PROMPT,
    build_listing_copy_context,
)
from .state import ListingAgentState


class DeepSeekListingCopywriter:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def generate(self, state: ListingAgentState) -> ListingDraft:
        return self.llm.complete(
            system_prompt=LISTING_COPYWRITER_SYSTEM_PROMPT,
            context=build_listing_copy_context(state),
            output_model=ListingDraft,
            max_tokens=5000,
        )

    def revise(self, state: ListingAgentState) -> ListingDraft:
        return self.llm.complete(
            system_prompt=LISTING_REVISION_SYSTEM_PROMPT,
            context=build_listing_copy_context(state, revision=True),
            output_model=ListingDraft,
            max_tokens=5000,
        )
