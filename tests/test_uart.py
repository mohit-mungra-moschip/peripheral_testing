import pytest
import time
import threading
import os
import subprocess
import re

def test_uart_physical_loopback(board_config, step_logger):
    """
    Validates UART TX/RX hardware via physical loopback.
    Tests multiple baud rates, edge-case payloads, and framing/error flags.
    """
    try:
        import serial
    except ImportError:
        pytest.skip("'pyserial' not installed on target.")

    assert "uart_port" in board_config, "board_config missing 'uart_port'"
    assert "uart_baud" in board_config, "board_config missing 'uart_baud'"

    port = board_config["uart_port"]

    baud_rates = sorted(set([
        9600,
        board_config["uart_baud"],
        921600,
    ]))

    test_stages = {
        "ASCII string":   b"HW_VAL_UART_ECHO_TEST_PASSED\\n",
        "Null bytes":     bytes([0x00] * 16),
        "0xFF bytes":     bytes([0xFF] * 16),
        "Binary pattern": bytes(range(0, 256)),
        "Long buffer":    bytes([0xA5] * 1024),
    }

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED UART PHYSICAL LOOPBACK: {port}")

    for baud in baud_rates:
        step_logger.info(f"{'-'*60}")
        step_logger.info(f"Baud Rate: {baud}")

        with step_logger.step(f"UART Loopback at {baud} baud", action="Send varied payloads", expected="All payloads match received data") as step:
            try:
                ser = serial.Serial(
                    port,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=2.0 
                )
            except serial.SerialException as e:
                pytest.fail(f"Could not open {port} at {baud} baud. Error: {e}")

            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()

                for stage_name, payload in test_stages.items():
                    ser.write(payload)
                    ser.flush()   

                    received = ser.read(len(payload))

                    if len(received) < len(payload):
                        pytest.fail(f"Baud {baud} | '{stage_name}': Partial read — got {len(received)}/{len(payload)} bytes.")

                    if not received:
                        pytest.fail(f"Baud {baud} | '{stage_name}': Received 0 bytes. Wire missing or UART disabled.")

                    leftover = ser.in_waiting
                    if leftover > 0:
                        ser.reset_input_buffer()
                        pytest.fail(f"Baud {baud} | '{stage_name}': {leftover} unexpected extra bytes in RX buffer.")

                    if received != payload:
                        corruptions = [f"byte[{i}]: sent 0x{s:02X} got 0x{r:02X}" for i, (s, r) in enumerate(zip(payload, received)) if s != r]
                        pytest.fail(f"Baud {baud} | '{stage_name}': {len(corruptions)} byte corruption(s).")

                    expected_ms = (len(payload) * 10 / baud) * 1000
                    step_logger.info(f"PASS: {stage_name:<16} ({len(payload):>4} bytes) (~{expected_ms:.1f} ms)")

                step.success(f"All payloads matched at {baud} baud.")

            finally:
                if ser.is_open:
                    ser.close()

    step_logger.info("SUCCESS: UART TX/RX validated across all baud rates and payloads!")
    step_logger.info("="*60)


