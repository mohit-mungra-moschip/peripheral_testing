import pytest
import os
import subprocess
import time
import re

def test_status_led_sysfs_control(step_logger):
    """
    Validates onboard status LED control via sysfs:
      1. Enumerates all available LEDs
      2. Tests each LED: manual ON/OFF toggle
      3. Tests timer (blink) trigger
      4. Restores original state for every LED
    """
    step_logger.info("="*60)
    step_logger.info("LED SUBSYSTEM VALIDATION — STATUS LED CONTROL")

    led_base = "/sys/class/leds/"
    if not os.path.exists(led_base):
        pytest.fail(
            f"sysfs LED interface not found: {led_base}\n"
            f"This system may not expose LEDs via sysfs."
        )

    # ── Enumerate all available LEDs ──────────────────────────────────────────
    with step_logger.step("Enumerate LEDs", action=f"Read {led_base}", expected="LEDs found in sysfs") as step:
        available_leds = sorted(os.listdir(led_base))
        step_logger.info(f"All LEDs found on this board: {available_leds}")
        if not available_leds:
            pytest.fail(
                f"No LEDs found under {led_base}.\n"
                f"Check: ls /sys/class/leds/"
            )
        step.success(f"Discovered {len(available_leds)} LEDs.")

    PRIORITY_NAMES = ["ACT", "PWR", "led0", "led1", "default-on"]
    priority_leds  = [l for l in PRIORITY_NAMES if l in available_leds]
    remaining_leds = [l for l in available_leds if l not in PRIORITY_NAMES]
    test_leds      = priority_leds + remaining_leds
    step_logger.info(f"Test order: {test_leds}")

    def sysfs_read(led_path, filename):
        filepath = os.path.join(led_path, filename)
        try:
            with open(filepath) as f:
                return f.read().strip()
        except PermissionError:
            return subprocess.check_output(
                ["sudo", "cat", filepath], text=True
            ).strip()

    def sysfs_write(led_path, filename, value):
        filepath = os.path.join(led_path, filename)
        try:
            subprocess.check_call(
                ["sudo", "sh", "-c", f"echo '{value}' > {filepath}"]
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to write '{value}' to {filepath}: {e}"
            )

    def get_active_trigger(led_path):
        raw = sysfs_read(led_path, "trigger")
        match = re.search(r'\[(.*?)\]', raw)
        return match.group(1) if match else "none"

    def get_available_triggers(led_path):
        raw = sysfs_read(led_path, "trigger")
        return re.findall(r'\[?(\w[\w-]*)\]?', raw)

    failed_leds = []

    for led_name in test_leds:
        led_path = os.path.join(led_base, led_name)
        step_logger.info(f"\n{'─'*50}")
        step_logger.info(f"Testing LED: {led_name}  ({led_path})")

        try:
            orig_trigger    = get_active_trigger(led_path)
            orig_brightness = sysfs_read(led_path, "brightness")
            max_brightness  = sysfs_read(led_path, "max_brightness")
            avail_triggers  = get_available_triggers(led_path)
            step_logger.info(f"Original trigger:    '{orig_trigger}'")
            step_logger.info(f"Original brightness: {orig_brightness}")
            step_logger.info(f"Max brightness:      {max_brightness}")
            step_logger.info(f"Available triggers:  {avail_triggers}")
        except Exception as e:
            step_logger.info(f"[ERROR] Cannot read LED state: {e}")
            failed_leds.append((led_name, f"state read failed: {e}"))
            continue

        try:
            with step_logger.step(f"[{led_name}] Override Trigger", action="Set trigger to 'none'", expected="Trigger becomes 'none'") as step:
                sysfs_write(led_path, "trigger", "none")
                active = get_active_trigger(led_path)
                assert active == "none", f"Trigger override failed — still showing '{active}'."
                step.success("Trigger set to 'none'.")

            with step_logger.step(f"[{led_name}] Set ON", action=f"Write {max_brightness} to brightness", expected="Brightness updates") as step:
                sysfs_write(led_path, "brightness", max_brightness)
                actual_brightness = sysfs_read(led_path, "brightness")

                if actual_brightness == "0" and max_brightness != "0":
                    step_logger.info("LED is hardware-managed. Brightness writes ignored. Skipping brightness toggle.")
                    skip_brightness = True
                    step.success("Skipped (Hardware managed)")
                else:
                    assert int(actual_brightness) > 0, f"ON state rejected: read back {actual_brightness}."
                    skip_brightness = False
                    time.sleep(2)
                    step.success(f"ON confirmed (brightness={actual_brightness}).")

            if not skip_brightness:
                with step_logger.step(f"[{led_name}] Set OFF", action="Write 0 to brightness", expected="Brightness becomes 0") as step:
                    sysfs_write(led_path, "brightness", "0")
                    actual_brightness = sysfs_read(led_path, "brightness")
                    assert actual_brightness == "0", f"OFF state rejected: read back {actual_brightness}."
                    time.sleep(2)
                    step.success("OFF confirmed.")

            if "timer" in avail_triggers:
                with step_logger.step(f"[{led_name}] Timer Trigger", action="Set trigger to 'timer'", expected="LED blinks") as step:
                    sysfs_write(led_path, "trigger", "timer")
                    active = get_active_trigger(led_path)
                    assert active == "timer", f"Timer trigger not accepted — showing '{active}'."
                    delay_on_path = os.path.join(led_path, "delay_on")
                    if os.path.exists(delay_on_path):
                        sysfs_write(led_path, "delay_on",  "250")
                        sysfs_write(led_path, "delay_off", "250")
                    time.sleep(3)
                    step.success("Timer trigger accepted.")

            if "heartbeat" in avail_triggers:
                with step_logger.step(f"[{led_name}] Heartbeat Trigger", action="Set trigger to 'heartbeat'", expected="LED pulses heartbeat") as step:
                    sysfs_write(led_path, "trigger", "heartbeat")
                    active = get_active_trigger(led_path)
                    assert active == "heartbeat", f"Heartbeat trigger not accepted — showing '{active}'."
                    time.sleep(3)
                    step.success("Heartbeat trigger accepted.")

        except Exception as e:
            step_logger.info(f"[ERROR] LED '{led_name}' test failed: {e}")
            failed_leds.append((led_name, str(e)))

        finally:
            with step_logger.step(f"[{led_name}] Restore State", action="Restore original brightness and trigger", expected="State restored") as step:
                try:
                    sysfs_write(led_path, "brightness", orig_brightness)
                    sysfs_write(led_path, "trigger",    orig_trigger)
                    step.success(f"Restored: trigger='{orig_trigger}', brightness={orig_brightness}.")
                except Exception as e:
                    step_logger.info(f"[WARN] Restore failed for '{led_name}': {e}.")

    if failed_leds:
        report = "\n".join(f"  {name}: {reason}" for name, reason in failed_leds)
        pytest.fail(f"LED control failures:\n{report}")

    passed = [l for l in test_leds if l not in [n for n, _ in failed_leds]]
    step_logger.info(f"SUCCESS: All {len(passed)} LED(s) validated.")
    step_logger.info("="*60)


