import pytest
import time
from datetime import timedelta

SPI_MODES = [0, 1, 2, 3]

def test_spi_master_loopback(board_config, step_logger):
    """
    Validates SPI Master mode across all 4 CPOL/CPHA combinations.
    """
    try:
        import spidev
    except ImportError:
        pytest.skip("'spidev' module not installed on target.")

    assert "spi_bus" in board_config, "board_config missing 'spi_bus'"
    assert "spi_device" in board_config, "board_config missing 'spi_device'"

    bus    = board_config.get("spi_bus", 0)
    device = board_config.get("spi_device", 0)
    test_payload = [0xDE, 0xAD, 0xBE, 0xEF, 0xAA, 0x55]

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED SPI MASTER LOOPBACK: (Bus {bus}, CE{device})")

    spi = spidev.SpiDev()
    try:
        with step_logger.step("Configure SPI", action=f"Open bus {bus} device {device}", expected="SPI device opened") as step:
            spi.open(bus, device)
            spi.max_speed_hz  = 1_000_000
            spi.bits_per_word = 8
            step.success("SPI configured.")

        for mode in SPI_MODES:
            with step_logger.step(f"Loopback Mode {mode}", action=f"Set mode {mode}, send payload", expected="Received matches sent") as step:
                spi.mode = mode
                received = spi.xfer2(list(test_payload))

                assert received != [0x00] * len(test_payload), f"Mode {mode} Fault: All-zeros received — MISO line may be floating low or jumper missing."
                assert received != [0xFF] * len(test_payload), f"Mode {mode} Fault: All-ones received — MISO line may be floating high."
                assert received == test_payload, f"Mode {mode} Fault: Data mismatch. Sent {test_payload}, got {received}"

                step.success(f"PASS Mode {mode}: {[hex(x) for x in received]}")

        step_logger.info("="*60)

    except FileNotFoundError:
        pytest.fail(f"OS Error: /dev/spidev{bus}.{device} not found. Enable SPI via raspi-config.")
    finally:
        spi.close()


def test_spi_chip_select_assertion(board_config, step_logger):
    """
    Validates the full Chip Select (CS) lifecycle using kernel edge-buffers.
    """
    try:
        import spidev
        import gpiod
        from gpiod.line import Direction, Edge
    except ImportError as e:
        pytest.skip(f"Required module missing: {e}")

    assert "spi_bus"    in board_config, "board_config missing 'spi_bus'"
    assert "spi_device" in board_config, "board_config missing 'spi_device'"
    assert "chip"       in board_config, "board_config missing 'chip'"

    bus            = board_config["spi_bus"]
    device         = board_config["spi_device"]
    cs_monitor_pin = board_config.get("in_pin", 27)

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED SPI CHIP SELECT LIFECYCLE: (Monitoring GPIO {cs_monitor_pin})")

    spi = spidev.SpiDev()
    spi.open(bus, device)
    spi.max_speed_hz  = 100_000
    spi.bits_per_word = 8

    assert not spi.cshigh, "Configuration Error: spi.cshigh=True means CS is active-HIGH."

    req = gpiod.request_lines(
        board_config["chip"],
        consumer="cs_validator",
        config={cs_monitor_pin: gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.BOTH)}
    )

    try:
        with step_logger.step("Test CS Lifecycle", action="Send 200 bytes and monitor CS edges", expected="Exactly 2 edges detected (LOW then HIGH)") as step:
            test_payload = [0xAA] * 200
            step_logger.info(f"Executing blocking {len(test_payload)}-byte transfer...")
            spi.xfer2(list(test_payload))

            assert req.wait_edge_events(timeout=timedelta(seconds=1)), f"Hardware Fault: No CS edges detected. Is the CS pin wired to GPIO {cs_monitor_pin}?"

            all_events = req.read_edge_events(max_events=10)
            all_events = sorted(all_events, key=lambda e: e.timestamp_ns)

            assert len(all_events) >= 2, f"Hardware Fault: Expected at least 2 edges, got {len(all_events)}. CS may be stuck."

            first_event = all_events[0]
            assert first_event.event_type == gpiod.EdgeEvent.Type.FALLING_EDGE, "Fault: CS Asserted HIGH (Expected LOW)."
            step_logger.info(f"PASS: CS Asserted   LOW  at {first_event.timestamp_ns:,} ns")

            last_event = all_events[-1]
            assert last_event.event_type == gpiod.EdgeEvent.Type.RISING_EDGE, "Fault: CS never de-asserted."
            step_logger.info(f"PASS: CS De-asserted HIGH at {last_event.timestamp_ns:,} ns")

            assert len(all_events) == 2, f"Fault: CS toggled {len(all_events)} times. Expected exactly 2."

            cs_low_duration_ns = last_event.timestamp_ns - first_event.timestamp_ns
            min_expected_ns    = 16_000_000
            
            assert cs_low_duration_ns >= min_expected_ns, f"Fault: CS LOW duration too short ({cs_low_duration_ns / 1_000_000:.2f} ms)."
            step.success(f"CS held LOW for required {cs_low_duration_ns / 1_000_000:.2f} ms.")

        step_logger.info("="*60)

    finally:
        spi.close()
        req.release()


