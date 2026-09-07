"""Unit tests for scripts/size_rules_ts.py.

Fits into the Hive:
    Layer: none (tests for a dev-time gate helper). Exercises the brace-matching heuristic
    directly, including the documented gaps it knowingly leaves uncaught.

Key invariants:
    - None: this module holds tests, not behaviour.

See Also:
    - scripts/size_rules_ts.py, the module under test.
"""

# codingrules 6.2: a sentence-shaped test name is its own docstring, and `assert` is how pytest
# reports failure. pyproject.toml's [tool.ruff.lint.per-file-ignores] already grants S101/D103 to
# packages/*/tests/**; scripts/tests/ is not in that list (roadmap 0.3 was not authorised to add
# to it), so the same exemption is granted per-file here instead.

from size_rules_ts import find_function_length_violations


def test_find_function_length_violations_is_clean_for_a_short_function() -> None:
    source = "function f() {\n  return 1;\n}\n"

    findings = find_function_length_violations(source, "a.ts")

    assert findings == []


def test_find_function_length_violations_flags_a_long_function_declaration() -> None:
    body = "\n".join(f"  const x{i} = {i};" for i in range(60))
    source = f"function f() {{\n{body}\n}}\n"

    findings = find_function_length_violations(source, "a.ts")

    assert len(findings) == 1
    assert "a.ts:1: function 'f' is" in findings[0]
    assert "(limit 50)" in findings[0]


def test_find_function_length_violations_flags_a_long_arrow_function() -> None:
    body = "\n".join(f"  const x{i} = {i};" for i in range(60))
    source = f"const f = (a, b) => {{\n{body}\n}}\n"

    findings = find_function_length_violations(source, "a.ts")

    assert len(findings) == 1
    assert "function 'f' is" in findings[0]


def test_find_function_length_violations_skips_an_inline_callback_arrow_function() -> None:
    # Documented gap: an arrow function passed inline as a callback has no declaration line to
    # anchor on, so it is never counted -- even though this one is well over the limit.
    body = "\n".join(f"  x{i}();" for i in range(60))
    source = f"items.forEach((x) => {{\n{body}\n}});\n"

    findings = find_function_length_violations(source, "a.ts")

    assert findings == []


def test_find_function_length_violations_skips_unbalanced_braces_instead_of_crashing() -> None:
    # A `{` that never finds its matching `}` (here, deliberately truncated) must not raise; the
    # heuristic just gives up on that one declaration.
    source = "function f() {\n  const x = 1;\n"

    findings = find_function_length_violations(source, "a.ts")

    assert findings == []
