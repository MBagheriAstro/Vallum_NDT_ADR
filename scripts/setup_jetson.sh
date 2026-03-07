#!/bin/bash
# Vallum-Jetson: one-time full setup on a Jetson (Orin Nano / Nano).
# Run once after cloning. Installs system packages, venv, PyTorch (GPU), and all app deps.
#
# Usage (from repo root):
#   ./scripts/setup_jetson.sh
#
# You will be prompted for sudo for apt. Then everything else is automatic.

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
WHEELS_DIR="${REPO_ROOT}/.jetson_wheels"
mkdir -p "$WHEELS_DIR"

# PyTorch 2.3 + torchvision 0.18 for JetPack 6 (L4T R36.x), Python 3.10, CUDA 12.4
TORCH_WHL_URL="https://nvidia.box.com/shared/static/zvultzsmd4iuheykxy17s4l2n91ylpl8.whl"
TV_WHL_URL="https://nvidia.box.com/shared/static/u0ziu01c0kyji4zz3gxam79181nebylf.whl"

echo "=============================================="
echo "  Vallum-Jetson – full setup (Jetson)"
echo "=============================================="
echo "Repo: $REPO_ROOT"
echo ""

# -----------------------------------------------------------------------------
# 1. System packages (requires sudo)
# -----------------------------------------------------------------------------
echo "Step 1/5: Installing system packages (sudo required once)..."
sudo apt-get update
sudo apt-get install -y \
  python3-pip \
  python3.10-venv \
  libopenblas-dev \
  libjpeg-dev \
  zlib1g-dev \
  libgpiod-utils \
  i2c-tools
echo "  System packages OK."
echo ""

# -----------------------------------------------------------------------------
# 2. Virtual environment
# -----------------------------------------------------------------------------
echo "Step 2/5: Creating virtual environment..."
if [ ! -f "venv/bin/activate" ]; then
  [ -d "venv" ] && rm -rf venv
  python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
echo "  Venv OK: $REPO_ROOT/venv"
echo ""

# -----------------------------------------------------------------------------
# 3. PyTorch + torchvision (NVIDIA wheels for GPU)
# -----------------------------------------------------------------------------
echo "Step 3/5: Installing PyTorch and torchvision (Jetson GPU wheels)..."
TORCH_WHL="${WHEELS_DIR}/torch-2.3.0-cp310-cp310-linux_aarch64.whl"
if [ ! -f "$TORCH_WHL" ]; then
  echo "  Downloading PyTorch 2.3.0..."
  wget -q --show-progress -O "$TORCH_WHL" "$TORCH_WHL_URL" || {
    echo "  ERROR: PyTorch wheel download failed. Check network and NVIDIA box.com."
    exit 1
  }
fi
pip install "$TORCH_WHL"
echo "  PyTorch OK."

TV_WHL="${WHEELS_DIR}/torchvision-0.18.0-cp310-cp310-linux_aarch64.whl"
TV_WHL_OLD="${WHEELS_DIR}/torchvision-0.18.0-cp310-linux_aarch64.whl"
[ ! -f "$TV_WHL" ] && [ -f "$TV_WHL_OLD" ] && mv "$TV_WHL_OLD" "$TV_WHL"
if [ ! -f "$TV_WHL" ]; then
  echo "  Downloading torchvision 0.18.0..."
  wget -q --show-progress -O "$TV_WHL" "$TV_WHL_URL" || true
fi
[ -f "$TV_WHL" ] && pip install "$TV_WHL" && echo "  torchvision OK."
echo ""

# -----------------------------------------------------------------------------
# 4. All Python dependencies from requirements.txt
# -----------------------------------------------------------------------------
echo "Step 4/5: Installing Python dependencies (requirements.txt)..."
pip install -r requirements.txt
echo "  requirements.txt OK."
echo ""

# -----------------------------------------------------------------------------
# 5. Verify
# -----------------------------------------------------------------------------
echo "Step 5/5: Verifying..."
venv/bin/python -c "
import torch
cuda = torch.cuda.is_available()
print('  torch:', torch.__version__, '| CUDA available:', cuda)
from ultralytics import YOLO
print('  ultralytics: OK')
import cv2
print('  opencv:', cv2.__version__)
import fastapi
print('  fastapi: OK')
"
if [ -f "$REPO_ROOT/webapp/models/best.pt" ]; then
  echo "  Model: webapp/models/best.pt (found)"
else
  echo "  WARNING: webapp/models/best.pt not found. Copy the YOLO model there for inspection."
fi
echo ""

echo "=============================================="
echo "  Setup complete"
echo "=============================================="
echo ""
echo "Run the webapp:"
echo "  cd $REPO_ROOT/webapp && ../venv/bin/python main.py"
echo ""
echo "Or activate the venv first:"
echo "  source $REPO_ROOT/venv/bin/activate"
echo "  cd webapp && python main.py"
echo ""
echo "Optional: enable I2C for Motor HAT (Jetson-IO or raspi-config style)."
echo "Check HAT: sudo i2cdetect -y 1  # expect 60 (PCA9685)"
echo ""
