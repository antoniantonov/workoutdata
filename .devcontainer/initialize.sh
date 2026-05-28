#!/usr/bin/env bash
set -euo pipefail

copy_if_missing() {
    local source_file="$1"
    local target_file="$2"

    if [[ -f "${target_file}" ]]; then
        return
    fi

    if [[ -f "${source_file}" ]]; then
        cp "${source_file}" "${target_file}"
        echo "Created ${target_file} from ${source_file}; edit it with local values."
    else
        touch "${target_file}"
        echo "Created empty ${target_file}; no sample file was found."
    fi
}

copy_if_missing "notebooks/.env.sample" "notebooks/.env"
copy_if_missing "jobs/import/.env.example" "jobs/import/.env"
