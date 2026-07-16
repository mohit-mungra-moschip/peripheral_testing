import time

from conftest import loopback_pins

def test_gpio_high_low(loopback_pins, board_config, step_logger):
    """Test basic HIGH/LOW propagation with visual LED pauses.
    The Hardware Setup
    Connect a single jumper wire directly from your configured out_pin 17 to your configured in_pin 27.
    """
    import time
    from gpiod.line import Value  
    
    request = loopback_pins
    out_pin = board_config["out_pin"]
    in_pin = board_config["in_pin"]

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED GPIO LOOPBACK: Pin {out_pin} (OUT) -> Pin {in_pin} (IN)")

    with step_logger.step("Assert HIGH", action=f"request.set_value({out_pin}, Value.ACTIVE)", expected=f"Pin {in_pin} reads HIGH") as step:
        request.set_value(out_pin, Value.ACTIVE)
        time.sleep(0.01) # 10ms hardware settling time to allow voltage to rise
        assert request.get_value(in_pin) == Value.ACTIVE, f"Hardware Failure: Pin {in_pin} did not read HIGH"
        step.success("Instantaneous HIGH propagation verified.")

    with step_logger.step("Assert LOW", action=f"request.set_value({out_pin}, Value.INACTIVE)", expected=f"Pin {in_pin} reads LOW") as step:
        request.set_value(out_pin, Value.INACTIVE)
        time.sleep(1) # 1s hardware settling time to allow voltage to drain
        assert request.get_value(in_pin) == Value.INACTIVE, f"Hardware Failure: Pin {in_pin} did not read LOW"
        step.success("Instantaneous LOW propagation verified.")
    
    step_logger.info("="*60)


def test_gpio_pull_up_down(board_config, step_logger):
    """Test internal pull-up and pull-down resistors with observation pauses.
    The Hardware Setup
    Maintain the exact same baseline setup as the previous test (Pin 17 -> Pin 27), 
    but for this test we will leave the OUT pin disconnected (High-Z) to allow the internal 
    pull resistors to do their job without interference.
    """
    import gpiod
    from gpiod.line import Direction, Value, Bias
    import time

    chip_path = board_config["chip"]
    out_pin = board_config["out_pin"]
    in_pin = board_config["in_pin"]

    # 1. High-Z Mode: Disconnect OUT pin so it doesn't interfere
    req_out = gpiod.request_lines(
        chip_path,
        consumer="test_high_z",
        config={out_pin: gpiod.LineSettings(direction=Direction.INPUT)}
    )

    try:
        step_logger.info("="*60)
        step_logger.info(f"AUTOMATED INTERNAL BIAS TEST: Pin {in_pin}")
        
        with step_logger.step("Request Pull-UP bias", action=f"request_lines({in_pin}, Bias.PULL_UP)", expected="Pin holds HIGH") as step:
            req_up = gpiod.request_lines(
                chip_path,
                consumer="test_pu",
                config={in_pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)}
            )
            time.sleep(1)  # 1s hardware settling time to allow internal voltage to rise
            assert req_up.get_value(in_pin) == Value.ACTIVE, f"Hardware Failure: Pin {in_pin} Pull-Up failed to hold HIGH"
            step.success("Pull-UP internal resistor successfully held line HIGH.")
            req_up.release() # Release hardware lock

        with step_logger.step("Request Pull-DOWN bias", action=f"request_lines({in_pin}, Bias.PULL_DOWN)", expected="Pin holds LOW") as step:
            req_down = gpiod.request_lines(
                chip_path,
                consumer="test_pd",
                config={in_pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_DOWN)}
            )
            time.sleep(0.01)  # 10ms hardware settling time to allow internal voltage to drain
            assert req_down.get_value(in_pin) == Value.INACTIVE, f"Hardware Failure: Pin {in_pin} Pull-Down failed to hold LOW"
            step.success("Pull-DOWN internal resistor successfully held line LOW.")
            req_down.release()
        
        step_logger.info("="*60)

    finally:
        req_out.release()


