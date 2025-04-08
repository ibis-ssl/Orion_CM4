#!/bin/bash
echo "install python packages"
sudo apt install python3-fastapi python3-uvicorn

set -e

# 移動
cd "$(dirname "$0")"

SERVICE_NAME=control_server.service
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

# systemdサービスファイルを配置
echo "Installing systemd service..."
sudo cp $SERVICE_NAME $SERVICE_PATH

# systemdにサービスを登録・有効化
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME

echo "Setup complete. Service is now running."
