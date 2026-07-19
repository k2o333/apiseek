#!/usr/bin/env bash
set -euo pipefail

project_dir="/root/projects/zhongzhuan"
service_file="/etc/systemd/system/aiapibank-monitor.service"

if [[ ! -f "${project_dir}/config.env" ]]; then
    echo "Missing ${project_dir}/config.env; create it from config.env.example first." >&2
    exit 1
fi

chmod 600 "${project_dir}/config.env"
/usr/bin/python3 -m venv "${project_dir}/.venv"
"${project_dir}/.venv/bin/python" -m pip install -r "${project_dir}/requirements.txt"
install -m 0644 "${project_dir}/aiapibank-monitor.service" "${service_file}"
systemctl daemon-reload
systemctl enable --now aiapibank-monitor.service
systemctl --no-pager --full status aiapibank-monitor.service
