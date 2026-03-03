#!/usr/bin/env python3
"""
Light control on Jetson – same physical pins as Pi frontend.
Pi uses BCM 22,23,24,25 = physical header pins 15,16,18,22. On Jetson the same
HAT/cable uses those same physical positions; BCM numbers can differ, so we
use BOARD (physical pin) numbering by default.
"""

import sys
import time
import argparse
import threading

# JetsonHacks Orin Nano J12: physical pins 15, 16, 18, 22 (same positions as Pi BCM 22,23,24,25).
BOARD_PINS = (15, 16, 18, 22)   # physical header pins: 15=GPIO12, 16=SPI1_CS1, 18=SPI1_CS0, 22=SPI1_MISO
BCM_PINS = (22, 23, 24, 25)     # Pi BCM convention for --bcm
PWM_FREQ_HZ = 300
PWM_PERIOD_S = 1.0 / PWM_FREQ_HZ

try:
    import Jetson.GPIO as GPIO
except ImportError:
    print("Jetson.GPIO not found. Install: pip install Jetson.GPIO")
    sys.exit(1)

# Set at init from --bcm flag
LIGHT_PINS = {1: BOARD_PINS[0], 2: BOARD_PINS[1], 3: BOARD_PINS[2], 4: BOARD_PINS[3]}
# Software PWM state: duty 0.0–1.0 per light, stop event for threads
light_duty = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
stop_events = {}
threads = {}
gpio_initialized = False
# Set by main() from --bcm; used when _init_gpio() is called without board_mode (e.g. from set_light).
use_board_mode = True


def _init_gpio(board_mode=None):
    global gpio_initialized, use_board_mode, LIGHT_PINS
    if board_mode is None:
        board_mode = use_board_mode
    if gpio_initialized:
        return
    use_board_mode = board_mode
    LIGHT_PINS = {
        1: BOARD_PINS[0] if board_mode else BCM_PINS[0],
        2: BOARD_PINS[1] if board_mode else BCM_PINS[1],
        3: BOARD_PINS[2] if board_mode else BCM_PINS[2],
        4: BOARD_PINS[3] if board_mode else BCM_PINS[3],
    }
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD if board_mode else GPIO.BCM)
    for pin in LIGHT_PINS.values():
        GPIO.setup(pin, GPIO.OUT)
        GPIO.output(pin, GPIO.LOW)
    gpio_initialized = True


def _soft_pwm_loop(light_id: int):
    """Run soft PWM for one light at 300 Hz."""
    pin = LIGHT_PINS[light_id]
    stop = stop_events[light_id]
    while not stop.is_set():
        duty = light_duty[light_id]
        if duty <= 0:
            GPIO.output(pin, GPIO.LOW)
            time.sleep(PWM_PERIOD_S)
        elif duty >= 1:
            GPIO.output(pin, GPIO.HIGH)
            time.sleep(PWM_PERIOD_S)
        else:
            on_time = PWM_PERIOD_S * duty
            off_time = PWM_PERIOD_S * (1.0 - duty)
            GPIO.output(pin, GPIO.HIGH)
            time.sleep(on_time)
            GPIO.output(pin, GPIO.LOW)
            time.sleep(off_time)
        time.sleep(0)  # yield
    GPIO.output(pin, GPIO.LOW)


def _start_soft_pwm(light_id: int):
    if light_id in threads and threads[light_id].is_alive():
        return
    stop_events[light_id] = threading.Event()
    t = threading.Thread(target=_soft_pwm_loop, args=(light_id,), daemon=True)
    threads[light_id] = t
    t.start()


def set_light(light_id: int, intensity: float):
    """Set one light. intensity 0.0–1.0."""
    if light_id not in (1, 2, 3, 4):
        raise ValueError("light_id must be 1, 2, 3, or 4")
    intensity = max(0.0, min(1.0, float(intensity)))
    _init_gpio()
    light_duty[light_id] = intensity
    _start_soft_pwm(light_id)


