# Python package candidate

The package workflow builds a wheel and source distribution from the checkout, validates metadata, installs the wheel in a fresh virtual environment outside the repository, and checks CLI and MCP imports. It does not publish to PyPI or start a brokerage connection.

Download the artifacts from a successful [Python package candidate workflow](https://github.com/RyanYang053/IBKR-Agent-harness/actions/workflows/package-candidate.yml) and verify SHA256SUMS. Source-checkout installation remains the documented path for configuration templates, fixtures and engineering examples. A wheel smoke test is not an end-to-end installed-package readiness certificate.

Before registry publication: verify package name ownership, installed configuration behavior, supported platforms, license notices and a signed/versioned release. Never publish credentials or private state.
