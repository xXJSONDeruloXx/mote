#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(pwd)"
CLI_LOCATION="${ROOT_DIR}/cli/decky"
PLUGIN_NAME="$(ROOT_DIR="${ROOT_DIR}" python - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ["ROOT_DIR"])
print(json.loads((root / "plugin.json").read_text())["name"])
PY
)"
OUTPUT_ZIP="${ROOT_DIR}/out/${PLUGIN_NAME}.zip"

if ! test -x "${CLI_LOCATION}"; then
    echo "Decky CLI not found at ${CLI_LOCATION}. Run .vscode/setup.sh first."
    exit 1
fi

echo "Building plugin in ${ROOT_DIR}"
"${CLI_LOCATION}" plugin build "${ROOT_DIR}"

if test -d "${ROOT_DIR}/assets"; then
    echo "Adding bundled assets to ${OUTPUT_ZIP}"
    ROOT_DIR="${ROOT_DIR}" OUTPUT_ZIP="${OUTPUT_ZIP}" PLUGIN_NAME="${PLUGIN_NAME}" python - <<'PY'
import os
import zipfile
from pathlib import Path

root = Path(os.environ["ROOT_DIR"])
zip_path = Path(os.environ["OUTPUT_ZIP"])
plugin_name = os.environ["PLUGIN_NAME"]
assets_dir = root / "assets"

with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
    for path in assets_dir.rglob("*"):
        if path.is_file():
            zf.write(path, Path(plugin_name) / path.relative_to(root))
PY
fi