def turn_off_all():
    """Turn off all four lights."""
    _init_gpio()
    for lid in (1, 2, 3, 4):
        light_duty[lid] = 0.0
    time.sleep(PWM_PERIOD_S * 2)  # let threads set output low
    for lid in (1, 2, 3, 4):
        if lid in stop_events:
            stop_events[lid].set()
        pin = LIGHT_PINS[lid]
        try:
            GPIO.output(pin, GPIO.LOW)
        except Exception:
            pass
    for t in threads.values():
        if t.is_alive():
            t.join(timeout=0.1)
    threads.clear()
    stop_events.clear()


def cleanup():
    turn_off_all()
    global gpio_initialized
    if gpio_initialized:
        try:
            GPIO.cleanup()
        except Exception:
            pass
        gpio_initialized = False


def _on_state(active_low):
    return GPIO.LOW if active_low else GPIO.HIGH


def _off_state(active_low):
    return GPIO.HIGH if active_low else GPIO.LOW


def _simple_on(active_low=False):
    """Plain digital output on all four pins: HIGH=on (or LOW=on if active_low)."""
    _init_gpio()
    level = _on_state(active_low)
    for pin in LIGHT_PINS.values():
        GPIO.output(pin, level)


def _simple_off(active_low=False):
    """Plain digital output: LOW=off (or HIGH=off if active_low)."""
    _init_gpio()
    level = _off_state(active_low)
    for pin in LIGHT_PINS.values():
        GPIO.output(pin, level)


def _blink_test(cycles=3, interval=0.5, board_mode=True, active_low=False):
    """Blink each light in turn (1, 2, 3, 4) to identify which pin drives which light."""
    _init_gpio(board_mode=board_mode)
    mode = "BOARD (physical 15,16,18,22)" if board_mode else "BCM (22,23,24,25)"
    on_level = _on_state(active_low)
    off_level = _off_state(active_low)
    print(f"Blink test using {mode}, active_low={active_low}. Ctrl+C to stop.")
    try:
        for _ in range(cycles):
            for lid in (1, 2, 3, 4):
                pin = LIGHT_PINS[lid]
                GPIO.output(pin, on_level)
                print(f"  Light {lid} (pin {pin}) ON")
                time.sleep(interval)
                GPIO.output(pin, off_level)
                time.sleep(0.15)
    except KeyboardInterrupt:
        pass
    for pin in LIGHT_PINS.values():
        GPIO.output(pin, off_level)
    print("Blink test done.")


def _probe_test(board_mode=True):
    """Try each pin HIGH then LOW, then repeat inverted. Watch which polarity turns a light on."""
    _init_gpio(board_mode=board_mode)
    mode = "BOARD (physical 15,16,18,22)" if board_mode else "BCM (22,23,24,25)"
    print(f"Probe: driving each pin with {mode}. Watch when a light turns ON.")
    print("Phase 1: each pin HIGH for 2.5s then LOW. If a light is ON during HIGH, use normal (no --active-low).")
    for lid in (1, 2, 3, 4):
        pin = LIGHT_PINS[lid]
        for p in LIGHT_PINS.values():
            GPIO.output(p, GPIO.LOW)
        GPIO.output(pin, GPIO.HIGH)
        print(f"  Pin {pin} (light {lid}) = HIGH now (2.5s)")
        time.sleep(2.5)
        GPIO.output(pin, GPIO.LOW)
        time.sleep(0.5)
    print("Phase 2: each pin LOW for 2.5s then HIGH. If a light is ON during LOW, use --active-low.")
    for lid in (1, 2, 3, 4):
        pin = LIGHT_PINS[lid]
        for p in LIGHT_PINS.values():
            GPIO.output(p, GPIO.HIGH)
        GPIO.output(pin, GPIO.LOW)
        print(f"  Pin {pin} (light {lid}) = LOW now (2.5s)")
        time.sleep(2.5)
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(0.5)
    for pin in LIGHT_PINS.values():
        GPIO.output(pin, GPIO.LOW)
    print("Probe done. If you saw a light in Phase 1 use: simple_on. In Phase 2 use: simple_on --active-low.")


