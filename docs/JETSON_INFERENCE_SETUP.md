# GPU inference on Jetson (Orin Nano)

For **GPU-accelerated YOLO inference** during inspection, use the same stack as the old backend so versions match and you avoid import/ABI issues.

## One-time system packages

```bash
sudo apt-get update && sudo apt-get install -y python3-pip python3.10-venv libopenblas-dev libjpeg-dev zlib1g-dev
```

## Run the setup script

From **Vallum-Jetson** repo root:

```bash
cd /path/to/Vallum-Jetson
chmod +x scripts/setup_jetson_inference.sh
./scripts/setup_jetson_inference.sh
```

This script:

- Creates a venv (if `python3-venv` is installed).
- Downloads and installs **PyTorch 2.3.0** and **torchvision 0.18.0** from NVIDIA wheels for JetPack 6 (L4T R36.x), Python 3.10, CUDA 12.4 (same URLs as `VALLUM-NDT-ADR-BACKEND/setup_jetson.sh`).
- Installs **ultralytics>=8.0.0**, **opencv-python>=4.5.0**, **Pillow>=9.0.0**, **numpy>=1.21.0** to match the backend.
- Installs the rest of Vallum-Jetson (FastAPI, Motor HAT, etc.).

Wheels are cached in `.jetson_wheels/` so reruns are quick.

## Verify GPU

With the venv activated:

```bash
source venv/bin/activate
python3 -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

You should see `CUDA: True` if the JetPack CUDA stack and the PyTorch wheel match.

## Run the webapp

```bash
cd webapp && ../venv/bin/python main.py
# or: source ../venv/bin/activate && python main.py
```

Inspection will use the YOLO model at `webapp/models/best.pt` with GPU inference when CUDA is available.

## Version alignment with backend

| Package       | Backend (setup_jetson.sh) | Vallum-Jetson script   |
|---------------|----------------------------|-------------------------|
| PyTorch       | 2.3.0 (NVIDIA wheel)       | Same                    |
| torchvision   | 0.18.0 (NVIDIA wheel)      | Same                    |
| ultralytics   | >=8.0.0                    | >=8.0.0                 |
| opencv-python | >=4.5.0                     | >=4.5.0                 |
| Pillow        | >=9.0.0                     | >=9.0.0                 |
| numpy         | >=1.21.0                    | >=1.21.0                |

If you hit version mismatches (e.g. after a JetPack upgrade), compare with `VALLUM-NDT-ADR-BACKEND/JETSON_SETUP_GUIDE.md` and `VALLUM-NDT-ADR-BACKEND/setup_jetson.sh` and update the wheel URLs or constraints as needed.