def test_spi_clock_polarity_and_phase(board_config, step_logger):
    """
    Software logic analyzer for CPOL/CPHA validation.
    """
    try:
        import spidev, gpiod
        from gpiod.line import Direction, Edge, Value
    except ImportError as e:
        pytest.skip(f"Required module missing: {e}")

    for key in ("spi_bus", "spi_device", "chip", "sclk_monitor_pin", "mosi_monitor_pin"):
        assert key in board_config, f"board_config missing required key: '{key}'"

    bus          = board_config["spi_bus"]
    device       = board_config["spi_device"]
    sclk_monitor = board_config["sclk_monitor_pin"]
    mosi_monitor = board_config["mosi_monitor_pin"]

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED CPOL/CPHA LOGIC ANALYZER: (SCLK={sclk_monitor}, MOSI={mosi_monitor})")

    spi = spidev.SpiDev()
    spi.open(bus, device)
    spi.max_speed_hz  = 100_000
    spi.bits_per_word = 8

    one_clock_ns       = int(1e9 / spi.max_speed_hz)
    jitter_margin_ns   = int(one_clock_ns * 0.20)

    monitor_req = gpiod.request_lines(
        board_config["chip"],
        consumer="spi_timing_analyzer",
        config={
            sclk_monitor: gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.BOTH),
            mosi_monitor: gpiod.LineSettings(direction=Direction.INPUT, edge_detection=Edge.BOTH)
        }
    )

    try:
        for mode in [0, 1, 2, 3]:
            with step_logger.step(f"Validate CPOL/CPHA Mode {mode}", action=f"Test SCLK idle and MOSI edge in mode {mode}", expected="Edges match expected polarity/phase") as step:
                spi.mode = mode
                expected_cpol = (mode >> 1) & 1
                expected_cpha = mode & 1

                spi.xfer2([0x00])
                time.sleep(0.01)

                while monitor_req.wait_edge_events(timeout=timedelta(milliseconds=1)):
                    monitor_req.read_edge_events()

                idle_value = monitor_req.get_value(sclk_monitor)
                idle_level = 1 if idle_value == Value.ACTIVE else 0
                assert idle_level == expected_cpol, f"Mode {mode} CPOL FAIL: SCLK idles {'HIGH' if idle_level else 'LOW'}, expected {'HIGH' if expected_cpol else 'LOW'}."
                step_logger.info(f"PASS CPOL={expected_cpol}: SCLK idles {'HIGH' if idle_level else 'LOW'}")

                spi.xfer2([0x55, 0x55])
                assert monitor_req.wait_edge_events(timeout=timedelta(milliseconds=500)), f"Mode {mode} CPHA FAIL: No edges detected. Check wiring."

                events = sorted(monitor_req.read_edge_events(max_events=50), key=lambda e: e.timestamp_ns)
                sclk_events = [e for e in events if e.line_offset == sclk_monitor]
                mosi_events = [e for e in events if e.line_offset == mosi_monitor]

                assert len(sclk_events) >= 2, f"Mode {mode}: Too few SCLK edges. Clock may be stuck."
                assert len(mosi_events) >= 1, f"Mode {mode}: No MOSI transitions. Is MOSI wired correctly?"

                first_sclk_ns = sclk_events[0].timestamp_ns
                first_mosi_ns = mosi_events[0].timestamp_ns

                if expected_cpha == 0:
                    assert first_mosi_ns < first_sclk_ns, f"Mode {mode} CPHA=0 FAIL: MOSI changed AFTER first SCLK edge."
                    step.success(f"PASS CPHA=0: MOSI set up {first_sclk_ns - first_mosi_ns}ns before SCLK")
                else:
                    delta_ns = abs(first_mosi_ns - first_sclk_ns)
                    allowed  = one_clock_ns + jitter_margin_ns
                    assert delta_ns <= allowed, f"Mode {mode} CPHA=1 FAIL: MOSI/SCLK delta {delta_ns}ns exceeds allowed {allowed}ns."
                    step.success(f"PASS CPHA=1: MOSI/SCLK delta {delta_ns}ns <= {allowed}ns")

        step_logger.info("="*60)

    finally:
        spi.close()
        monitor_req.release()


