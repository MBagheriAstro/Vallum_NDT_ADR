# Standalone scripts (removed)

The standalone CLI scripts **`control_motors.py`**, **`control_lights.py`**, and **`jetson_lights_gpiod.py`** have been removed from the repository. The **webapp is the only entry point** for the Vallum Jetson application.

- **Lights and motors:** Use the webapp **Manual Control** tab to test lights, actuators, kick motor, and flip motor.
- **Hardware layout and pin mapping:** See [ARCHITECTURE.md](ARCHITECTURE.md) and the `webapp/config.py` / `webapp/hardware/` modules.

Run the dashboard with: `cd webapp && python3 main.py`