def test_gpio_edge_interrupts(board_config, step_logger):
    """Interactive test to physically confirm hardware interrupts.
    The Hardware Setup
    Maintain the exact same baseline setup as the previous test (Pin 17 -> Pin 27), 
    but for this test we will be generating electrical edges on the OUT pin and confirming 
    that the IN pin triggers hardware interrupts in the kernel.
    """
    import gpiod
    from gpiod.line import Direction, Edge, Bias, Value
    from datetime import timedelta
    import time

    chip_path = board_config["chip"]
    out_pin = board_config["out_pin"]
    in_pin = board_config["in_pin"]

    req_lines = gpiod.request_lines(
        chip_path,
        consumer="test_irq_automated",
        config={
            out_pin: gpiod.LineSettings(direction=Direction.OUTPUT),
            in_pin: gpiod.LineSettings(
                direction=Direction.INPUT, 
                edge_detection=Edge.BOTH, 
                bias=Bias.PULL_DOWN
            )
        }
    )

    try:
        step_logger.info("="*60)
        step_logger.info(f"AUTOMATED HARDWARE INTERRUPT (IRQ) VALIDATION")
        
        # 1. Establish baseline state (LOW)
        req_lines.set_value(out_pin, Value.INACTIVE)
        time.sleep(1)  # 1s settling time
        
        # Clear any electrical startup artifacts or noise from the kernel queue
        while req_lines.wait_edge_events(timedelta(seconds=0)):
            req_lines.read_edge_events()

        with step_logger.step("Test Rising Edge Event", action=f"set_value({out_pin}, Value.ACTIVE)", expected="RISING_EDGE event captured") as step:
            req_lines.set_value(out_pin, Value.ACTIVE)
            if not req_lines.wait_edge_events(timedelta(milliseconds=500)):
                assert False, f"Hardware Failure: Input pin {in_pin} failed to trigger an IRQ event on Rising Edge."
            events = req_lines.read_edge_events()
            assert events[0].event_type == gpiod.EdgeEvent.Type.RISING_EDGE, "Interrupt driver mismatch: Expected RISING_EDGE event token."
            step.success("Hardware kernel interrupt successfully captured matching RISING_EDGE.")

        with step_logger.step("Test Falling Edge Event", action=f"set_value({out_pin}, Value.INACTIVE)", expected="FALLING_EDGE event captured") as step:
            req_lines.set_value(out_pin, Value.INACTIVE)
            if not req_lines.wait_edge_events(timedelta(milliseconds=500)):
                assert False, f"Hardware Failure: Input pin {in_pin} failed to trigger an IRQ event on Falling Edge."
            events = req_lines.read_edge_events()
            assert events[0].event_type == gpiod.EdgeEvent.Type.FALLING_EDGE, "Interrupt driver mismatch: Expected FALLING_EDGE event token."
            step.success("Hardware kernel interrupt successfully captured matching FALLING_EDGE.")
            
        step_logger.info("="*60)

    finally:
        req_lines.release()


def test_gpio_level_polling(board_config, step_logger):
    """Simulates Level-Triggered validation via high-frequency polling.
    The Hardware Setup
    Maintain the exact same baseline setup as the previous test (Pin 17 -> Pin 27)
    """
    import gpiod
    from gpiod.line import Direction, Value, Bias
    import time

    chip_path = board_config["chip"]
    out_pin = board_config["out_pin"]
    in_pin = board_config["in_pin"]

    req_lines = gpiod.request_lines(
        chip_path,
        consumer="test_level",
        config={
            out_pin: gpiod.LineSettings(direction=Direction.OUTPUT),
            in_pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_DOWN)
        }
    )

    try:
        step_logger.info("="*60)
        step_logger.info("AUTOMATED LEVEL TRIGGER: ACTIVE-HIGH STABILITY")

        with step_logger.step("Assert HIGH and Poll", action=f"set_value({out_pin}, ACTIVE) and poll 100 times", expected="Signal remains HIGH constantly") as step:
            req_lines.set_value(out_pin, Value.ACTIVE)
            time.sleep(0.01) # 10ms initial hardware settling

            stability_checks = 100
            check_interval = 0.001 # 1 millisecond

            for i in range(stability_checks):
                if req_lines.get_value(in_pin) == Value.INACTIVE:
                    assert False, f"Hardware Failure: Signal collapsed back to LOW at check {i}/{stability_checks}."
                time.sleep(check_interval)

            step.success("Active-HIGH level was sustained cleanly at machine speed!")
            
        step_logger.info("="*60)

    finally:
        req_lines.release()


