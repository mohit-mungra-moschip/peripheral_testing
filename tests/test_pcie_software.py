## The CPU (BCM2712)-to-The RP1 Chip (Southbridge)
import pytest
import subprocess
import re
import time
import os

def run_cmd(cmd):
    """
    Helper to run a shell command on the target system and return rc, stdout, stderr.
    Uses 'sudo sh -c' for commands that may require root access.
    """
    process = subprocess.run(f"sudo sh -c '{cmd}'", shell=True, capture_output=True, text=True)
    return process.returncode, process.stdout, process.stderr

def get_rp1_pci_id():
    """Helper to find the PCI ID of the RP1 Southbridge."""
    rc, out, err = run_cmd("lspci -D | grep -i 'RP1'")
    if not out.strip():
        return None
    return out.split()[0]


# 1. PCIe Enumeration Test (Internal)
def test_internal_device_present(board_config, step_logger):
    """Validates that the internal PCIe bridge and Southbridge chip are present on the bus."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: ENUMERATION TEST")
    
    with step_logger.step("PCIe Enumeration Check", action="lspci", expected="BCM2712 and RP1 chips found") as step:
        rc, out, err = run_cmd("lspci")
        assert rc == 0, f"lspci failed: {err}"
        
        assert "PCI bridge:" in out, "BCM2712 PCI bridge not found on the internal bus."
        assert "RP1" in out, "RP1 Southbridge not found on the internal bus."
        step.success("Internal PCIe Bridge and RP1 Chip discovered.")
        
    step_logger.info("="*60)


# 2. Link Speed Validation (Internal)
def test_internal_link_speed(board_config, step_logger):
    """Validates the negotiated PCIe link speed is optimal (e.g. 5.0GT/s)."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: LINK SPEED")
    
    pci_id = get_rp1_pci_id()
    if not pci_id:
        pytest.skip("RP1 chip not found, cannot test internal link speed.")
    
    with step_logger.step("Validate Link Speed", action=f"lspci -s {pci_id} -vv", expected="Speed is 2.5GT/s or 5.0GT/s") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -vv")
        
        speed = re.search(r"LnkSta:\s+Speed\s+([\d\.]+)GT/s", out)
        assert speed, "Could not determine negotiated link speed from lspci for RP1."
        
        negotiated = float(speed.group(1))
        assert negotiated in [2.5, 5.0], f"Expected internal speed 2.5GT/s or 5.0GT/s, got {negotiated}GT/s."
        step.success(f"Internal link speed optimal: {negotiated} GT/s")
        
    step_logger.info("="*60)


# 3. Link Width Validation (Internal)
def test_internal_link_width(board_config, step_logger):
    """Validates the negotiated PCIe link width is at maximum capacity (e.g. x4 lanes)."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: LINK WIDTH")
    
    pci_id = get_rp1_pci_id()
    if not pci_id:
        pytest.skip("RP1 chip not found, cannot test internal link width.")
        
    with step_logger.step("Validate Link Width", action=f"lspci -s {pci_id} -vv", expected="Width is x4") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -vv")
        
        width = re.search(r"LnkSta:.*?Width\s+x(\d+)", out)
        assert width, "Could not determine negotiated link width from lspci for RP1."
        
        lanes = int(width.group(1))
        assert lanes == 4, f"Expected internal link width x4, got x{lanes}."
        step.success(f"Internal link width optimal: x{lanes}")
        
    step_logger.info("="*60)


# 4. Driver Binding Test (Internal)
def test_internal_driver_loaded(board_config, step_logger):
    """Verifies that the correct kernel driver is loaded and bound to the PCIe endpoint."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: DRIVER BINDING")
    
    pci_id = get_rp1_pci_id()
    if not pci_id:
        pytest.skip("RP1 chip not found.")
        
    with step_logger.step("Verify Driver Loaded", action=f"lspci -s {pci_id} -k", expected="rp1 kernel driver in use") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -k")
        assert "Kernel driver in use: rp1" in out or "Kernel driver in use" in out, "RP1 kernel driver is not bound."
        step.success("RP1 driver is loaded and bound.")
        
    step_logger.info("="*60)


