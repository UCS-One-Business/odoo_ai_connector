#!/usr/bin/env bash
# Tag a release after verifying version consistency.
#   scripts/release.sh            # verify only
#   scripts/release.sh --tag      # verify, then create and push tag vX.Y.Z
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

PKG_VERSION=$(python3 -c "
import re
src = open('ucs_ai_connector/__init__.py').read()
print(re.search(r'__version__ = \"([^\"]+)\"', src).group(1))")
MCP_VERSION=$(python3 -c "
import re
src = open('ucs_ai_mcp_server.py').read()
print(re.search(r\"__version__ = '([^']+)'\", src).group(1))")

if [ "$PKG_VERSION" != "$MCP_VERSION" ]; then
    echo "ERROR: version mismatch: ucs_ai_connector/__init__.py=$PKG_VERSION" \
         "ucs_ai_mcp_server.py=$MCP_VERSION" >&2
    exit 1
fi

if ! grep -q "## \[$PKG_VERSION\]" CHANGELOG.md; then
    echo "ERROR: CHANGELOG.md has no '## [$PKG_VERSION]' section" >&2
    exit 1
fi

echo "Version consistent: $PKG_VERSION"

if [ "${1-}" = "--tag" ]; then
    if git rev-parse "v$PKG_VERSION" >/dev/null 2>&1; then
        echo "ERROR: tag v$PKG_VERSION already exists" >&2
        exit 1
    fi
    git tag -a "v$PKG_VERSION" -m "ucs-ai-connector $PKG_VERSION"
    git push origin "v$PKG_VERSION"
    echo "Tagged and pushed v$PKG_VERSION."
    echo "Remember: update the pinned adapter URL in ucs_ai (ADAPTER_URL in"
    echo "models/ucs_ai_token.py and docs/connecting.md) to this tag."
fi
