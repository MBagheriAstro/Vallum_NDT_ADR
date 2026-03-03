#!/usr/bin/env python3
"""
Simple M1–M4 motor control for Adafruit DC/Stepper Motor HAT on Jetson (Orin Nano / Nano).
Uses the same MotorKit API as the Pi frontend: motor1..motor4, throttle in [-1, 1].

Usage:
  python control_motors.py m2 extend [duration_sec]
  python control_motors.py m2 retract [duration_sec]
  python control_motors.py m2 stop
  python control_motors.py status
  python control_motors.py interactive   # REPL: m1 extend, m3 retract, stop, status, quit
"""

import sys
import time
import argparse

try:
    from adafruit_motorkit import MotorKit
except ImportError as e:
    print("Error: Install packages first: pip install -r requirements.txt")
    print("(Requires adafruit-blinka and adafruit-circuitpython-motorkit)")
    sys.exit(1)

# Default I2C address for Adafruit Motor HAT (PCA9685)
kit = None


def get_kit():
    global kit
    if kit is None:
        try:
            kit = MotorKit()
        except Exception as e:
            print(f"MotorKit init failed: {e}")
            print("Check: I2C enabled, HAT on 40-pin header, address 0x60 (e.g. sudo i2cdetect -y 1)")
            raise
    return kit


def motor_by_name(name):
    """name: m1, m2, m3, m4 (case-insensitive)."""
    k = get_kit()
    n = name.strip().upper().replace("M", "")
    if n not in ("1", "2", "3", "4"):
        raise ValueError(f"Motor must be m1, m2, m3, or m4; got {name}")
    return getattr(k, f"motor{int(n)}")


def run(motor_name, action, duration_sec=2.0):
    motor_name = motor_name.strip().lower()
    motor = motor_by_name(motor_name)
    if action == "extend":
        motor.throttle = -1.0
    elif action == "retract":
        motor.throttle = 1.0
    elif action == "stop":
        motor.throttle = 0.0
        return
    else:
        raise ValueError(f"Action must be extend, retract, or stop; got {action}")
    try:
        time.sleep(duration_sec)
    finally:
        motor.throttle = 0.0


def stop_all():
    k = get_kit()
    for i in range(1, 5):
        getattr(k, f"motor{i}").throttle = 0.0
    print("All motors stopped.")


def status():
    k = get_kit()
    print("M1–M4 (MotorKit): ready (throttle not read back by library; assume 0 when idle).")


def interactive_loop():
    print("Commands: m1|m2|m3|m4 extend [sec] | retract [sec] | stop  |  stopall | status | quit")
    get_kit()
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            stop_all()
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        if cmd == "quit" or cmd == "q":
            stop_all()
            break
        if cmd == "stopall":
            stop_all()
            continue
        if cmd == "status":
            status()
            continue
        if len(parts) < 2:
            print("Usage: m1 extend [sec] | m2 retract [sec] | m3 stop")
            continue
        motor_name = parts[0]
        action = parts[1].lower()
        duration = 2.0
        if len(parts) >= 3 and action in ("extend", "retract"):
            try:
                duration = float(parts[2])
            except ValueError:
                pass
        if motor_name.lower() not in ("m1", "m2", "m3", "m4"):
            print("Motor must be m1, m2, m3, or m4")
            continue
        if action not in ("extend", "retract", "stop"):
            print("Action must be extend, retract, or stop")
            continue
        try:
            run(motor_name, action, duration if action != "stop" else 0)
            print(f"Done: {motor_name} {action}" + (f" {duration}s" if action != "stop" else ""))
        except Exception as e:
            print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Control M1–M4 on Adafruit Motor HAT (Jetson)")
    parser.add_argument("motor", nargs="?", help="m1, m2, m3, m4")
    parser.add_argument("action", nargs="?", choices=["extend", "retract", "stop"], help="extend | retract | stop")
    parser.add_argument("duration", nargs="?", type=float, default=2.0, help="Duration in seconds (default 2.0)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive REPL")
    parser.add_argument("--status", action="store_true", help="Print status and exit")
    args = parser.parse_args()

    if args.status:
        get_kit()
        status()
        return
    if args.interactive:
        interactive_loop()
        return
    if not args.motor or not args.action:
        parser.print_help()
        print("\nExamples:")
        print("  python control_motors.py m2 extend 1.5")
        print("  python control_motors.py m2 retract")
        print("  python control_motors.py m2 stop")
        print("  python control_motors.py -i")
        sys.exit(0)
    run(args.motor, args.action, args.duration if args.action != "stop" else 0)
    print(f"Done: {args.motor} {args.action}" + (f" {args.duration}s" if args.action != "stop" else ""))


if __name__ == "__main__":
    main()
