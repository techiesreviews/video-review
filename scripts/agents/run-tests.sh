#!/usr/bin/env bash
# The project's checks.test (scripts/agents/kit.json): the Python and Node unit tests. Needs Bash, Python and Node, but no npm install or network.
# kit pr runs a check without a shell, so the choice of Python (python3, else python on Windows) lives here.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/../.."
python=python3
"$python" -c '' > /dev/null 2>&1 || python=python
"$python" -m unittest discover -s tests
# These two are plain scripts, not unittest classes, so discovery does not collect them.
"$python" tests/test_server.py
"$python" tests/test_mcp_server.py
node --test tests/*.test.mjs
