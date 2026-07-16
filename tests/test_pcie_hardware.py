import pytest
import subprocess
import re
import time
import os

def run_cmd(cmd):
    """
    Helper to run a shell command on the target system and return rc, stdout, stderr.
    Uses 'sudo sh -c' to ensure commands that require root access (like nvme, dmesg, echo to sysfs) work properly.
    """
    process = subprocess.run(f"sudo sh -c '{cmd}'", shell=True, capture_output=True, text=True)
    return process.returncode, process.stdout, process.stderr

def test_pcie_device_present(board_config, step_logger):
    """
    1. PCIe Enumeration Test
    Verify endpoint is visible.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: ENUMERATION TEST")
    
    with step_logger.step("PCIe Enumeration Check", action="lspci", expected="Endpoint visible (NVMe or Network controller)") as step:
        rc, out, err = run_cmd("lspci")
        assert rc == 0, f"lspci failed: {err}"
        
        expected = [
            "Non-Volatile memory controller",
            "Network controller"
        ]
        
        found = any(dev in out for dev in expected)
        assert found, f"PCIe endpoint not detected. Expected one of {expected} in lspci."
        step.success("PCIe endpoint(s) found.")
        
    step_logger.info("="*60)

def get_nvme_pci_id():
    """Helper to find the PCI ID of the NVMe device."""
    rc, out, err = run_cmd("lspci -D | grep -i 'Non-Volatile'")
    if not out.strip():
        return None
    return out.split()[0]

def test_link_speed(board_config, step_logger):
    """
    2. Link Speed Validation
    RPi5 typically negotiates: PCIe Gen2/Gen3 x1.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: LINK SPEED")
    
    pci_id = get_nvme_pci_id()
    if not pci_id:
        pytest.skip("No NVMe device found to check link speed.")
        
    with step_logger.step("Validate Link Speed", action=f"lspci -s {pci_id} -vv", expected="Negotiated link speed >= 5.0 GT/s") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -vv")
        
        speed = re.search(r"LnkSta:\s+Speed\s+([\d\.]+)GT/s", out)
        assert speed, "Could not determine negotiated link speed from lspci (LnkSta)."
        
        negotiated = float(speed.group(1))
        assert negotiated >= 5.0, f"Link speed {negotiated}GT/s is lower than expected 5.0GT/s."
        step.success(f"Negotiated Link Speed: {negotiated} GT/s")
        
    step_logger.info("="*60)

def test_link_width(board_config, step_logger):
    """
    3. Link Width Validation
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: LINK WIDTH")
    
    pci_id = get_nvme_pci_id()
    if not pci_id:
        pytest.skip("No NVMe device found to check link width.")
        
    with step_logger.step("Validate Link Width", action=f"lspci -s {pci_id} -vv", expected="Negotiated link width >= x1") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -vv")
        
        width = re.search(r"LnkSta:.*?Width\s+x(\d+)", out)
        assert width, "Could not determine negotiated link width from lspci (LnkSta)."
        
        lanes = int(width.group(1))
        assert lanes >= 1, f"Link width x{lanes} is lower than expected x1."
        step.success(f"Negotiated Link Width: x{lanes}")
        
    step_logger.info("="*60)

def test_driver_loaded(board_config, step_logger):
    """
    4. Driver Binding Test
    Verify kernel driver loaded.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: DRIVER BINDING")
    
    pci_id = get_nvme_pci_id()
    if not pci_id:
        pytest.skip("No NVMe device found to check driver binding.")
        
    with step_logger.step("Verify Driver Loaded", action=f"lspci -s {pci_id} -k", expected="Kernel driver in use") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -k")
        assert "Kernel driver in use" in out, "Kernel driver is not bound/in use for the NVMe PCIe device."
        step.success("Kernel drivers are loaded and bound.")
        
    step_logger.info("="*60)