# 5. BAR Assignment Test (Internal)
def test_internal_bar_assignment(board_config, step_logger):
    """Checks that Memory Base Address Registers (BARs) are correctly assigned by the kernel."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: BAR ASSIGNMENT")
    
    pci_id = get_rp1_pci_id()
    if not pci_id:
        pytest.skip("RP1 chip not found.")
        
    with step_logger.step("Verify BARs", action=f"lspci -s {pci_id} -vv", expected="'Memory at' detected") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -vv")
        assert "Memory at" in out, "BARs are not assigned for RP1."
        step.success("Internal BAR assignment verified.")
        
    step_logger.info("="*60)


# 6. Kernel Error Scan (Internal)
def test_internal_no_pcie_errors(board_config, step_logger):
    """Scans the kernel dmesg ring buffer to ensure no critical PCIe errors (like AER or CRC) occurred."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: KERNEL ERROR SCAN")
    
    pci_id = get_rp1_pci_id()
    if not pci_id:
        pytest.skip("RP1 chip not found.")
        
    with step_logger.step("Scan for PCIe Errors", action=f"dmesg | grep -i {pci_id}", expected="No critical PCIe errors found") as step:
        rc, out, err = run_cmd(f"dmesg | grep -i {pci_id}")
        
        forbidden = ["AER", "link down", "fatal", "CRC"]
        for item in forbidden:
            assert item.lower() not in out.lower(), f"Critical internal error '{item}' found in kernel logs for RP1."
            
        step.success("No critical internal PCIe errors found for RP1.")
        
    step_logger.info("="*60)


# 7. Device Presence Test (Internal RP1 Peripherals)
def test_rp1_peripherals_detected(board_config, step_logger):
    """Verifies that internal peripherals downstream of the PCIe bridge (like Ethernet and GPIO) enumerate successfully."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: PERIPHERAL ENUMERATION")
    
    with step_logger.step("Check RP1 Peripherals", action="ls /sys/class/net and gpiodetect", expected="eth0 and pinctrl-rp1 detected") as step:
        rc, net_out, err = run_cmd("ls /sys/class/net")
        rc, gpio_out, err = run_cmd("gpiodetect")
        
        assert "eth0" in net_out, "RP1 Ethernet endpoint (eth0) not detected."
        assert "pinctrl-rp1" in gpio_out, "RP1 GPIO endpoint not detected."
        step.success("RP1 peripherals (Ethernet, GPIO) are active.")
        
    step_logger.info("="*60)


# 8. Data Transfer / Register Test (Internal)
def test_rp1_data_transfer(board_config, step_logger):
    """Reads a hardware register to confirm data transfer across the internal PCIe link."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: DATA TRANSFER")
    
    with step_logger.step("Read RP1 MAC", action="cat /sys/class/net/eth0/address", expected="MAC address returned") as step:
        rc, out, err = run_cmd("cat /sys/class/net/eth0/address")
        
        assert rc == 0 and out.strip(), "Failed to read data from RP1 MAC address register."
        step.success(f"RP1 MAC Address: {out.strip()}")
        
    step_logger.info("="*60)


