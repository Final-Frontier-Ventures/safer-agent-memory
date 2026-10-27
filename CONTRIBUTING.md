# Contributing

Thanks for helping. Three kinds of contribution help most right now:

1. **Conformance reports.** You applied AMS to a real system. What tier, which backend, which tests passed, what was awkward. Open an issue with the `report` label.
2. **Backends.** Implement `ams.backend.Backend`, add a factory to `conformance/conftest.py`, and make the suite pass.
3. **Standard changes.** Open an issue first. Changes to MUST rules need a rationale and, where possible, a new conformance test.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
pytest
```

The `ams` package has no runtime dependencies; keep it that way. Every new rule in `STANDARD.md` should come with a test in `conformance/`.

By contributing you agree that code is licensed under Apache-2.0 and text under CC BY 4.0.
