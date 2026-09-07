# hivemind test-data builders

Small factory functions used across the other test trees to build valid test data without
repeating pydantic model boilerplate in every test: `make_task`, `make_cell_spec`, and similar.
Builders return real, validated models; they never bypass validation to save time.
