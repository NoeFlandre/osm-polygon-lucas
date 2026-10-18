#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ -z "${OAR_JOB_ID:-}" ]]; then
  echo "Refusing to run outside an active OAR allocation." >&2
  exit 2
fi

: "${LUCAS_CODE_ROOT:?set LUCAS_CODE_ROOT to the pinned checkout}"
: "${LUCAS_RUNTIME_PROJECT_ROOT:?set LUCAS_RUNTIME_PROJECT_ROOT to the QA-pinned runtime project}"
CODE_ROOT="$(cd "$LUCAS_CODE_ROOT" && pwd -P)"
RUNTIME_PROJECT_ROOT="$(cd "$LUCAS_RUNTIME_PROJECT_ROOT" && pwd -P)"
RUN_ROOT="${LUCAS_RUN_ROOT:-$HOME/lucas-main-category-20261018-lfm2.5-2.6b-v1-constrained-choice}"
MANIFEST_ROOT="$RUN_ROOT/manifests"
CONFIG_PATH="$CODE_ROOT/config/lucas-source-pools.json"
RUNNER_PATH="$CODE_ROOT/tools/lucas_main_category_inference.py"
JOB_TEMP_ROOT="${JOB_TMPDIR:-${TMPDIR:-/tmp}}"
SCRATCH_ROOT="$JOB_TEMP_ROOT/lucas-main-category-$OAR_JOB_ID"
RUNTIME_ROOT="$SCRATCH_ROOT/runtime"
UV_BIN="$RUNTIME_ROOT/bin/uv"
UV_VERSION="0.11.16"
RUNTIME_PYPROJECT_SHA256="88cf51fb7348346215aa892140babdb2dc4cc60a5e2af1cd71a829509cd8e632"
RUNTIME_UV_LOCK_SHA256="96fa2cb67852952919e0ed76c6e4c9a9478d419cca373b7ed33aea8dc6f1dd67"
EXPECTED_RUNNER_SHA256="6dd779a139b913a7cba0ea3b9a29525a651e5175530505d49ced011c0d490861"

for required_file in \
  "$CONFIG_PATH" \
  "$RUNNER_PATH" \
  "$MANIFEST_ROOT/description.json" \
  "$MANIFEST_ROOT/website.json" \
  "$MANIFEST_ROOT/combined-wiki.json" \
  "$RUNTIME_PROJECT_ROOT/pyproject.toml" \
  "$RUNTIME_PROJECT_ROOT/uv.lock"; do
  if [[ ! -f "$required_file" ]]; then
    echo "Required pinned input is missing: $required_file" >&2
    exit 2
  fi
done

if [[ "$(sha256sum "$RUNNER_PATH" | cut -d ' ' -f 1)" != "$EXPECTED_RUNNER_SHA256" ]]; then
  echo "Inference runner does not match its QA pin." >&2
  exit 2
fi

if [[ "$(sha256sum "$RUNTIME_PROJECT_ROOT/pyproject.toml" | cut -d ' ' -f 1)" != "$RUNTIME_PYPROJECT_SHA256" ]]; then
  echo "Inference runtime pyproject does not match its QA pin." >&2
  exit 2
fi
if [[ "$(sha256sum "$RUNTIME_PROJECT_ROOT/uv.lock" | cut -d ' ' -f 1)" != "$RUNTIME_UV_LOCK_SHA256" ]]; then
  echo "Inference runtime lock does not match its QA pin." >&2
  exit 2
fi

mkdir -p "$RUNTIME_ROOT/bin" "$RUNTIME_ROOT/python" "$RUNTIME_ROOT/uv-cache"
chmod 700 "$SCRATCH_ROOT" "$RUNTIME_ROOT"
if [[ ! -x "$UV_BIN" ]]; then
  installer="$RUNTIME_ROOT/uv-installer.sh"
  curl --proto '=https' --tlsv1.2 -LsSf \
    "https://astral.sh/uv/$UV_VERSION/install.sh" -o "$installer"
  UV_INSTALL_DIR="$RUNTIME_ROOT/bin" UV_NO_MODIFY_PATH=1 sh "$installer"
fi
if [[ "$($UV_BIN --version)" != "uv $UV_VERSION ("* ]]; then
  echo "Unexpected uv version at $UV_BIN" >&2
  exit 2
fi

export UV_PROJECT_ENVIRONMENT="$RUNTIME_ROOT/venv"
export UV_CACHE_DIR="$RUNTIME_ROOT/uv-cache"
export UV_PYTHON_INSTALL_DIR="$RUNTIME_ROOT/python"
export UV_INDEX_URL="https://pypi.org/simple"
export HF_HOME="$SCRATCH_ROOT/hf-home"
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export TOKENIZERS_PARALLELISM=false
export USE_TF=0
mkdir -p "$HF_HOME"