def main():
    parser = argparse.ArgumentParser(
        description="Control lights. Default: BOARD mode (physical pins 15,16,18,22 = same as Pi)."
    )
    parser.add_argument("action", choices=["on", "off", "set", "simple_on", "simple_off", "blink", "probe"],
                        help="on=soft PWM, off=off, set=one light, simple_on/simple_off=plain HIGH/LOW, blink=cycle, probe=try both polarities")
    parser.add_argument("--light", "-l", type=int, choices=[1, 2, 3, 4], help="Light 1–4 for 'set'")
    parser.add_argument("--intensity", "-i", type=float, default=1.0, help="0.0–1.0 for 'set' or 'on' (default 1.0)")
    parser.add_argument("--seconds", "-s", type=float, default=0, help="Turn on for N seconds then off (0 = leave on)")
    parser.add_argument("--bcm", action="store_true", help="Use BCM pin numbers (22,23,24,25) instead of BOARD (15,16,18,22)")
    parser.add_argument("--active-low", action="store_true", help="Lights turn ON when pin is LOW (e.g. common cathode / NPN driver)")
    parser.add_argument("--blink-cycles", type=int, default=3, help="Number of blink cycles for 'blink' (default 3)")
    args = parser.parse_args()

    global use_board_mode
    use_board_mode = not args.bcm
    board_mode = use_board_mode
    active_low = args.active_low

    try:
        if args.action == "blink":
            _blink_test(cycles=args.blink_cycles, interval=0.5, board_mode=board_mode, active_low=active_low)
            cleanup()
            return
        if args.action == "probe":
            _probe_test(board_mode=board_mode)
            cleanup()
            return
        _init_gpio(board_mode=board_mode)
        if args.action == "off":
            turn_off_all()
            print("All lights off.")
        elif args.action == "simple_off":
            _simple_off(active_low=active_low)
            print("All lights off (simple digital).")
            cleanup()
        elif args.action == "simple_on":
            _simple_on(active_low=active_low)
            print("All lights on (simple). Ctrl+C to turn off and exit." + (" [active-low]" if active_low else ""))
            if args.seconds > 0:
                time.sleep(args.seconds)
                _simple_off(active_low=active_low)
                cleanup()
                print("Lights off after delay.")
            else:
                try:
                    while True:
                        time.sleep(60)
                except KeyboardInterrupt:
                    _simple_off(active_low=active_low)
                    cleanup()
                    print("Lights off.")
        elif args.action == "on":
            for lid in (1, 2, 3, 4):
                set_light(lid, args.intensity)
            print(f"All lights on at {args.intensity:.0%}. (Process stays running; Ctrl+C to turn off and exit.)")
            if args.seconds > 0:
                time.sleep(args.seconds)
                turn_off_all()
                print("Lights off after delay.")
                cleanup()
            else:
                try:
                    while True:
                        time.sleep(60)
                except KeyboardInterrupt:
                    turn_off_all()
                    cleanup()
                    print("Lights off.")
        elif args.action == "set":
            if args.light is None:
                print("Use --light 1|2|3|4 with action 'set'")
                sys.exit(1)
            set_light(args.light, args.intensity)
            print(f"Light {args.light} at {args.intensity:.0%}. (Process stays running; Ctrl+C to turn off and exit.)")
            if args.seconds > 0:
                time.sleep(args.seconds)
                turn_off_all()
                cleanup()
                print("Lights off after delay.")
            else:
                try:
                    while True:
                        time.sleep(60)
                except KeyboardInterrupt:
                    turn_off_all()
                    cleanup()
                    print("Lights off.")
    finally:
        if args.action == "off":
            cleanup()


if __name__ == "__main__":
    main()