def test_gpio_level_polling_low(board_config, step_logger):
    """Simulates Level-Triggered validation for Active-LOW signals.
    The Hardware Setup
    Maintain the exact same baseline setup as the previous test (Pin 17 -> Pin 27)
    """
    import gpiod
    from gpiod.line import Direction, Value, Bias
    import time

    chip_path = board_config["chip"]
    out_pin = board_config["out_pin"]
    in_pin = board_config["in_pin"]

    req_lines = gpiod.request_lines(
        chip_path,
        consumer="test_level_low",
        config={
            out_pin: gpiod.LineSettings(direction=Direction.OUTPUT),
            in_pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)
        }
    )

    try:
        step_logger.info("="*60)
        step_logger.info("AUTOMATED LEVEL TRIGGER: ACTIVE-LOW STABILITY")

        with step_logger.step("Assert LOW and Poll", action=f"set_value({out_pin}, INACTIVE) and poll 100 times", expected="Signal remains LOW against internal Pull-Up") as step:
            req_lines.set_value(out_pin, Value.INACTIVE)
            time.sleep(0.01) # 10ms initial hardware settling

            stability_checks = 100
            check_interval = 0.001 # 1 millisecond

            for i in range(stability_checks):
                if req_lines.get_value(in_pin) == Value.ACTIVE:
                    assert False, f"Hardware Failure: Level bounced back to HIGH at check {i}/{stability_checks}."
                time.sleep(check_interval)

            step.success("Active-LOW level was sustained cleanly against internal Pull-Up!")
            
        step_logger.info("="*60)

    finally:
        req_lines.release()


def test_gpio_muxing_state(board_config, step_logger):
    """Validates that the SoC's internal router (Pinmux) has correctly assigned a pin.
    Because this test strictly queries the internal RP1 silicon router and does not measure 
    external electrical thresholds, no physical wiring is required. The test runs entirely inside the chip's logic gates.
    """
    import subprocess
    import pytest

    pin = board_config.get("mux_test_pin")
    expected_mux = board_config.get("expected_mux")

    assert pin is not None, "Configuration Error: 'mux_test_pin' not defined in board_config."
    assert expected_mux is not None, "Configuration Error: 'expected_mux' not defined in board_config."

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED PINMUX ROUTER VALIDATION: Pin {pin}")
    
    with step_logger.step("Query Silicon Registers", action=f"pinctrl get {pin}", expected=f"Register output contains {expected_mux}") as step:
        try:
            result = subprocess.run(
                ["pinctrl", "get", str(pin)], 
                capture_output=True, 
                text=True, 
                check=True
            )
        except FileNotFoundError:
            pytest.fail("Environment Error: 'pinctrl' tool not found on the target OS. Cannot validate multiplexer.")
        except subprocess.CalledProcessError as e:
            pytest.fail(f"OS Error: pinctrl command failed to read pin {pin}: {e.stderr}")

        output = result.stdout.strip()
        
        if expected_mux not in output:
            pytest.fail(
                f"Hardware Mux Failure!\n"
                f"   Expected Routing : {expected_mux}\n"
                f"   Actual Register  : {output}"
            )

        step.success(f"Silicon successfully routed Pin {pin} to {expected_mux}.")
        
    step_logger.info("="*60)


def test_gpio_drive_strength_basic(board_config, step_logger):
    """Interactive test to validate output drive strength under physical load.
    The Hardware Setup
    Maintain the exact same baseline setup as the previous test (Pin 17 -> Pin 27)
    """
    import gpiod
    from gpiod.line import Direction, Value, Bias
    import time

    chip_path = board_config["chip"]
    out_pin = board_config["out_pin"]
    in_pin = board_config["in_pin"]

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED DRIVE STRENGTH VALIDATION: Pin {out_pin} -> Pin {in_pin}")

    with step_logger.step("Testing Source Drive", action="Drive HIGH against internal Pull-Down load", expected="Source overcomes pull-down, reads HIGH") as step:
        req_lines = gpiod.request_lines(
            chip_path,
            consumer="test_drive_strength_src",
            config={
                out_pin: gpiod.LineSettings(direction=Direction.OUTPUT),
                in_pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_DOWN)
            }
        )

        try:
            req_lines.set_value(out_pin, Value.ACTIVE)
            time.sleep(0.01)  # 10ms machine settling time
            assert req_lines.get_value(in_pin) == Value.ACTIVE, \
                f"Hardware Failure: Pin {out_pin} source drive strength failed to overcome internal pull-down on Pin {in_pin}."
            step.success("Source current capability validated successfully.")
        finally:
            req_lines.release()

    with step_logger.step("Testing Sink Drive", action="Drive LOW against internal Pull-Up load", expected="Sink overcomes pull-up, reads LOW") as step:
        req_lines = gpiod.request_lines(
            chip_path,
            consumer="test_drive_strength_snk",
            config={
                out_pin: gpiod.LineSettings(direction=Direction.OUTPUT),
                in_pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)
            }
        )

        try:
            req_lines.set_value(out_pin, Value.INACTIVE)
            time.sleep(0.01)  # 10ms machine settling time
            assert req_lines.get_value(in_pin) == Value.INACTIVE, \
                f"Hardware Failure: Pin {out_pin} sink drive strength failed to overcome internal pull-up on Pin {in_pin}."
            step.success("Sink current capability validated successfully.")
        finally:
            req_lines.release()
            
    step_logger.info("="*60)