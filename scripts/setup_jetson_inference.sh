#!/bin/bash
# Vallum-Jetson: GPU inference setup on Jetson (Orin Nano).
# Matches VALLUM-NDT-ADR-BACKEND/setup_jetson.sh versions:
#   PyTorch 2.3.0 + torchvision 0.18.0 for JetPack 6 (L4T R36.x), Python 3.10, CUDA 12.4
# Run from repo root: ./scripts/setup_jetson_inference.sh
#
# One-time system deps (run with sudo if needed):
#   sudo apt-get update && sudo apt-get install -y python3-pip python3.10-venv libopenblas-dev libjpeg-dev zlib1g-dev

set -e
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
WHEELS_DIR="${REPO_ROOT}/.jetson_wheels"
mkdir -p "$WHEELS_DIR"

# Same wheel URLs as VALLUM-NDT-ADR-BACKEND/setup_jetson.sh
TORCH_WHL_URL="https://nvidia.box.com/shared/static/zvultzsmd4iuheykxy17s4l2n91ylpl8.whl"
TV_WHL_URL="https://nvidia.box.com/shared/static/u0ziu01c0kyji4zz3gxam79181nebylf.whl"

echo "=== Vallum-Jetson GPU inference setup (Jetson) ==="
echo "Python: $(python3 --version)"
echo "Repo: $REPO_ROOT"
echo ""

if ! python3 -m pip --version &>/dev/null; then
    echo "ERROR: pip not found. Install first:"
    echo "  sudo apt-get update && sudo apt-get install -y python3-pip python3.10-venv"
    exit 1
fi

USE_VENV=0
if python3 -c "import venv" 2>/dev/null; then
    if [ ! -f "venv/bin/activate" ]; then
        [ -d "venv" ] && rm -rf venv
        echo "Creating virtual environment..."
        python3 -m venv venv
    fi
    if [ -f "venv/bin/activate" ]; then
        echo "Activating venv..."
        source venv/bin/activate
        USE_VENV=1
        PYTHON="venv/bin/python"
    else
        PYTHON="python3"
    fi
else
    echo "WARNING: python3-venv not found. Install: sudo apt-get install -y python3.10-venv"
    PYTHON="python3"
fi

[ "$USE_VENV" = 1 ] && pip install --upgrade pip || python3 -m pip install --upgrade pip --user

# Download PyTorch wheel
TORCH_WHL="${WHEELS_DIR}/torch-2.3.0-cp310-cp310-linux_aarch64.whl"
if [ ! -f "$TORCH_WHL" ]; then
    echo "Downloading PyTorch 2.3.0 wheel for Jetson..."
    wget -q --show-progress -O "$TORCH_WHL" "$TORCH_WHL_URL" || {
        echo "Download failed. Get it from NVIDIA PyTorch for Jetson documentation."
        exit 1
    }
fi
echo "Installing PyTorch..."
[ "$USE_VENV" = 1 ] && pip install "$TORCH_WHL" || python3 -m pip install --user "$TORCH_WHL"

# Download and install torchvision
TV_WHL="${WHEELS_DIR}/torchvision-0.18.0-cp310-cp310-linux_aarch64.whl"
TV_WHL_OLD="${WHEELS_DIR}/torchvision-0.18.0-cp310-linux_aarch64.whl"
if [ ! -f "$TV_WHL" ] && [ -f "$TV_WHL_OLD" ]; then
    mv "$TV_WHL_OLD" "$TV_WHL"
fi
if [ ! -f "$TV_WHL" ]; then
    echo "Downloading torchvision 0.18.0 wheel..."
    wget -q --show-progress -O "$TV_WHL" "$TV_WHL_URL" || true
fi
if [ -f "$TV_WHL" ]; then
    [ "$USE_VENV" = 1 ] && pip install "$TV_WHL" || python3 -m pip install --user "$TV_WHL"
fi

# Match backend version constraints (setup_jetson.sh)
echo "Installing ultralytics, opencv-python, Pillow, numpy..."
[ "$USE_VENV" = 1 ] && pip install "ultralytics>=8.0.0" "opencv-python>=4.5.0" "Pillow>=9.0.0" "numpy>=1.21.0" \
    || python3 -m pip install --user "ultralytics>=8.0.0" "opencv-python>=4.5.0" "Pillow>=9.0.0" "numpy>=1.21.0"

# Install dashboard and motor deps (requirements.txt has inference deps; we already installed matching versions above)
echo "Installing dashboard and motor dependencies..."
DASH_DEPS="fastapi uvicorn[standard] jinja2 pydantic Jetson.GPIO adafruit-blinka Adafruit-PlatformDetect adafruit-circuitpython-motorkit adafruit-circuitpython-pca9685 adafruit-circuitpython-motor"
if [ "$USE_VENV" = 1 ]; then
    pip install $DASH_DEPS
else
    python3 -m pip install --user $DASH_DEPS
fi

echo ""
echo "Verifying GPU inference..."
"$PYTHON" -c "
import torch
print('  torch:', torch.__version__, '| CUDA available:', torch.cuda.is_available())
from ultralytics import YOLO
print('  ultralytics: OK')
import cv2
print('  opencv:', cv2.__version__)
"
MODEL_PATH="webapp/models/best.pt"
if [ -f "$REPO_ROOT/$MODEL_PATH" ]; then
    echo "  Model: $MODEL_PATH (found)"
else
    echo "  WARNING: Model not found at $MODEL_PATH - copy best.pt there for inspection."
fi
echo ""
echo "=== Setup complete ==="
echo "Run the webapp: cd $REPO_ROOT/webapp && $PYTHON main.py"
echo ""
