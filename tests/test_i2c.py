import os
import subprocess
import re
import pytest

def test_i2c_bus_detection(board_config, step_logger):
    """
    Validates that the target I2C bus is enabled and recognized by the OS.
    
    Hardware Setup: 
    No external wiring required. This strictly validates the internal 
    SoC I2C controller configuration and Linux device tree.
    """
    bus_num = board_config.get("i2c_bus", 1)
    bus_path = f"/dev/i2c-{bus_num}"

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED I2C BUS DETECTION: (Bus {bus_num})")

    # 1. OS-Level Device Check
    with step_logger.step("Check OS Device Path", action=f"Check if {bus_path} exists", expected="Path exists") as step:
        if not os.path.exists(bus_path):
            pytest.fail(f"Hardware Failure: {bus_path} not found. Is I2C enabled in raspi-config?")
        step.success(f"Kernel device recognized: {bus_path}")

    # 2. Driver-Level Subsystem Check
    with step_logger.step("Driver Subsystem Check", action="i2cdetect -l", expected=f"Output contains i2c-{bus_num}") as step:
        try:
            result = subprocess.run(
                    ["/usr/sbin/i2cdetect", "-l"], 
                    capture_output=True, 
                    text=True, 
                    check=True
                )
            output = result.stdout.strip()
            
            if f"i2c-{bus_num}" not in output:
                pytest.fail(f"Driver Error: i2cdetect did not report i2c-{bus_num}.")
            step.success(f"I2C Bus {bus_num} is actively routed and ready for devices!")
                
        except FileNotFoundError:
            pytest.fail("Environment Error: 'i2c-tools' is not installed on the target Pi.")

    step_logger.info("="*60)


@pytest.fixture(scope="function")
def virtual_i2c_environment(board_config, step_logger):
    """
    Dynamically creates a virtual I2C bus via the Linux kernel, 
    yields it to the test, and securely tears it down afterwards.
    
    Hardware Setup:
    Purely Software-In-the-Loop (SIL). No physical hardware required.
    """
    step_logger.info("-" * 60)
    step_logger.info("[SETUP] Provisioning Virtual Silicon in RAM...")
    
    virtual_devices = ["0x27", "0x68"]
    
    # Forcefully remove any existing stub in the kernel before we start
    subprocess.run(["sudo", "modprobe", "-r", "i2c-stub"], check=False, stderr=subprocess.DEVNULL)

    # 1. Tell the Linux Kernel to hallucinate an I2C bus
    subprocess.run(["sudo", "modprobe", "i2c-stub", "chip_addr=0x27,0x68"], check=True)
    
    # 2. Query the OS to find out what Bus Number it assigned to the stub
    result = subprocess.run(["/usr/sbin/i2cdetect", "-l"], capture_output=True, text=True)
    
    stub_bus = None
    for line in result.stdout.splitlines():
        if "SMBus stub driver" in line:
            match = re.search(r'i2c-(\d+)', line)
            if match:
                stub_bus = int(match.group(1))
                break
                
    if stub_bus is None:
        subprocess.run(["sudo", "modprobe", "-r", "i2c-stub"], check=False)
        pytest.fail("Automation Failure: Kernel did not create the i2c-stub.")
        
    step_logger.info(f"[SETUP] Virtual Bus Online and Mapped to: i2c-{stub_bus}")
    
    original_bus = board_config.get("i2c_bus")
    original_devices = board_config.get("expected_i2c_devices")
    
    board_config["i2c_bus"] = stub_bus
    board_config["expected_i2c_devices"] = virtual_devices
    
    yield board_config
    
    step_logger.info("[TEARDOWN] Destroying Virtual Bus...")
    subprocess.run(["sudo", "modprobe", "-r", "i2c-stub"], check=False)
    
    board_config["i2c_bus"] = original_bus
    board_config["expected_i2c_devices"] = original_devices
    step_logger.info("[TEARDOWN] Environment restored to physical state.")
    step_logger.info("-" * 60)


def test_i2c_virtual_device_scan(virtual_i2c_environment, step_logger):
    """
    Tests the parsing logic against an automated Software-In-Loop (SIL) environment.
    
    Hardware Setup:
    Dynamically handled by the virtual_i2c_environment fixture.
    """
    board_config = virtual_i2c_environment
    bus_num = board_config.get("i2c_bus")
    expected_devices = board_config.get("expected_i2c_devices")

    step_logger.info("="*60)
    step_logger.info(f"SOFTWARE-IN-LOOP: I2C DEVICE SCAN (Virtual Bus {bus_num})")

    with step_logger.step("Scan Virtual Devices", action=f"i2cdetect -y {bus_num}", expected=f"All virtual devices ({expected_devices}) detected") as step:
        try:
            result = subprocess.run(
                ["sudo", "/usr/sbin/i2cdetect", "-y", str(bus_num)], 
                capture_output=True, 
                text=True, 
                check=True
            )
        except subprocess.CalledProcessError:
            pytest.fail(f"Framework Error: Could not ping Virtual Bus {bus_num}.")

        output = result.stdout
        active_addresses = []
        for line in output.split('\n')[1:]:
            if ':' in line:
                cells = line.split(':')[1].strip().split()
                for cell in cells:
                    if cell not in ('--', 'UU') and re.match(r'^[0-9a-f]{2}$', cell):
                        active_addresses.append(f"0x{cell}")

        step_logger.info(f"Virtual Devices responding: {active_addresses}")

        missing_devices = [exp for exp in expected_devices if exp.lower() not in active_addresses]
        assert not missing_devices, f"SIL Mismatch: Expected {missing_devices} but they did not respond!"

        step.success(f"All virtual devices ({expected_devices}) detected properly!")
        
    step_logger.info("="*60)


def test_i2c_nack_error_handling(board_config, step_logger):
    """
    Validates that addressing a non-existent device properly catches a NACK.
    
    Hardware Setup: 
    Ensure no physical I2C device is connected to address 0x77 on the 
    target physical bus. The test requires an empty address slot to generate 
    a hardware NACK (Not Acknowledge) signal.
    """
    bus_num = board_config.get("i2c_bus", 1)
    missing_address = "0x77" 

    step_logger.info("="*60)
    step_logger.info(f"AUTOMATED I2C NACK HANDLING: (Bus {bus_num}, Address {missing_address})")

    with step_logger.step("Query Non-existent Address", action=f"i2cget -y {bus_num} {missing_address}", expected="Command fails with NACK error") as step:
        try:
            subprocess.run(
                ["/usr/sbin/i2cget", "-y", str(bus_num), missing_address], 
                capture_output=True, 
                text=True, 
                check=True
            )
            pytest.fail(f"Hardware Fault: A ghost device responded at {missing_address}! (Expected a NACK)")

        except subprocess.CalledProcessError as e:
            error_output = e.stderr.strip()
            
            if "Error: Read failed" in error_output or "Error:" in error_output:
                step.success(f"Framework successfully caught the hardware NACK at {missing_address}.")
            else:
                pytest.fail(f"Unexpected error format during NACK: {error_output}")
                
    step_logger.info("="*60)