#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_dir="${repo_root}/.devcontainer"

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"
fi

cd "${repo_root}"

uv sync --project "${project_dir}"

"${project_dir}/.venv/bin/python" -m ipykernel install \
    --user \
    --name workoutdata-devcontainer \
    --display-name "Python (workoutdata devcontainer)"

echo "Devcontainer Python environment is ready."
echo "Use: uv run --project .devcontainer python --version"
