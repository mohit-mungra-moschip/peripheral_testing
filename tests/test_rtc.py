import pytest
import subprocess
import os
import shutil
import time
from datetime import datetime, timezone
import re

# Shared file for pre/post reboot state
RETENTION_STATE_FILE = "/tmp/rtc_retention_state.txt"

def test_rtc_stage1_hardware_node(step_logger):
    """
    Stage 1: Verify RTC device node exists, is accessible,
    and the correct driver is loaded.
    """
    import stat
    import grp

    step_logger.info("="*60)
    step_logger.info("RTC STAGE 1: Hardware Node & Driver Check")

    rtc_node  = "/dev/rtc0"
    rtc_sysfs = "/sys/class/rtc/rtc0"

    with step_logger.step("Check Device Node Existence", action=f"Check {rtc_node}", expected="Node exists") as step:
        assert os.path.exists(rtc_node), \
            f"/dev/rtc0 does not exist.\\n" \
            f"Fix: add 'dtoverlay=i2c-rtc,ds3231' (or your RTC model) " \
            f"to /boot/firmware/config.txt and reboot."
        step.success("Device node exists.")

    try:
        rtc_stat   = os.stat(rtc_node)
        rtc_gid    = rtc_stat.st_gid
        rtc_mode   = rtc_stat.st_mode
        rtc_group  = grp.getgrgid(rtc_gid).gr_name
        perms_str  = oct(rtc_mode)[-3:]
        group_read = bool(rtc_mode & 0o040)
        step_logger.info(f"/dev/rtc0 permissions: {perms_str} owner group: '{rtc_group}' (GID {rtc_gid})")
    except Exception as e:
        rtc_group  = "root"
        perms_str  = "unknown"
        group_read = False
        step_logger.info(f"[WARN] Could not read node metadata: {e}.")

    with step_logger.step("Check Node Permissions", action=f"Check os.R_OK for {rtc_node}", expected="Node is readable") as step:
        if not os.access(rtc_node, os.R_OK):
            try:
                current_user = os.getlogin()
            except Exception:
                current_user = os.environ.get("USER", "$USER")

            if rtc_group == "root" or not group_read:
                pytest.fail(
                    f"/dev/rtc0 is not readable by user '{current_user}'.\\n"
                    f"Permissions: {perms_str}  Group: '{rtc_group}'\\n\\n"
                    f"The group permission bits are 0 — adding to '{rtc_group}' group will NOT fix this.\\n\\n"
                    f"Fix — create a udev rule:\\n"
                    f"  sudo sh -c 'echo \"SUBSYSTEM==\\\"rtc\\\", KERNEL==\\\"rtc0\\\", MODE=\\\"0664\\\", GROUP=\\\"dialout\\\"\" > /etc/udev/rules.d/99-rtc.rules'\\n"
                    f"  sudo udevadm control --reload-rules\\n"
                    f"  sudo udevadm trigger\\n\\n"
                )
            else:
                try:
                    group_members  = grp.getgrnam(rtc_group).gr_mem
                    already_member = current_user in group_members
                except Exception:
                    already_member = False

                if already_member:
                    pytest.fail(
                        f"/dev/rtc0 not readable — '{current_user}' is in group '{rtc_group}' but the session predates the group change.\\n\\n"
                        f"Fix: log out and back in (or reboot) to apply group membership.\\n"
                    )
                else:
                    pytest.fail(
                        f"/dev/rtc0 not readable by user '{current_user}'.\\n"
                        f"Fix: sudo usermod -a -G {rtc_group} {current_user}\\n"
                    )
        step.success(f"/dev/rtc0 exists and is readable (group: '{rtc_group}').")

    with step_logger.step("Check Sysfs Driver Info", action=f"Check {rtc_sysfs}", expected="Driver is loaded") as step:
        if not os.path.exists(rtc_sysfs):
            pytest.fail(f"Sysfs entry {rtc_sysfs} missing. The kernel RTC driver is not loaded.")

        name_path = f"{rtc_sysfs}/name"
        if os.path.exists(name_path):
            with open(name_path) as f:
                driver_name = f.read().strip()
            step.success(f"RTC driver loaded: '{driver_name}'")
        else:
            step.success("Could not read driver name from sysfs.")

    with step_logger.step("Check hctosys", action=f"Read {rtc_sysfs}/hctosys", expected="hctosys value read") as step:
        hctosys_path = f"{rtc_sysfs}/hctosys"
        if os.path.exists(hctosys_path):
            with open(hctosys_path) as f:
                hctosys = f.read().strip()
            if hctosys == "1":
                step.success("RTC set system clock at boot (hctosys=1).")
            else:
                step.success("RTC did NOT set system clock at boot (hctosys=0).")
        else:
            step.success("hctosys not found")

    with step_logger.step("Check since_epoch", action=f"Read {rtc_sysfs}/since_epoch", expected="Year >= 2020") as step:
        since_epoch_path = f"{rtc_sysfs}/since_epoch"
        if os.path.exists(since_epoch_path):
            with open(since_epoch_path) as f:
                since_epoch = int(f.read().strip())
            rtc_year = datetime.fromtimestamp(since_epoch).year
            step_logger.info(f"RTC reports year: {rtc_year} (epoch seconds: {since_epoch})")
            assert rtc_year >= 2020, f"RTC reports year {rtc_year} — battery is likely DEAD."
            step.success("RTC epoch sanity check passed.")
        else:
            step.success("since_epoch not found")

    step_logger.info("="*60)


