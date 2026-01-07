#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MLRUNS_DIR="${SCRIPT_DIR}/mlruns"

usage() {
  cat <<'EOF'
Usage:
  ./pack_mlruns_linux.sh <experiment_id> <run_id> [output_dir]

Default output_dir: <this_script_dir>/models
Example:
  ./pack_mlruns_linux.sh 1 f5dcb44fae5e4f7facc1c88facb6db0b
  ./pack_mlruns_linux.sh -h
EOF
}

for arg in "$@"; do
  if [[ "${arg}" == "-h" || "${arg}" == "--help" ]]; then
    usage
    exit 0
  fi
done

EXP_ID="${1:-}"
RUN_ID="${2:-}"
OUT_DIR="${3:-${SCRIPT_DIR}/models}"

if [[ -z "${EXP_ID}" || -z "${RUN_ID}" ]]; then
  usage
  exit 1
fi

META_FILE="${MLRUNS_DIR}/${EXP_ID}/meta.yaml"
RUN_DIR="${MLRUNS_DIR}/${EXP_ID}/${RUN_ID}"

if [[ ! -f "${META_FILE}" ]]; then
  echo "ERROR: meta.yaml not found: ${META_FILE}" >&2
  exit 1
fi
if [[ ! -d "${RUN_DIR}" ]]; then
  echo "ERROR: run directory not found: ${RUN_DIR}" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"
OUT_FILE="${OUT_DIR}/qlib_run_${EXP_ID}_${RUN_ID}.tar.gz"

if [[ -f "${OUT_FILE}" && "${OVERWRITE:-0}" != "1" ]]; then
  echo "ERROR: output exists: ${OUT_FILE}" >&2
  echo "Set OVERWRITE=1 to replace it." >&2
  exit 1
fi

tar -czf "${OUT_FILE}" -C "${SCRIPT_DIR}" \
  "mlruns/${EXP_ID}/meta.yaml" \
  "mlruns/${EXP_ID}/${RUN_ID}"

echo "Packaged: ${OUT_FILE}"
echo "Next: copy this tar.gz to Windows and run unpack_mlruns_windows.ps1."