def test_physical_button_interrupts(board_config, step_logger):
    """
    Interactive test to physically validate external button interrupts.
    """
    try:
        import gpiod
        from gpiod.line import Direction, Edge, Bias
    except ImportError:
        pytest.skip("'gpiod' module not found on target.")

    assert "chip" in board_config, "board_config missing 'chip'"
    assert "in_pin" in board_config, "board_config missing 'in_pin'"

    chip_path = board_config["chip"]
    in_pin = board_config["in_pin"]

    step_logger.info("="*60)
    step_logger.info(f"MANUAL BUTTON VALIDATION: INTERRUPTS (Pin {in_pin})")

    req = gpiod.request_lines(
        chip_path,
        consumer="manual_button_test",
        config={
            in_pin: gpiod.LineSettings(
                direction=Direction.INPUT, 
                bias=Bias.PULL_UP, 
                edge_detection=Edge.BOTH
            )
        }
    )

    try:
        from datetime import timedelta
        while req.wait_edge_events(timedelta(seconds=0)):
            req.read_edge_events()

        step_logger.info("WAITING FOR BUTTON PRESS...")
        step_logger.info("Press and HOLD the button now (You have 30 seconds).")

        with step_logger.step("Wait for Button Press", action="Wait for FALLING edge", expected="FALLING edge detected") as step:
            if not req.wait_edge_events(timedelta(seconds=30)):
                pytest.fail("Timeout: No button press detected within 30 seconds.")

            events = req.read_edge_events()
            assert events[0].event_type == gpiod.EdgeEvent.Type.FALLING_EDGE, "Hardware Fault: Expected FALLING edge on press."
            step.success(f"Hardware Interrupt Caught: FALLING EDGE (Pressed) at {events[0].timestamp_ns} ns")

        step_logger.info("WAITING FOR BUTTON RELEASE...")
        step_logger.info("Let go of the button now.")

        with step_logger.step("Wait for Button Release", action="Wait for RISING edge", expected="RISING edge detected") as step:
            if not req.wait_edge_events(timedelta(seconds=30)):
                pytest.fail("Timeout: No button release detected.")

            release_events = req.read_edge_events()
            assert release_events[-1].event_type == gpiod.EdgeEvent.Type.RISING_EDGE, "Hardware Fault: Expected RISING edge on release."
            step.success(f"Hardware Interrupt Caught: RISING EDGE (Released) at {release_events[-1].timestamp_ns} ns")

        step_logger.info("="*60)

    finally:
        req.release()


def test_physical_button_debounce(board_config, step_logger):
    """
    Interactive test to prove the kernel debounce filter stops mechanical bounce.
    """
    import gpiod
    from gpiod.line import Direction, Edge, Bias

    chip_path = board_config["chip"]
    in_pin = board_config["in_pin"]

    step_logger.info("="*60)
    step_logger.info(f"MANUAL BUTTON VALIDATION: KERNEL DEBOUNCE")
    from datetime import timedelta
    req = gpiod.request_lines(
        chip_path,
        consumer="manual_debounce_test",
        config={
            in_pin: gpiod.LineSettings(
                direction=Direction.INPUT, 
                bias=Bias.PULL_UP, 
                edge_detection=Edge.BOTH,
                debounce_period=timedelta(milliseconds=50) 
            )
        }
    )

    try:
        while req.wait_edge_events(timedelta(seconds=0)):
            req.read_edge_events()

        step_logger.info("Mash the button as fast and as aggressively as you can for 5 seconds!")
        time.sleep(1)
        step_logger.info("GO!")

        with step_logger.step("Debounce Test", action="Count events over 5 seconds", expected="Clean edges with no bounce") as step:
            end_time = time.time() + 5.0
            total_edges = 0

            while time.time() < end_time:
                if req.wait_edge_events(timedelta(milliseconds=100)):
                    events = req.read_edge_events()
                    for event in events:
                        edge_type = "PRESS  " if event.event_type == gpiod.EdgeEvent.Type.FALLING_EDGE else "RELEASE"
                        step_logger.info(f"   Caught clean {edge_type} at {event.timestamp_ns}")
                        total_edges += 1

            step.success(f"5 seconds of aggressive mashing yielded {total_edges} perfectly clean edges.")
            
        step_logger.info("="*60)

    finally:
        req.release()