def test_rtc_stage2_drift_check(step_logger):
    """
    Stage 2: Read hardware clock and measure drift against system clock.
    Validates RTC is ticking and within acceptable tolerance.
    """
    step_logger.info("="*60)
    step_logger.info("RTC STAGE 2: Live Register Read & Drift Check")

    hwclock_path = shutil.which("hwclock")
    if not hwclock_path:
        for fallback in ["/sbin/hwclock", "/usr/sbin/hwclock"]:
            if os.path.exists(fallback):
                hwclock_path = fallback
                break
    if not hwclock_path:
        pytest.fail("hwclock not found. Install: sudo apt install util-linux")

    with step_logger.step("Hardware Drift Check", action="Compare hwclock to system clock", expected="Drift < 2.0s") as step:
        try:
            rtc_raw = subprocess.check_output(["sudo", hwclock_path, "--show"], text=True).strip()
            step_logger.info(f"Raw hwclock output: {rtc_raw}")
        except subprocess.CalledProcessError as e:
            pytest.fail(f"hwclock --show failed: {e}")

        sys_now = datetime.now()

        try:
            cleaned = rtc_raw.split(".")[0].strip()
            try:
                rtc_now = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                rtc_now = datetime.strptime(cleaned, "%a %d %b %Y %I:%M:%S %p")
        except ValueError as e:
            pytest.fail(f"Cannot parse hwclock output: '{rtc_raw}'\\nError: {e}")

        delta = abs((sys_now - rtc_now).total_seconds())

        step_logger.info(f"System Time:   {sys_now.strftime('%Y-%m-%d %H:%M:%S')}")
        step_logger.info(f"Hardware Time: {rtc_now.strftime('%Y-%m-%d %H:%M:%S')}")
        step_logger.info(f"Delta:         {delta:.3f} seconds")

        assert delta < 2.0, f"RTC drift too large: {delta:.3f}s (threshold: 2.0s)."
        step.success("RTC matches system clock within tolerance.")

    step_logger.info("="*60)


