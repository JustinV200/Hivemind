"""Define EmbeddingRequest and EmbeddingResponse: the boundary every embed call crosses.

An embedding is a fixed-length vector of floats that stands in for a piece of text's meaning, so
that two texts with similar meaning land near each other in vector space (ADR-0036). This module
is the "our types at the boundary" half of that decision (codingrules section 8.6, mirroring
`hivemind.llm.models` for chat): `EmbeddingRequest` carries the texts to embed, and
`EmbeddingResponse` carries one vector per text, in order, plus the model id that produced them,
their shared dimension, and a normalised `Usage`. No vendor SDK type or wire field name appears
here; every adapter's own module is where that translation happens.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.embedding`. Built by
    every caller of `hivemind.llm.embedding.provider.EmbeddingProvider.embed` (the Fanner, the
    Honey Store's ripener, `hive llm test`) and returned by every `EmbeddingProvider`
    implementation. Calls into `hivemind.llm.models` only, for the shared `Usage` shape.

Key invariants:
    - Both models are frozen and forbid unknown fields (codingrules section 8.5).
    - `EmbeddingRequest.texts` holds at least one text, each at least one character, and never
      more than `MAX_EMBED_TEXTS`; a caller that needs to embed more splits into several requests.
    - `EmbeddingResponse.vectors` has exactly one entry per `EmbeddingRequest.texts` entry, in the
      same order (enforced by callers, not by this module, since a response is built independently
      of the request that produced it); every vector's length equals `dimensions`, and every
      component is a finite float (never `inf`/`nan`), checked by a validator on this class.

See Also:
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the decision this boundary
      implements.
    - hivemind.llm.embedding.provider for EmbeddingProvider, the protocol these models cross.
    - hivemind.llm.embedding.capabilities for EmbeddingCapabilities, the companion boundary value.
    - hivemind.llm.models for Usage, the normalised accounting shape reused here unchanged.
"""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.llm.models import Usage

MAX_EMBED_TEXTS = 256  # One request's own ceiling; a caller embedding more splits into several
# requests. Matches the largest batch every adapter in this phase declares (EmbeddingCapabilities.
# max_batch), so a single request can never outrun what any of them could serve in one call.

__all__ = ["MAX_EMBED_TEXTS", "EmbeddingRequest", "EmbeddingResponse"]

# A single text: never empty, since an empty string has no tokens to hash or embed meaningfully.
_NonEmptyText = Annotated[str, Field(min_length=1)]


class EmbeddingRequest(BaseModel):
    """One batch of texts to embed, in the order their vectors should come back.

    Carries no model id (codingrules section 8.6, "model slots, not model names" extended to
    embeddings): the adapter answering this request is already built for one specific
    `(provider, model)` pair (`hivemind.llm.embedding.bound.BoundEmbedder`), so there is nothing
    for a request field to select.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    texts: tuple[_NonEmptyText, ...] = Field(
        min_length=1,
        max_length=MAX_EMBED_TEXTS,
        description="The texts to embed, in the order their vectors should be returned.",
    )


class EmbeddingResponse(BaseModel):
    """One vector per requested text, in order, plus the model that produced them and their cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vectors: tuple[tuple[float, ...], ...] = Field(
        description="One embedding vector per EmbeddingRequest.texts entry, in the same order."
    )
    model: str = Field(description="The provider's own model id that produced these vectors.")
    dimensions: int = Field(ge=1, description="The length every vector in `vectors` must have.")
    usage: Usage = Field(
        description="Normalised token and cost accounting for this call; output_tokens is "
        "always 0, since embedding has nothing analogous to a generated reply."
    )

    @model_validator(mode="after")
    def _every_vector_matches_dimensions(self) -> EmbeddingResponse:
        """Reject a response whose vectors disagree with `dimensions`, or hold a non-finite value.

        A wrong length or a NaN/inf component would silently corrupt every distance computation
        the Honey Store's vector search (ADR-0035) later runs over a stored copy of this vector,
        so this is checked once, here, rather than trusted from every adapter individually.
        """
        for vector in self.vectors:
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"vector has {len(vector)} components, expected dimensions={self.dimensions}"
                )
            if not all(math.isfinite(component) for component in vector):
                raise ValueError("vector has a non-finite component (inf or nan)")
        return self
