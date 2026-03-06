#!/usr/bin/env python3
"""
Set camera exposure and gains when the server is off.
Writes webapp/camera_config.json; the webapp and camera_live_stream.py use it on next run.

Run from repo root:
  python3 scripts/set_camera_config.py --exposure 100 --red-gain 4.0 --blue-gain 0.5
  python3 scripts/set_camera_config.py --exposure 150
  python3 scripts/set_camera_config.py --print   # show current saved values
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "webapp" / "camera_config.json"

DEFAULTS = {
    "exposure_ms": 100.0,
    "red_gain": 4.0,
    "blue_gain": 0.5,
    "analogue_gain": 4.0,
}


def main():
    parser = argparse.ArgumentParser(description="Set camera exposure and gains (saved to file, used when server/scripts run)")
    parser.add_argument("--exposure", type=float, default=None, metavar="MS", help="Exposure time in milliseconds")
    parser.add_argument("--red-gain", type=float, default=None, help="Red gain")
    parser.add_argument("--blue-gain", type=float, default=None, help="Blue gain")
    parser.add_argument("--analogue-gain", type=float, default=None, help="Analogue gain")
    parser.add_argument("--print", action="store_true", help="Print current saved config and exit")
    args = parser.parse_args()

    if args.print:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            for k, v in data.items():
                print(f"{k}: {v}")
        else:
            print("No saved config; using defaults:")
            for k, v in DEFAULTS.items():
                print(f"{k}: {v}")
        return 0

    # Merge with existing or defaults
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
        except Exception:
            data = DEFAULTS.copy()
    else:
        data = DEFAULTS.copy()

    if args.exposure is not None:
        data["exposure_ms"] = args.exposure
    if args.red_gain is not None:
        data["red_gain"] = args.red_gain
    if args.blue_gain is not None:
        data["blue_gain"] = args.blue_gain
    if args.analogue_gain is not None:
        data["analogue_gain"] = args.analogue_gain

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("Camera config saved to", CONFIG_FILE)
    for k, v in data.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
