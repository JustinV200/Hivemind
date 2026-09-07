# hivemind.llm

The llm package defines the LLMProvider and EmbeddingProvider protocols the rest of the Hive
uses to talk to a model, plus slot resolution, ladders, routing and the Fanner (the seat meter
every model call passes through). It is provider-agnostic: no vendor SDK is imported outside
llm/providers/, and code above this package sees only our own request, response and capability
models.
