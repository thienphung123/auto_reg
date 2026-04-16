#!/bin/sh
set -eu

# 1. KHỞI TẠO MÀN HÌNH ẢO (Xvfb) - Bước sống còn để chạy trên Cloud
# Tạo màn hình tàng hình số :99 để trình duyệt ảo có chỗ hoạt động
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99

# 2. GIỮ NGUYÊN PHẦN QUẢN LÝ THƯ MỤC CỦA TÁC GIẢ
APP_DIR="/app"
RUNTIME_DIR="${APP_RUNTIME_DIR:-/runtime}"

mkdir -p "${RUNTIME_DIR}" "${RUNTIME_DIR}/logs" "${RUNTIME_DIR}/smstome_used"
touch \
  "${RUNTIME_DIR}/account_manager.db" \
  "${RUNTIME_DIR}/smstome_all_numbers.txt" \
  "${RUNTIME_DIR}/smstome_uk_deep_numbers.txt" \
  "${RUNTIME_DIR}/logs/solver.log"

ln -sfn "${RUNTIME_DIR}/account_manager.db" "${APP_DIR}/account_manager.db"
ln -sfn "${RUNTIME_DIR}/smstome_used" "${APP_DIR}/smstome_used"
ln -sfn "${RUNTIME_DIR}/smstome_all_numbers.txt" "${APP_DIR}/smstome_all_numbers.txt"
ln -sfn "${RUNTIME_DIR}/smstome_uk_deep_numbers.txt" "${APP_DIR}/smstome_uk_deep_numbers.txt"
ln -sfn "${RUNTIME_DIR}/logs/solver.log" "${APP_DIR}/services/turnstile_solver/solver.log"

# 3. CHẠY ỨNG DỤNG CHÍNH
exec python main.py