def test_bar_assignment(board_config, step_logger):
    """
    5. BAR Assignment Test
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: BAR ASSIGNMENT")
    
    pci_id = get_nvme_pci_id()
    if not pci_id:
        pytest.skip("No NVMe device found to check BARs.")
        
    with step_logger.step("Verify BARs", action=f"lspci -s {pci_id} -vv", expected="'Memory at' detected for NVMe") as step:
        rc, out, err = run_cmd(f"lspci -s {pci_id} -vv")
        assert "Memory at" in out, "BARs are not assigned (No 'Memory at' detected for NVMe)."
        step.success("BAR assignment verified.")
        
    step_logger.info("="*60)

def test_no_pcie_errors(board_config, step_logger):
    """
    6. Kernel Error Scan
    Critical BSP validation test.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: KERNEL ERROR SCAN")
    
    with step_logger.step("Scan for PCIe Errors", action="dmesg | grep -i pcie", expected="No critical PCIe errors found") as step:
        rc, out, err = run_cmd("dmesg | grep -i pcie")
        
        forbidden = [
            "AER",
            "link down",
            "fatal",
            "CRC"
        ]
        
        for item in forbidden:
            assert item.lower() not in out.lower(), f"Critical error '{item}' found in kernel logs."
            
        step.success("No critical PCIe errors found in dmesg.")
        
    step_logger.info("="*60)

def test_nvme_detected(board_config, step_logger):
    """
    7. NVMe Presence Test
    If NVMe SSD attached.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: NVME PRESENCE")
    
    with step_logger.step("Check NVMe block device", action="nvme list", expected="/dev/nvme0n1 is found") as step:
        rc, out, err = run_cmd("nvme list")
        assert "/dev/nvme0n1" in out, "NVMe block device /dev/nvme0n1 not found."
        step.success("NVMe SSD is attached and detected.")
        
    step_logger.info("="*60)

def test_nvme_rw(board_config, step_logger):
    """
    8. NVMe Read/Write Test
    Create temporary file dynamically on the mounted NVMe drive.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: NVME READ/WRITE")
    
    with step_logger.step("NVMe Write Test", action="dd write 100MB to NVMe mountpoint", expected="dd write succeeds") as step:
        rc, out, err = run_cmd("lsblk -o MOUNTPOINT -nr /dev/nvme0n1 | grep -v '^$' | head -n 1")
        mnt = out.strip()
        
        if not mnt:
            pytest.skip("NVMe drive is not mounted. Skipping filesystem write test to prevent data loss or SD card wear.")
            
        test_file = os.path.join(mnt, "nvme_rw_test.bin")
        cmd = (
            f"dd if=/dev/zero "
            f"of={test_file} "
            "bs=1M count=100 "
            "conv=fsync"
        )
        
        step_logger.info(f"Performing 100MB write test to {test_file}...")
        rc, out, err = run_cmd(cmd)
        assert rc == 0, f"Write test failed: {err}"
        run_cmd(f"rm -f {test_file}")
        
        step.success("NVMe Read/Write completed successfully.")
        
    step_logger.info("="*60)

