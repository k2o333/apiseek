#!/usr/bin/env bash
set -euo pipefail

project_dir="/root/projects/zhongzhuan"
service_file="/etc/systemd/system/pinaic-monitor.service"

if [[ ! -f "${project_dir}/pinaic.env" ]]; then
    echo "Missing ${project_dir}/pinaic.env; create it from pinaic.env.example first." >&2
    exit 1
fi

chmod 600 "${project_dir}/pinaic.env"
/usr/bin/python3 -m venv "${project_dir}/.venv"
"${project_dir}/.venv/bin/python" -m pip install -r "${project_dir}/requirements.txt"
install -m 0644 "${project_dir}/pinaic-monitor.service" "${service_file}"
systemd-analyze verify "${service_file}"
systemctl daemon-reload
systemctl enable --now pinaic-monitor.service
systemctl --no-pager --full status pinaic-monitor.service
