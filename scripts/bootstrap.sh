#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
venv_dir="${project_root}/.venv"
python_bin="${PYTHON_BIN:-python3}"

python_version="$("${python_bin}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "${python_version}" != "3.12" ]]; then
    echo "Python 3.12 is required, found ${python_version} at ${python_bin}" >&2
    exit 1
fi

if [[ ! -x "${venv_dir}/bin/python" ]]; then
    "${python_bin}" -m venv "${venv_dir}"
fi

venv_python_version="$("${venv_dir}/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "${venv_python_version}" != "3.12" ]]; then
    echo "Existing virtual environment uses Python ${venv_python_version}; recreate .venv with Python 3.12" >&2
    exit 1
fi

"${venv_dir}/bin/python" -m pip install --disable-pip-version-check \
    -r "${project_root}/requirements.txt"

echo "Environment is ready: ${venv_dir}"
