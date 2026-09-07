# hivemind.llm.providers

The providers package holds one sub-package per model vendor or local server kind. These are the
only modules in the whole workspace allowed to import a vendor LLM SDK or an HTTP client aimed
at a model server, so that swapping a provider never touches code above llm/.