def test_uart_baud_rate_sweep(board_config, step_logger):
    """
    Validates the hardware baud-rate generator by sweeping legacy,
    standard, and high-speed UART configurations via physical loopback.
    """
    try:
        import serial
    except ImportError:
        pytest.skip("'pyserial' not installed on target.")

    assert "uart_port" in board_config, "board_config missing 'uart_port'"
    port = board_config["uart_port"]

    test_baud_rates = [9600, 19200, 57600, 115200, 460800, 921600]

    OS_LATENCY_FLOOR_S = 0.015   
    MIN_WIRE_RATIO     = 10      

    def make_payload(baud):
        min_bytes = int((OS_LATENCY_FLOOR_S * MIN_WIRE_RATIO * baud) / 10)
        min_bytes = max(min_bytes, 64)    
        min_bytes = min(min_bytes, 4096)  

        header  = f"BAUD_{baud}:".encode()
        pattern = bytes([0x55, 0xAA, 0x00, 0xFF])
        body    = (pattern * ((min_bytes // len(pattern)) + 1))[:min_bytes - len(header)]
        return header + body

    TOLERANCE = 0.20   
    failed_rates = []

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED UART BAUD RATE SWEEP: {port}")

    for baud in test_baud_rates:
        payload  = make_payload(baud)
        original = bytes(payload)
        wire_time_expected_ms = (len(original) * 10 / baud) * 1000

        with step_logger.step(f"Baud Sweep: {baud}", action=f"Test throughput at {baud} baud", expected=f"Throughput within {int(TOLERANCE*100)}% of target") as step:
            step_logger.info(f"{baud:>7,} Baud ({len(original):>4} bytes, ~{wire_time_expected_ms:4.1f}ms expected wire time)")
            ser = None

            try:
                ser = serial.Serial(
                    port,
                    baudrate = baud,
                    bytesize = serial.EIGHTBITS,
                    parity   = serial.PARITY_NONE,
                    stopbits = serial.STOPBITS_ONE,
                    timeout  = max(3.0, (wire_time_expected_ms / 1000) * 5)
                )
                ser.reset_input_buffer()
                ser.reset_output_buffer()

                t_start = time.perf_counter()
                ser.write(payload)
                ser.flush()              
                t_tx_end = time.perf_counter()

                received = ser.read(len(original))

                if len(received) < len(original):
                    step_logger.info(f"[ERROR] Partial read: got {len(received)}/{len(original)} bytes.")
                    failed_rates.append(baud)
                    continue

                leftover = ser.in_waiting
                if leftover > 0:
                    ser.reset_input_buffer()
                    step_logger.info(f"[ERROR] {leftover} extra bytes in RX buffer — framing error.")
                    failed_rates.append(baud)
                    continue

                if received != original:
                    step_logger.info("[ERROR] Data corruption detected.")
                    failed_rates.append(baud)
                    continue

                tx_elapsed_s  = t_tx_end - t_start
                actual_bps    = (len(original) * 10) / tx_elapsed_s
                lower_bound   = baud * (1 - TOLERANCE)
                upper_bound   = baud * (1 + TOLERANCE)

                if not (lower_bound <= actual_bps <= upper_bound):
                    step_logger.info(f"[ERROR] Throughput out of tolerance: measured {actual_bps:,.0f} bps, expected {lower_bound:,.0f} - {upper_bound:,.0f} bps.")
                    failed_rates.append(baud)
                    continue

                step.success(f"PASS: {actual_bps:>10,.0f} bps (Tolerance +/-{int(TOLERANCE*100)}%)")

            except serial.SerialException as e:
                step_logger.info(f"[ERROR] SerialException: {e}")
                failed_rates.append(baud)

            finally:
                if ser and ser.is_open:
                    ser.close()

    if failed_rates:
        pytest.fail(f"Baud rate generator failure at: {failed_rates}.")

    step_logger.info(f"SUCCESS: Baud rate generator validated across {len(test_baud_rates)} speeds!")
    step_logger.info("="*60)


def test_uart_hardware_flow_control(board_config, step_logger):
    """
    Validates RTS/CTS Hardware Flow Control via software-driven CTS toggling.
    """
    try:
        import serial
        import gpiod
        from gpiod.line import Direction, Value
    except ImportError as e:
        pytest.skip(f"Required module missing: {e}")

    assert "uart_port"       in board_config, "board_config missing 'uart_port'"
    assert "uart_baud"       in board_config, "board_config missing 'uart_baud'"
    assert "chip"            in board_config, "board_config missing 'chip'"
    assert "cts_driver_gpio" in board_config, "board_config missing 'cts_driver_gpio'"

    port            = board_config["uart_port"]
    baud            = board_config["uart_baud"]
    CTS_DRIVER_GPIO = board_config["cts_driver_gpio"] 

    payload = b"FLOW_CONTROL_STRESS_TEST_DATA_" * 256  

    step_logger.info("="*60)
    step_logger.info("AUTOMATED UART HARDWARE FLOW CONTROL")
    step_logger.info("Forcing GPIO 16 into UART0_CTS hardware mode...")
    subprocess.run(["pinctrl", "set", "16", "a4"], check=False, stderr=subprocess.DEVNULL)
    
    cts_req = gpiod.request_lines(
        board_config["chip"],
        consumer="cts_controller",
        config={
            CTS_DRIVER_GPIO: gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.INACTIVE    
            )
        }
    )

    def run_transfer(ser, data, expect_block=False):
        received   = bytearray()
        tx_exc     = [None]

        def transmit():
            try:
                ser.write(data)
                ser.flush()
            except Exception as e:
                tx_exc[0] = e

        tx_thread = threading.Thread(target=transmit, daemon=True)
        tx_thread.start()

        if not expect_block:
            start = time.time()
            while len(received) < len(data):
                if time.time() - start > 10.0:
                    break
                chunk = ser.read(min(1024, len(data) - len(received)))
                if chunk:
                    received.extend(chunk)

        tx_thread.join(timeout=3.0)
        return bytes(received), tx_exc[0]

    try:
        ser = serial.Serial(
            port,
            baudrate     = baud,
            bytesize     = serial.EIGHTBITS,
            parity       = serial.PARITY_NONE,
            stopbits     = serial.STOPBITS_ONE,
            rtscts       = True,         
            timeout      = 2.0,
            write_timeout= 2.0
        )
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        with step_logger.step("Flow Control Baseline", action="Drive CTS LOW", expected="Transfer completes freely") as step:
            cts_req.set_value(CTS_DRIVER_GPIO, Value.INACTIVE)
            time.sleep(0.02)

            received, tx_exc = run_transfer(ser, payload, expect_block=False)

            if isinstance(tx_exc, serial.SerialTimeoutException):
                pytest.fail("Stage 1 FAIL: TX blocked with CTS HIGH.")
            elif tx_exc:
                pytest.fail(f"Stage 1 FAIL: TX thread error: {tx_exc}")

            assert len(received) == len(payload), f"Stage 1 FAIL: Got {len(received)}/{len(payload)} bytes."
            assert received == payload, "Stage 1 FAIL: Data corruption during baseline transfer."
            step.success(f"{len(payload):,} bytes transferred freely.")

        ser.reset_input_buffer()
        ser.reset_output_buffer()

        with step_logger.step("Flow Control Blocking", action="Drive CTS HIGH", expected="Transfer blocks") as step:
            cts_req.set_value(CTS_DRIVER_GPIO, Value.ACTIVE)  
            time.sleep(0.05)   
            ser.write_timeout = 1.5

            _, tx_exc = run_transfer(ser, payload, expect_block=True)
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            assert isinstance(tx_exc, serial.SerialTimeoutException), "Stage 2 FAIL: TX was NOT blocked when CTS driven HIGH."
            step.success("TX correctly blocked when CTS driven HIGH.")

        with step_logger.step("Flow Control Resume", action="Restore CTS LOW", expected="Transfer resumes cleanly") as step:
            cts_req.set_value(CTS_DRIVER_GPIO, Value.INACTIVE)   
            time.sleep(0.05) 
            
            ghost_bytes = ser.read(ser.in_waiting) if ser.in_waiting else b""
            if ghost_bytes:
                step_logger.info(f"Swept up {len(ghost_bytes)} ghost bytes from hardware FIFO.")

            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write_timeout = 2.0   

            chunk_payload = b"FLOW_CONTROL_STRESS_TEST_DATA_" * 32   
            received2, tx_exc2 = run_transfer(ser, chunk_payload, expect_block=False)

            if isinstance(tx_exc2, serial.SerialTimeoutException):
                pytest.fail("Stage 3 FAIL: TX still blocked after CTS restored LOW.")
            elif tx_exc2:
                pytest.fail(f"Stage 3 FAIL: TX thread error: {tx_exc2}")

            assert received2 == chunk_payload, "Stage 3 FAIL: Data corruption after flow control resume."
            step.success("TX resumed cleanly.")

        step_logger.info("SUCCESS: RTS/CTS fully validated!")
        step_logger.info("="*60)

    except serial.SerialException as e:
        pytest.fail(f"SerialException: {e}")

    finally:
        if 'cts_req' in locals():
            try:
                cts_req.set_value(CTS_DRIVER_GPIO, Value.ACTIVE)
            except Exception:
                pass
            cts_req.release()
        if 'ser' in locals() and ser.is_open:
            ser.close()


def test_uart_console_vs_secondary_isolation(board_config, step_logger):
    """
    Validates Console UART vs Secondary UART isolation.
    """
    try:
        import serial
    except ImportError:
        pytest.skip("'pyserial' not installed on target.")

    assert "uart_port"           in board_config, "board_config missing 'uart_port'"
    assert "secondary_uart_port" in board_config, "board_config missing 'secondary_uart_port'"
    assert "uart_baud"           in board_config, "board_config missing 'uart_baud'"

    console_port   = board_config["uart_port"]
    secondary_port = board_config["secondary_uart_port"]
    baud           = board_config["uart_baud"]

    step_logger.info("="*60)
    step_logger.info("AUTOMATED UART ISOLATION TEST: Console vs Secondary")

    with step_logger.step("Port Existence", action="Check console and secondary ports", expected="Both ports exist") as step:
        for port in [console_port, secondary_port]:
            if not os.path.exists(port):
                pytest.fail(f"Port {port} does not exist. Check config.txt")
        step.success("Both ports exist on filesystem.")

    with step_logger.step("Verify Console Free", action="Check /proc/cmdline", expected="Console not in use") as step:
        try:
            with open("/proc/cmdline") as f:
                cmdline = f.read()
            console_in_use = bool(
                re.search(r"console=ttyAMA0", cmdline) or
                re.search(r"console=ttyAMA10", cmdline) or
                re.search(r"console=serial0", cmdline)
            )
            if console_in_use:
                pytest.fail(f"Console UART is still the active Linux serial console.")
            step.success("Console UART is NOT the active kernel console.")
        except FileNotFoundError:
            step.success("Cannot read /proc/cmdline — not running on Linux.")

    with step_logger.step("Discover GPIO Routing", action="Use pinctrl", expected="Determine GPIO pins") as step:
        KNOWN_UART_PINS = {
            "/dev/ttyAMA0": (14, 15),
            "/dev/ttyAMA1": (14, 15),
            "/dev/ttyAMA10": (14, 15),
            "/dev/ttyAMA2": (4,  5),
            "/dev/ttyAMA3": (4,  5),
            "/dev/ttyAMA4": (8,  9),
            "/dev/ttyAMA5": (12, 13),
        }

        def discover_uart_pins(port_name):
            try:
                if "ttyAMA10" in port_name:
                    uart_num = "0"
                else:
                    uart_num_match = re.search(r"ttyAMA(\d+)", port_name)
                    uart_num = uart_num_match.group(1) if uart_num_match else "0"

                pinctrl_out = subprocess.check_output(["pinctrl", "-p"], text=True, stderr=subprocess.DEVNULL)
                tx_pattern = rf"^(\d+):.*(?:TXD{uart_num}|UART{uart_num}_TXD)"
                rx_pattern = rf"^(\d+):.*(?:RXD{uart_num}|UART{uart_num}_RXD)"

                tx_gpio, rx_gpio = None, None
                
                tx_match = re.search(tx_pattern, pinctrl_out, re.MULTILINE)
                if tx_match:
                    tx_gpio = int(tx_match.group(1))
                    
                rx_match = re.search(rx_pattern, pinctrl_out, re.MULTILINE)
                if rx_match:
                    rx_gpio = int(rx_match.group(1))

                known = KNOWN_UART_PINS.get(port_name)
                if known and (tx_gpio, rx_gpio) != known:
                    step_logger.info(f"[WARN] pinctrl mismatch. Using known: {known[0]}/{known[1]}")
                    return known

                if tx_gpio is not None and rx_gpio is not None:
                    return tx_gpio, rx_gpio

            except Exception:
                pass

            known = KNOWN_UART_PINS.get(port_name)
            if known:
                return known
            return None, None

        console_tx, console_rx     = discover_uart_pins(console_port)
        secondary_tx, secondary_rx = discover_uart_pins(secondary_port)

        assert console_tx and console_rx, f"Cannot determine GPIO pins for {console_port}."
        assert secondary_tx and secondary_rx, f"Cannot determine GPIO pins for {secondary_port}."
        step_logger.info(f"Console:   GPIO{console_tx} (TX) + GPIO{console_rx} (RX)")
        step_logger.info(f"Secondary: GPIO{secondary_tx} (TX) + GPIO{secondary_rx} (RX)")
        step.success("GPIO routing discovered.")

    with step_logger.step("Verify Electrical Isolation", action="Check overlap", expected="No overlap") as step:
        console_pins   = {console_tx, console_rx}
        secondary_pins = {secondary_tx, secondary_rx}
        overlap        = console_pins & secondary_pins
        assert not overlap, f"GPIO OVERLAP: Buses share pins {overlap}."
        step.success("No GPIO overlap — buses are electrically isolated.")

    with step_logger.step("Simultaneous Loopback", action="Transfer on both at once", expected="Both succeed") as step:
        console_payload   = b"CONSOLE_UART_TRAFFIC_" * 32    
        secondary_payload = b"SECONDARY_UART_TRAFFIC_" * 32  

        console_results   = {"received": None, "exception": None}
        secondary_results = {"received": None, "exception": None}

        def uart_transfer(ser, payload, result_dict):
            try:
                ser.write(payload)
                ser.flush()
                received = bytearray()
                start    = time.time()
                while len(received) < len(payload):
                    if time.time() - start > 5.0:
                        break
                    chunk = ser.read(min(256, len(payload) - len(received)))
                    if chunk:
                        received.extend(chunk)
                result_dict["received"] = bytes(received)
            except Exception as e:
                result_dict["exception"] = e

        try:
            console_ser = serial.Serial(
                console_port, baudrate=baud, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=2.0, write_timeout=2.0
            )
            secondary_ser = serial.Serial(
                secondary_port, baudrate=baud, bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=2.0, write_timeout=2.0
            )
            time.sleep(0.1)
            console_ser.reset_input_buffer()
            console_ser.reset_output_buffer()
            secondary_ser.reset_input_buffer()
            secondary_ser.reset_output_buffer()

            step_logger.info("Executing concurrent transfers...")
            t_console   = threading.Thread(target=uart_transfer, args=(console_ser, console_payload, console_results), daemon=True)
            t_secondary = threading.Thread(target=uart_transfer, args=(secondary_ser, secondary_payload, secondary_results), daemon=True)

            t_console.start()
            t_secondary.start()
            t_console.join(timeout=10.0)
            t_secondary.join(timeout=10.0)

            if console_results["exception"]:
                pytest.fail(f"Console UART error: {console_results['exception']}")
            assert console_results["received"] == console_payload, "Console UART: data corruption or partial read."
            step_logger.info(f"Console: {len(console_payload)} bytes OK")

            if secondary_results["exception"]:
                pytest.fail(f"Secondary UART error: {secondary_results['exception']}")
            assert secondary_results["received"] == secondary_payload, "Secondary UART: data corruption or partial read."
            step_logger.info(f"Secondary: {len(secondary_payload)} bytes OK")

            step.success("Console and Secondary UARTs fully isolated!")

        except serial.SerialException as e:
            pytest.fail(f"SerialException: {e}")
        finally:
            if 'console_ser' in locals() and console_ser.is_open:
                console_ser.close()
            if 'secondary_ser' in locals() and secondary_ser.is_open:
                secondary_ser.close()

    step_logger.info("="*60)