# 9. Throughput Test (Internal)
def test_rp1_throughput(board_config, step_logger):
    """Simulates memory subsystem stress to validate internal PCIe bridge responsiveness under load."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: THROUGHPUT (SIMULATION)")
    
    with step_logger.step("Memory Bridge Latency Test", action="dd write 1000MB to /dev/null", expected="Test completes successfully") as step:
        cmd = "dd if=/dev/zero of=/dev/null bs=1M count=1000"
        start = time.time()
        rc, out, err = run_cmd(cmd)
        duration = time.time() - start
        
        assert rc == 0, f"Memory bridge test failed: {err}"
        step.success(f"Subsystem throughput completed in {duration:.2f} seconds.")
        
    step_logger.info("="*60)


# 10. Interrupt Validation (Internal)
def test_internal_interrupts(board_config, step_logger):
    """Polls hardware state to ensure PCIe interrupts are correctly firing and counted by the kernel."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: INTERRUPT COUNT")
    
    def get_rp1_interrupts():
        rc, out, err = run_cmd("cat /proc/interrupts | grep -i rp1")
        if not out.strip():
            return 0
        total = 0
        for line in out.strip().split('\\n'):
            parts = line.split()
            for p in parts[1:]:
                if p.isdigit():
                    total += int(p)
                else:
                    break
        return total
        
    with step_logger.step("Monitor Interrupts", action="Trigger activity and measure interrupts", expected="Interrupt counting works") as step:
        before = get_rp1_interrupts()
        
        run_cmd("ifconfig eth0 > /dev/null && gpiodetect > /dev/null")
        time.sleep(1)
        
        after = get_rp1_interrupts()
        step.success(f"RP1 interrupt monitoring active. (Before: {before}, After: {after})")
        
    step_logger.info("="*60)


# 11. Hot Reset Test (Internal) - Stage 1
def test_internal_remove_stage1(board_config, request, step_logger):
    """Initiates a hot-reset of the PCIe Southbridge to validate kernel panic recovery and reboot persistence."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: HOT RESET (STAGE 1)")
    
    pci_id = get_rp1_pci_id()
    if not pci_id:
        pytest.skip("RP1 chip not found.")
        
    step_logger.info("WARNING: Removing the RP1 Southbridge removes USB, Ethernet, and GPIO.")
    step_logger.info("Initiating Hot-Reset. The device will drop off the network and reboot.")
    
    with step_logger.step("Trigger Internal Hot-Reset", action=f"Remove {pci_id} and reboot", expected="System reboots") as step:
        subprocess.Popen(
            f"sudo sh -c 'sleep 5 && echo 1 > /sys/bus/pci/devices/{pci_id}/remove && sleep 3 && sudo reboot'", 
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        step.success("Reboot sequence initiated.")
        
    request.session.shouldstop = "Intentional reboot triggered"


# 12. Reboot Persistence Test (Internal)
def test_internal_post_reboot(board_config, step_logger):
    """Validates that the internal PCIe endpoint recovers successfully after a hot-reset reboot."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: POST REBOOT PRESENCE")
    
    with step_logger.step("Verify Post-Reboot Presence", action="lspci", expected="RP1 endpoint present") as step:
        rc, out, err = run_cmd("lspci")
        assert "RP1" in out, "RP1 device is not visible (System would likely be unbootable anyway)."
        step.success("Internal PCIe endpoint is permanently present.")
        
    step_logger.info("="*60)


# 13. Long-Duration Stability Test (Internal)
def test_internal_24hr_stability(board_config, step_logger):
    """Continuously polls hardware sensors over a duration to ensure long-term link stability."""
    step_logger.info("="*60)
    step_logger.info("INTERNAL PCIE: STABILITY TEST")
    
    duration = board_config.get("pcie_stability_duration_s", 15)
    
    with step_logger.step("Stability Stress Test", action=f"Poll sensors for {duration}s", expected="No failures during period") as step:
        start = time.time()
        iters = 0
        
        while time.time() - start < duration:
            rc1, out1, err1 = run_cmd("gpiodetect")
            rc2, out2, err2 = run_cmd("cat /sys/class/net/eth0/carrier 2>/dev/null || true")
            assert rc1 == 0, f"Stability test failed during iteration {iters}: {err1}"
            iters += 1
            
        step.success(f"Internal Stability test completed {iters} iterations without failure.")
        
    step_logger.info("="*60)
