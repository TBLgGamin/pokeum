# Tests

- The suite is **offline-only**: no network, no external services. Test pure logic; anything that needs a running service does not belong in `tests/`.
- Use pytest. Docstrings are not required here (Ruff `D` rules are disabled for `tests/*`); test names carry the documentation.
- No fixed coverage target: cover the logic where mistakes hurt (config parsing, core behaviour), skip trivial pass-throughs.
- Environment-dependent code is tested by monkeypatching settings, not by reading a developer's real environment.