def test_rtc_stage3_pre_reboot_stamp(request, step_logger):
    """
    Stage 3: Write a timestamp to the RTC and to a state file.
    """
    step_logger.info("="*60)
    step_logger.info("RTC STAGE 3: Pre-Reboot Timestamp Stamp")

    hwclock_path = shutil.which("hwclock")
    if not hwclock_path:
        for fallback in ["/sbin/hwclock", "/usr/sbin/hwclock"]:
            if os.path.exists(fallback):
                hwclock_path = fallback
                break
    if not hwclock_path:
        pytest.fail("hwclock not found.")

    with step_logger.step("Sync System to RTC", action="hwclock --systohc", expected="Sync successful") as step:
        try:
            subprocess.check_call(["sudo", hwclock_path, "--systohc"])
            step.success("System time synced to RTC (hwclock --systohc).")
        except subprocess.CalledProcessError as e:
            pytest.fail(f"Failed to sync system → RTC: {e}")

    with step_logger.step("Write Pre-Reboot Stamp", action="Write stamp to state file", expected="State file saved") as step:
        stamp_epoch = int(time.time())
        stamp_str   = datetime.fromtimestamp(stamp_epoch).strftime("%Y-%m-%d %H:%M:%S")

        state_path = os.path.expanduser("~/rtc_retention_state.txt")
        with open(state_path, "w") as f:
            f.write(f"stamp_epoch={stamp_epoch}\n")
            f.write(f"stamp_str={stamp_str}\n")

        step_logger.info(f"Timestamp written to RTC: {stamp_str} (epoch {stamp_epoch})")
        step_logger.info(f"State file saved to: {state_path}")
        step.success("Pre-reboot state saved.")
        
    step_logger.info("Initiating scheduled reboot...")
    subprocess.Popen(
        "sudo sh -c 'sleep 5 && sudo reboot'", 
        shell=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    
    request.session.shouldstop = "Intentional reboot triggered"
    step_logger.info("="*60)


def test_rtc_stage4_post_reboot_verify(step_logger):
    """
    Stage 4: Read RTC after reboot and compare against pre-reboot stamp.
    """
    step_logger.info("="*60)
    step_logger.info("RTC STAGE 4: Post-Reboot Time Retention Verification")

    state_path = os.path.expanduser("~/rtc_retention_state.txt")

    with step_logger.step("Read Pre-Reboot State", action=f"Read {state_path}", expected="State file exists and is valid") as step:
        if not os.path.exists(state_path):
            pytest.fail(f"State file not found: {state_path}")

        state = {}
        with open(state_path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    state[k] = v

        assert "stamp_epoch" in state, "State file malformed — missing stamp_epoch."
        pre_epoch = int(state["stamp_epoch"])
        pre_str   = state.get("stamp_str", "unknown")

        step_logger.info(f"Pre-reboot stamp: {pre_str} (epoch {pre_epoch})")
        step.success("Pre-reboot state loaded.")

    with step_logger.step("Confirm Reboot Happened", action="Check uptime vs elapsed time", expected="Uptime < elapsed time") as step:
        elapsed_since_stamp = int(time.time()) - pre_epoch

        try:
            with open("/proc/uptime") as f:
                uptime_s = float(f.read().split()[0])

            step_logger.info(f"System uptime:      {uptime_s:.0f}s")
            step_logger.info(f"Since stamp written: {elapsed_since_stamp}s")

            if uptime_s > elapsed_since_stamp:
                pytest.fail(f"NO REBOOT DETECTED. System uptime ({uptime_s:.0f}s) is longer than time since stamp was written ({elapsed_since_stamp}s).")

        except FileNotFoundError:
            pass

        MIN_REBOOT_ELAPSED = 30
        if elapsed_since_stamp < MIN_REBOOT_ELAPSED:
            pytest.fail(f"NO REBOOT DETECTED. Only {elapsed_since_stamp}s elapsed since Stage 3.")

        step.success(f"Reboot confirmed: uptime={uptime_s:.0f}s < stamp age={elapsed_since_stamp}s.")

    with step_logger.step("Verify Post-Reboot RTC", action="hwclock --show", expected="RTC retained time across reboot") as step:
        hwclock_path = shutil.which("hwclock")
        if not hwclock_path:
            for fallback in ["/sbin/hwclock", "/usr/sbin/hwclock"]:
                if os.path.exists(fallback):
                    hwclock_path = fallback
                    break
        if not hwclock_path:
            pytest.fail("hwclock not found.")

        try:
            rtc_raw = subprocess.check_output(["sudo", hwclock_path, "--show"], text=True).strip()
        except subprocess.CalledProcessError as e:
            pytest.fail(f"hwclock --show failed post-reboot: {e}")

        post_now = datetime.now()

        try:
            cleaned = rtc_raw.split(".")[0].strip()
            try:
                rtc_post_dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                rtc_post_dt = datetime.strptime(cleaned, "%a %d %b %Y %I:%M:%S %p")
        except ValueError as e:
            pytest.fail(f"Cannot parse post-reboot hwclock output: '{rtc_raw}'\\nError: {e}")

        rtc_post_epoch = int(rtc_post_dt.timestamp())
        step_logger.info(f"Post-reboot RTC: {rtc_post_dt.strftime('%Y-%m-%d %H:%M:%S')} (epoch {rtc_post_epoch})")

        assert rtc_post_dt > datetime(2020, 1, 1), f"RTC reset to near-epoch: {rtc_post_dt}."
        step_logger.info("RTC did not reset to epoch — battery is alive.")

        elapsed_rtc   = rtc_post_epoch - pre_epoch
        elapsed_wall  = int(post_now.timestamp()) - pre_epoch
        elapsed_delta = abs(elapsed_rtc - elapsed_wall)

        step_logger.info(f"Elapsed on RTC:  {elapsed_rtc}s")
        step_logger.info(f"Elapsed on wall: {elapsed_wall}s")
        step_logger.info(f"Drift:           {elapsed_delta}s")

        assert elapsed_rtc > 0, f"RTC time did not advance after confirmed reboot (elapsed={elapsed_rtc}s)."

        max_drift = max(10, elapsed_wall * 0.01)
        assert elapsed_delta < max_drift, f"RTC drift too large: {elapsed_delta}s over {elapsed_wall}s elapsed."

        sys_rtc_delta = abs((post_now - rtc_post_dt).total_seconds())
        assert sys_rtc_delta < 5.0, f"Post-reboot RTC differs from system clock by {sys_rtc_delta:.1f}s."

        step.success("RTC retained time across reboot.")

    os.remove(state_path)
    step_logger.info("State file cleaned up.")
    step_logger.info("="*60)


def test_rtc_battery_power_off_retention(step_logger):
    """
    Stage 3: Validates the physical RTC battery retained the clock state 
    while main power was completely severed.
    """
    step_logger.info("="*60)
    step_logger.info("RTC VALIDATION - STAGE 3: TRUE POWER-OFF RETENTION")
    
    if not os.environ.get("RUN_MANUAL_POWER_OFF_TEST"):
        pytest.skip("Manual test requires physical power removal and pre-setting the RTC to 2036. Set RUN_MANUAL_POWER_OFF_TEST=1 to run.")
        
    with step_logger.step("Verify NTP is inactive", action="timedatectl status", expected="NTP is inactive") as step:
        try:
            timedate_out = subprocess.check_output(["timedatectl", "status"], text=True)
            if "NTP service: active" in timedate_out or "System clock synchronized: yes" in timedate_out:
                pytest.fail("Test Invalidated: NTP is active! The OS synced the time via Wi-Fi/Ethernet, masking the battery status.")
            step.success("NTP is inactive.")
        except subprocess.CalledProcessError:
            step.success("Could not verify NTP status, proceeding to raw hardware read...")

    with step_logger.step("Read Hardware Clock", action="hwclock --show", expected="Year >= 2036") as step:
        try:
            hwclock_path = subprocess.check_output(["sudo", "which", "hwclock"], text=True).strip()
            rtc_time_str = subprocess.check_output(["sudo", hwclock_path, "--show"], text=True).strip()
            
            cleaned_rtc_str = rtc_time_str.split(".")[0] 
            rtc_now = datetime.strptime(cleaned_rtc_str, "%Y-%m-%d %H:%M:%S")
        except Exception as e:
            pytest.fail(f"OS Error: Failed to cleanly read hardware clock: {e}")

        step_logger.info(f"Retrieved Hardware Time: {rtc_now.strftime('%Y-%m-%d %H:%M:%S')}")

        assert rtc_now.year >= 2036, f"Battery Failure! Expected year >= 2036, but got {rtc_now.year}."
        step.success("RTC battery successfully retained the clock registers through a complete power loss!")
        
    step_logger.info("="*60)


def test_ntp_sync_interaction(step_logger):
    """
    Validates NTP sync interaction with the RTC:
    """
    step_logger.info("="*60)
    step_logger.info("NTP SYNC INTERACTION TEST")

    with step_logger.step("Enable NTP", action="timedatectl set-ntp true", expected="NTP enabled") as step:
        try:
            subprocess.check_call(["sudo", "timedatectl", "set-ntp", "true"], stdout=subprocess.DEVNULL)
            step.success("NTP enabled via timedatectl.")
        except subprocess.CalledProcessError as e:
            pytest.fail(f"Failed to enable NTP: {e}")

    with step_logger.step("Wait for Sync", action="Wait for System clock synchronized: yes", expected="Sync complete") as step:
        SYNC_TIMEOUT   = 60
        POLL_INTERVAL  = 3
        synchronized   = False
        elapsed        = 0

        while elapsed < SYNC_TIMEOUT:
            try:
                out = subprocess.check_output(["timedatectl", "status"], text=True)
                if re.search(r"System clock synchronized:\s*yes", out, re.IGNORECASE):
                    synchronized = True
                    break
            except subprocess.CalledProcessError:
                pass

            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            step_logger.info(f"Waiting for sync... ({elapsed}s / {SYNC_TIMEOUT}s)")

        assert synchronized, f"Clock NOT synchronized after {SYNC_TIMEOUT}s."
        step.success(f"Clock synchronized after {elapsed}s.")

    with step_logger.step("Check Full Status", action="timedatectl status", expected="NTP service: active") as step:
        try:
            timedate_out = subprocess.check_output(["timedatectl", "status"], text=True)
            step_logger.info(f"{timedate_out.strip()}")
        except subprocess.CalledProcessError:
            pytest.fail("Could not query timedatectl status.")

        ntp_active = bool(re.search(r"NTP service:\s*active", timedate_out, re.IGNORECASE))
        assert ntp_active, "NTP service is not active."
        step.success("NTP service active.")

    with step_logger.step("Check Offset", action="timedatectl show-timesync", expected="Offset < 500ms") as step:
        try:
            timesync_out = subprocess.check_output(["timedatectl", "show-timesync", "--no-pager"], text=True)
            server_match = re.search(r"ServerAddress=(.+)", timesync_out)
            server_addr  = server_match.group(1).strip() if server_match else "unknown"

            offset_match = re.search(r"NTPMessage=.*?offset=([-\d.]+)", timesync_out)
            if not offset_match:
                offset_match = re.search(r"Offset=([-\d.]+)", timesync_out)

            step_logger.info(f"NTP server: {server_addr}")

            if offset_match:
                offset_us  = float(offset_match.group(1))
                offset_ms  = offset_us / 1000
                step_logger.info(f"Clock offset: {offset_ms:.3f} ms")

                MAX_OFFSET_MS = 500
                assert abs(offset_ms) < MAX_OFFSET_MS, f"NTP offset too large: {offset_ms:.3f}ms (threshold: ±{MAX_OFFSET_MS}ms)."
                step.success(f"NTP offset within tolerance ({offset_ms:.3f}ms).")
            else:
                step.success("Could not extract offset from timesync output.")

        except (subprocess.CalledProcessError, FileNotFoundError):
            step.success("timedatectl show-timesync not available — skipping offset check.")

    with step_logger.step("Write NTP to RTC", action="hwclock --systohc", expected="Write successful") as step:
        hwclock_path = shutil.which("hwclock")
        if not hwclock_path:
            for fb in ["/sbin/hwclock", "/usr/sbin/hwclock"]:
                if os.path.exists(fb):
                    hwclock_path = fb
                    break
        assert hwclock_path, "hwclock not found."

        system_before = datetime.now()

        try:
            subprocess.check_call(["sudo", hwclock_path, "--systohc"])
            step.success("NTP-corrected system time written to RTC (hwclock --systohc).")
        except subprocess.CalledProcessError as e:
            pytest.fail(f"Failed to write system time to RTC: {e}")

    with step_logger.step("Verify Write-Back", action="Compare hwclock to system clock", expected="Delta < 5.0s") as step:
        try:
            rtc_raw = subprocess.check_output(["sudo", hwclock_path, "--show"], text=True).strip()
        except subprocess.CalledProcessError as e:
            pytest.fail(f"hwclock --show failed: {e}")

        system_after = datetime.now()

        try:
            cleaned = rtc_raw.split(".")[0].strip()
            try:
                rtc_dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                rtc_dt = datetime.strptime(cleaned, "%a %d %b %Y %I:%M:%S %p")
        except ValueError as e:
            pytest.fail(f"Cannot parse hwclock output: '{rtc_raw}'\\nError: {e}")

        delta = abs((system_after - rtc_dt).total_seconds())

        step_logger.info(f"System time: {system_after.strftime('%Y-%m-%d %H:%M:%S')}")
        step_logger.info(f"RTC time:    {rtc_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        step_logger.info(f"Delta:       {delta:.3f}s")

        assert delta < 5.0, f"RTC does not match NTP-corrected system time. Delta: {delta:.3f}s (threshold: 5.0s)"
        step.success("RTC matches NTP-corrected system time.")

    step_logger.info("SUCCESS: Full NTP↔RTC interaction validated!")
    step_logger.info("="*60)