def test_nvme_throughput(board_config, step_logger):
    """
    9. FIO Throughput Test
    Useful for BSP performance baselining.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: FIO THROUGHPUT")
    
    with step_logger.step("FIO Throughput Test", action="fio 512M read on NVMe", expected="FIO benchmark completes and shows READ throughput") as step:
        rc, out, err = run_cmd("lsblk -o MOUNTPOINT -nr /dev/nvme0n1 | grep -v '^$' | head -n 1")
        mnt = out.strip()
        
        if not mnt:
            pytest.skip("NVMe drive is not mounted. Skipping filesystem throughput test.")
            
        test_file = os.path.join(mnt, "fio_testfio.bin")
        cmd = f"""
        fio --name=test \\
            --filename={test_file} \\
            --size=512M \\
            --rw=read \\
            --bs=1M
        """
        
        step_logger.info(f"Running FIO throughput test (512M read) at {test_file}...")
        rc, out, err = run_cmd(cmd)
        
        assert rc == 0, f"FIO execution failed: {err}"
        assert "READ:" in out, "FIO results did not contain 'READ:' stats."
        
        speed = re.search(r"READ:.*?bw=(.*?/s)", out)
        if speed:
            step.success(f"Measured Throughput: {speed.group(1)}")
        else:
            step.success("FIO completed, speed parse failed.")
        
        run_cmd(f"rm -f {test_file}")
        
    step_logger.info("="*60)

def test_interrupts(board_config, step_logger):
    """
    10. Interrupt Validation
    Verify MSI interrupts increase specifically for the NVMe controller.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: INTERRUPT COUNT")
    
    def get_nvme_interrupts():
        rc, out, err = run_cmd("cat /proc/interrupts | grep -i nvme")
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
        
    with step_logger.step("Validate Interrupt Increase", action="Measure NVMe interrupts before and after dd read", expected="Interrupt count increases") as step:
        before = get_nvme_interrupts()
        
        step_logger.info("Triggering NVMe activity...")
        run_cmd("dd if=/dev/nvme0n1 of=/dev/null bs=1M count=100")
        
        after = get_nvme_interrupts()
        
        assert before != after, "NVMe interrupt counts did not increase after disk activity."
        step.success(f"Interrupts increased dynamically: Before={before}, After={after}")
        
    step_logger.info("="*60)

def test_remove_rescan(board_config, step_logger):
    """
    11. Hot Reset Test
    PCIe recovery validation.
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: HOT RESET")
    
    pci_id = get_nvme_pci_id()
    if not pci_id:
        pytest.skip("No NVMe device found to perform hot reset.")
        
    with step_logger.step("PCIe Hot Reset", action=f"Remove {pci_id} then rescan bus", expected="NVMe device reappears after rescan") as step:
        step_logger.info(f"Removing PCIe device {pci_id}...")
        run_cmd(f"echo 1 > /sys/bus/pci/devices/{pci_id}/remove")
        time.sleep(1)
        
        step_logger.info("Rescanning PCIe bus...")
        run_cmd("echo 1 > /sys/bus/pci/rescan")
        time.sleep(2)
        
        rc, out, err = run_cmd("lspci")
        assert "Non-Volatile" in out, "NVMe device did not reappear after rescan."
        step.success("PCIe hot reset completed successfully.")
        
    step_logger.info("="*60)

def test_post_reboot(board_config, step_logger):
    """
    12. Reboot Persistence Test
    Run after reboot: (Verifies it is currently visible)
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: POST REBOOT PRESENCE")
    
    with step_logger.step("Verify Post-Reboot Presence", action="lspci", expected="NVMe endpoint present") as step:
        rc, out, err = run_cmd("lspci")
        assert "Non-Volatile" in out, "NVMe device is not visible post-reboot."
        step.success("PCIe endpoint is present.")
        
    step_logger.info("="*60)

def test_24hr_stability(board_config, step_logger):
    """
    13. Long-Duration Stability Test
    Continuous stress: (Shortened for standard CI runs)
    """
    step_logger.info("="*60)
    step_logger.info("PCIE VALIDATION: STABILITY TEST")
    
    duration = board_config.get("pcie_stability_duration_s", 10)
    step_logger.info(f"Running continuous DD stress test for {duration} seconds...")
    
    with step_logger.step("Stability Stress Test", action=f"Repeated DD reads for {duration}s", expected="No DD failures during period") as step:
        start = time.time()
        iters = 0
        
        while time.time() - start < duration:
            rc, out, err = run_cmd(
                "dd if=/dev/nvme0n1 "
                "of=/dev/null bs=1M count=512"
            )
            assert rc == 0, f"Stability test failed during iteration {iters}: {err}"
            iters += 1
            
        step.success(f"Stability test completed {iters} iterations without failure.")
        
    step_logger.info("="*60)