import random

def test_spi_data_integrity_stress(board_config, step_logger):
    """
    Validates SPI data integrity by blasting edge-case patterns.
    """
    try:
        import spidev
    except ImportError:
        pytest.skip("'spidev' module not installed on target.")

    assert "spi_bus"    in board_config, "board_config missing 'spi_bus'"
    assert "spi_device" in board_config, "board_config missing 'spi_device'"

    bus    = board_config["spi_bus"]
    device = board_config["spi_device"]
    KERNEL_MAX = 4096 

    test_stages = {
        "Ground-Stuck  (All 0x00)":    [0x00] * 128,
        "VCC-Stuck     (All 0xFF)":    [0xFF] * 128,
        "Slew-Rate     (0x55/0xAA)":   [0x55, 0xAA] * 64,
        "Walking-Ones  (bit sweep)":   [(1 << (i % 8)) for i in range(128)],
        "FIFO Boundary (4094 bytes)":  [random.randint(0, 255) for _ in range(4094)],
        "FIFO Limit    (4096 bytes)":  [random.randint(0, 255) for _ in range(KERNEL_MAX)],
    }

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED SPI STRESS VALIDATION: (10 MHz Integrity Test)")

    spi = spidev.SpiDev()
    spi.open(bus, device)
    spi.bits_per_word = 8

    try:
        for mode in [0, 1, 2, 3]:
            with step_logger.step(f"Stress Test Mode {mode}", action="Execute payload matrix at 10MHz", expected="No corruptions detected") as step:
                spi.mode = mode
                spi.max_speed_hz = 10_000_000 

                step_logger.info(f"Executing Payload Matrix for Mode {mode}...")

                for stage_name, payload in test_stages.items():
                    is_ambiguous = payload == [0x00] * len(payload) or payload == [0xFF] * len(payload)

                    original = list(payload)
                    start    = time.perf_counter()
                    received = spi.xfer2(list(payload))
                    elapsed  = time.perf_counter() - start

                    if not is_ambiguous:
                        assert received != [0x00] * len(payload), f"Mode {mode} | '{stage_name}': MISO stuck LOW."
                        assert received != [0xFF] * len(payload), f"Mode {mode} | '{stage_name}': MISO stuck HIGH."

                    if received != original:
                        corruptions = [f"byte[{i}]: sent {hex(s)} got {hex(r)}" for i, (s, r) in enumerate(zip(original, received)) if s != r]
                        if len(received) != len(original):
                            corruptions.append(f"length mismatch: sent {len(original)}, got {len(received)}")
                        
                        total = len(corruptions)
                        corruption_report = "\\n    ".join(corruptions[:5]) 
                        assert False, f"Mode {mode} | '{stage_name}': {total} corruption(s) detected:\\n    {corruption_report}..."

                    kb_per_sec = (len(payload) / elapsed) / 1024
                    step_logger.info(f"PASS: {stage_name:<26} -> {kb_per_sec:8.2f} KB/s")

                step.success("All payloads completed successfully.")

        with step_logger.step("Guard Check", action="Test 4097 byte kernel rejection limit", expected="Transfer rejected by kernel") as step:
            try:
                spi.xfer2([0xAA] * (KERNEL_MAX + 1))
                step_logger.info("WARNING: 4097-byte transfer was NOT rejected by kernel.")
            except (OverflowError, OSError, IOError) as e:
                step.success(f"Transfer rejected securely by kernel ({type(e).__name__})")

        step_logger.info("="*60)

    finally:
        spi.close()