"$UV_BIN" sync \
  --project "$RUNTIME_PROJECT_ROOT" \
  --frozen \
  --extra inference \
  --no-dev \
  --python 3.12.13
PYTHON="$UV_PROJECT_ENVIRONMENT/bin/python"
"$UV_BIN" pip install --python "$PYTHON" --no-deps pyarrow==25.0.1

FQDN="$(hostname -f)"
NODE="$(hostname -s)"
SITE="${LUCAS_G5K_SITE:-${OAR_SITE:-}}"
if [[ -z "$SITE" ]]; then
  if [[ "$FQDN" =~ ^[^.]+\.([a-z0-9-]+)\.grid5000\.fr$ ]]; then
    SITE="${BASH_REMATCH[1]}"
  else
    echo "Cannot determine Grid5000 site from hostname; set LUCAS_G5K_SITE." >&2
    exit 2
  fi
fi
RUNTIME_INFO="$SCRATCH_ROOT/runtime-info.json"

"$PYTHON" - "$RUNTIME_INFO" "$RUNTIME_PROJECT_ROOT" "$UV_BIN" "$SITE" "$NODE" <<'PY'
import json
import os
import subprocess
import sys

import pyarrow
import torch
import transformers
from huggingface_hub import __version__ as hub_version

output_path, project_root, uv_bin, site, node = sys.argv[1:]
uv_output = subprocess.run(
    [uv_bin, "--version"], check=True, capture_output=True, text=True
).stdout.strip()
if not uv_output.startswith("uv 0.11.16 ("):
    raise SystemExit(f"Unexpected uv version: {uv_output}")
minimum_memory = 24 * 1024**3
if sys.version_info[:3] != (3, 12, 13):
    raise SystemExit(f"Unexpected Python version: {sys.version.split()[0]}")
if not torch.cuda.is_available():
    raise SystemExit("CUDA is unavailable in the allocated job")
if torch.__version__ != "2.14.0+cu130" or torch.version.cuda != "13.0":
    raise SystemExit("Unexpected pinned PyTorch/CUDA runtime")
if not torch.cuda.is_bf16_supported():
    raise SystemExit("The allocated GPU does not support BF16")
properties = torch.cuda.get_device_properties(0)
if properties.major < 8 or properties.total_memory < minimum_memory:
    raise SystemExit("The allocated GPU does not meet the pinned capability/memory floor")
if transformers.__version__ != "5.17.0":
    raise SystemExit(f"Unexpected Transformers version: {transformers.__version__}")
if hub_version != "1.31.0":
    raise SystemExit(f"Unexpected Hugging Face Hub version: {hub_version}")
if pyarrow.__version__ != "25.0.1":
    raise SystemExit(f"Unexpected PyArrow version: {pyarrow.__version__}")

import hashlib
from pathlib import Path

project = Path(project_root)
runtime_info = {
    "oar_job_id": os.environ["OAR_JOB_ID"],
    "site": site,
    "node": node,
    "uv": uv_output.split()[1],
    "python": sys.version.split()[0],
    "torch": torch.__version__,
    "cuda": torch.version.cuda,
    "transformers": transformers.__version__,
    "huggingface_hub": hub_version,
    "pyarrow": pyarrow.__version__,
    "gpu": properties.name,
    "compute_capability": f"{properties.major}.{properties.minor}",
    "memory_bytes": properties.total_memory,
    "bf16_supported": torch.cuda.is_bf16_supported(),
    "runtime_pyproject_sha256": hashlib.sha256(
        (project / "pyproject.toml").read_bytes()
    ).hexdigest(),
    "runtime_lock_sha256": hashlib.sha256(
        (project / "uv.lock").read_bytes()
    ).hexdigest(),
}
payload = json.dumps(runtime_info, sort_keys=True, indent=2) + "\n"
try:
    with open(output_path, "x", encoding="utf-8") as stream:
        stream.write(payload)
except FileExistsError:
    with open(output_path, encoding="utf-8") as stream:
        if stream.read() != payload:
            raise SystemExit("Existing allocation runtime metadata has changed")
print(json.dumps({key: value for key, value in runtime_info.items() if key != "memory_bytes"}, sort_keys=True))
PY
chmod 600 "$RUNTIME_INFO"

"$PYTHON" "$RUNNER_PATH" \
  --manifest-root "$MANIFEST_ROOT" \
  --config "$CONFIG_PATH" \
  --output-root "$RUN_ROOT/output" \
  --scratch-root "$SCRATCH_ROOT/source-and-model" \
  --runtime-project "$RUNTIME_PROJECT_ROOT" \
  --runtime-info "$RUNTIME_INFO"
