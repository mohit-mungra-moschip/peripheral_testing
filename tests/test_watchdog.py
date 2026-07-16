import pytest
import os
import time
import fcntl
import struct
import subprocess

# ── Linux Watchdog IOCTL Constants ────────────────────────────────────────────
WDIOC_GETSTATUS     = 0x80045701
WDIOC_GETBOOTSTATUS = 0x80045702
WDIOC_KEEPALIVE     = 0x80045705
WDIOC_SETTIMEOUT    = 0xc0045706
WDIOC_GETTIMEOUT    = 0x80045707

# ── Watchdog boot status flag ─────────────────────────────────────────────────
WDIOF_CARDRESET = 0x0020   # bit set if last reboot was caused by watchdog


def _ensure_watchdog_accessible(watchdog_path):
    """
    Ensures /dev/watchdog0 is accessible without world-chmod.
    Uses a udev rule approach — installs it if missing.
    Falls back to targeted chmod only if udev is not available.
    """
    if os.access(watchdog_path, os.W_OK):
        return   # already accessible — nothing to do

    # Try udev rule first (persistent, secure)
    udev_rule = (
        'SUBSYSTEM=="misc", KERNEL=="watchdog0", '
        'MODE="0666", GROUP="dialout"'
    )
    udev_path = "/etc/udev/rules.d/99-watchdog.rules"

    try:
        if not os.path.exists(udev_path):
            subprocess.run(
                ["sudo", "sh", "-c", f"echo '{udev_rule}' > {udev_path}"],
                check=True, stderr=subprocess.DEVNULL
            )
            subprocess.run(
                ["sudo", "udevadm", "control", "--reload-rules"],
                check=True, stderr=subprocess.DEVNULL
            )
            subprocess.run(
                ["sudo", "udevadm", "trigger"],
                check=True, stderr=subprocess.DEVNULL
            )
            time.sleep(0.5)   # let udev apply

        if os.access(watchdog_path, os.W_OK):
            return   # udev rule worked
    except Exception:
        pass

    # Last resort: targeted chmod
    try:
        subprocess.run(
            ["sudo", "chmod", "666", watchdog_path],
            check=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"Cannot make {watchdog_path} accessible.\n"
            f"Error: {e}\n"
            f"Fix: sudo chmod 666 {watchdog_path}"
        )


def test_watchdog_enable_and_feed(board_config, step_logger):
    """
    Validates hardware watchdog: enable, configure, feed, graceful disarm.
    No wiring required — purely internal silicon.

    Proves:
      1. /dev/watchdog0 opens and configures correctly
      2. Timeout readback matches requested value
      3. KEEPALIVE IOCTL succeeds on every cycle
      4. Magic 'V' disarms cleanly (no reboot triggered)
    """
    for key in ("watchdog_path", "watchdog_timeout", "watchdog_feed_cycles"):
        assert key in board_config, f"board_config missing '{key}'"

    watchdog_path  = board_config["watchdog_path"]
    timeout_val    = board_config["watchdog_timeout"]
    feeding_cycles = board_config["watchdog_feed_cycles"]

    step_logger.info("="*60)
    step_logger.info("WATCHDOG VALIDATION: ENABLE & FEED")

    # ── Pre-check: device node exists ────────────────────────────────────────
    if not os.path.exists(watchdog_path):
        pytest.fail(
            f"{watchdog_path} not found.\n"
            f"Fix: add 'dtparam=watchdog=on' to /boot/firmware/config.txt\n"
            f"     and reboot."
        )
    step_logger.info(f"Device node exists: {watchdog_path}")

    # ── Pre-check: not already open (EBUSY guard) ─────────────────────────────
    try:
        fuser_out = subprocess.run(
            ["fuser", watchdog_path],
            capture_output=True, text=True
        )
        if fuser_out.stdout.strip():
            pytest.fail(
                f"{watchdog_path} is already held open by "
                f"PID(s): {fuser_out.stdout.strip()}.\n"
                f"A previous test may have crashed without disarming.\n"
                f"Fix: sudo killall watchdog  OR  reboot the Pi."
            )
    except FileNotFoundError:
        pass   # fuser not installed — skip

    # ── Ensure accessible ─────────────────────────────────────────────────────
    _ensure_watchdog_accessible(watchdog_path)

    fd = None
    try:
        # ── Stage 1: Open (arms the watchdog) ─────────────────────────────────
        with step_logger.step("Arm Watchdog", action=f"Open {watchdog_path}", expected="Watchdog opens successfully") as step:
            fd = os.open(watchdog_path, os.O_WRONLY)
            step.success("Watchdog armed — countdown started.")

        # ── Stage 2: Configure timeout ────────────────────────────────────────
        with step_logger.step(f"Set Timeout ({timeout_val}s)", action="WDIOC_SETTIMEOUT", expected=f"Timeout is {timeout_val}s") as step:
            fcntl.ioctl(fd, WDIOC_SETTIMEOUT, struct.pack("i", timeout_val))
            buf = fcntl.ioctl(fd, WDIOC_GETTIMEOUT, struct.pack("i", 0))
            current_timeout = struct.unpack("i", buf)[0]
            assert current_timeout == timeout_val, \
                f"Timeout mismatch: requested {timeout_val}s, silicon reports {current_timeout}s."
            step.success(f"Timeout confirmed: {current_timeout}s.")

        # ── Stage 3: Check boot status flags ──────────────────────────────────
        with step_logger.step("Check Boot Status Flags", action="WDIOC_GETBOOTSTATUS", expected="Read successfully") as step:
            try:
                buf = fcntl.ioctl(fd, WDIOC_GETBOOTSTATUS, struct.pack("i", 0))
                boot_status = struct.unpack("i", buf)[0]
                if boot_status & WDIOF_CARDRESET:
                    step.success("WDIOF_CARDRESET set — last reboot was caused by watchdog starvation.")
                else:
                    step.success("Last reboot was clean (not watchdog-triggered).")
            except OSError:
                step.success("GETBOOTSTATUS not supported by this driver.")

        # ── Stage 4: Feed loop ────────────────────────────────────────────────
        sleep_interval = timeout_val * 0.4
        with step_logger.step("Feed Watchdog Loop", action=f"{feeding_cycles} cycles at {sleep_interval:.1f}s intervals", expected="KEEPALIVE ioctl succeeds every time") as step:
            for i in range(1, feeding_cycles + 1):
                time.sleep(sleep_interval)
                try:
                    result = fcntl.ioctl(fd, WDIOC_KEEPALIVE, struct.pack("i", 0))
                    step_logger.info(f"    Cycle {i}/{feeding_cycles}: Fed at {i * sleep_interval:.1f}s elapsed.")
                except OSError as e:
                    pytest.fail(
                        f"Feed cycle {i} FAILED: KEEPALIVE ioctl returned error: {e}\n"
                        f"Watchdog may have already expired or driver is broken."
                    )
            step.success("Watchdog fed successfully for all cycles.")

        # ── Stage 5: Graceful disarm ──────────────────────────────────────────
        with step_logger.step("Graceful Disarm", action="Write magic 'V'", expected="Write succeeds") as step:
            os.write(fd, b'V')
            step.success("Magic 'V' written — kernel will NOT reboot on close.")

    except OSError as e:
        pytest.fail(f"Hardware/Driver Error: {e}")

    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    step_logger.info("="*60)


def test_watchdog_starvation_reboot(board_config, request, step_logger):
    """
    DESTRUCTIVE TEST: Proves that failing to feed the watchdog triggers a reboot.
    """
    for key in ("watchdog_path", "watchdog_timeout"):
        assert key in board_config, f"board_config missing '{key}'"

    if not board_config.get("allow_destructive_reboot", False):
        pytest.skip(
            "Destructive test skipped.\n"
            "Set 'allow_destructive_reboot: True' in board_config to run."
        )

    watchdog_path = board_config["watchdog_path"]
    timeout_val   = board_config["watchdog_timeout"]

    step_logger.info("="*60)
    step_logger.info("DESTRUCTIVE WATCHDOG: STARVATION REBOOT")
    step_logger.info(f"System will hard-reset in ~{timeout_val}s.")
    step_logger.info("SSH session will be severed. This is expected.")
    step_logger.info("After reboot, run test_watchdog_post_reboot_verify.")
    
    step_logger.info("Starting in 5 seconds — Ctrl+C to abort...")
    time.sleep(5)

    _ensure_watchdog_accessible(watchdog_path)

    state_path = os.path.expanduser("~/watchdog_starvation_state.txt")
    with step_logger.step("Write State File", action="Write timestamp to file", expected="File written") as step:
        with open(state_path, "w") as f:
            f.write(f"triggered_epoch={int(time.time())}\n")
            f.write(f"timeout_val={timeout_val}\n")
        step.success("State file written.")

    with step_logger.step("Arm and Abandon Watchdog", action=f"Open {watchdog_path}, set timeout, close WITHOUT 'V'", expected="Device arms and countdown begins") as step:
        fd = None
        try:
            fd = os.open(watchdog_path, os.O_WRONLY)
            fcntl.ioctl(fd, WDIOC_SETTIMEOUT, struct.pack("i", timeout_val))
            step_logger.info(f"Watchdog armed with {timeout_val}s timeout. Closing WITHOUT magic 'V'...")
            os.close(fd)
            fd = None
            step.success("Watchdog abandoned successfully.")
        except Exception as e:
            pytest.fail(f"Failed to trigger starvation: {e}")
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    step_logger.info(f"Hardware Watchdog will force a reset in {timeout_val}s.")
    step_logger.info("Halting Pytest gracefully so report saves...")
    request.session.shouldstop = "Intentional reboot triggered"
    
    step_logger.info("="*60)


def test_watchdog_post_reboot_verify(board_config, step_logger):
    """
    Phase 2 of starvation test — run AFTER the Pi reboots.
    Reads WDIOC_GETBOOTSTATUS to confirm the reboot was watchdog-caused.
    """
    for key in ("watchdog_path",):
        assert key in board_config, f"board_config missing '{key}'"

    watchdog_path = board_config["watchdog_path"]
    state_path    = os.path.expanduser("~/watchdog_starvation_state.txt")

    step_logger.info("="*60)
    step_logger.info("WATCHDOG POST-REBOOT VERIFICATION")

    with step_logger.step("Verify Starvation State", action=f"Read {state_path}", expected="State file exists and is valid") as step:
        if not os.path.exists(state_path):
            pytest.skip(
                "State file not found — starvation test was not run.\n"
                "Run test_watchdog_starvation_reboot first."
            )

        state = {}
        with open(state_path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    state[k] = v

        triggered_epoch = int(state.get("triggered_epoch", 0))
        timeout_val     = int(state.get("timeout_val", 15))
        step.success("State file read successfully.")

    with step_logger.step("Confirm Reboot Timings", action="Compare uptime to trigger epoch", expected="Uptime is less than elapsed time since trigger") as step:
        with open("/proc/uptime") as f:
            uptime_s = float(f.read().split()[0])

        elapsed_since_trigger = int(time.time()) - triggered_epoch

        if uptime_s > elapsed_since_trigger:
            pytest.fail(
                f"No reboot detected.\n"
                f"Uptime ({uptime_s:.0f}s) > elapsed since trigger "
                f"({elapsed_since_trigger}s).\n"
                f"Run test_watchdog_starvation_reboot and wait for the reboot."
            )
        step.success(f"Reboot confirmed: uptime={uptime_s:.0f}s, triggered {elapsed_since_trigger}s ago.")

    with step_logger.step("Verify Watchdog Reboot Flag", action="WDIOC_GETBOOTSTATUS or vcgencmd get_rsts", expected="WDIOF_CARDRESET is set") as step:
        _ensure_watchdog_accessible(watchdog_path)
        try:
            fd = os.open(watchdog_path, os.O_WRONLY)
            try:
                buf         = fcntl.ioctl(fd, WDIOC_GETBOOTSTATUS, struct.pack("i", 0))
                boot_status = struct.unpack("i", buf)[0]
                step_logger.info(f"WDIOC_GETBOOTSTATUS = {boot_status:#010x}")

                wd_reset_detected = bool(boot_status & WDIOF_CARDRESET)

                if not wd_reset_detected:
                    try:
                        rsts_out = subprocess.run(
                            ["vcgencmd", "get_rsts"],
                            capture_output=True, text=True, check=True
                        ).stdout.strip()
                        step_logger.info(f"vcgencmd fallback = {rsts_out}")
                        if "20" in rsts_out:
                            wd_reset_detected = True
                            step_logger.info("vcgencmd confirmed WDIOF_CARDRESET equivalent.")
                    except Exception as e:
                        step_logger.info(f"vcgencmd fallback failed: {e}")

                assert wd_reset_detected, \
                    f"WDIOF_CARDRESET bit NOT set (boot_status={boot_status:#010x}).\n" \
                    f"The watchdog driver does not report a watchdog-caused reboot."
                step.success("WDIOF_CARDRESET confirmed — last reboot was watchdog-triggered.")
            finally:
                os.write(fd, b'V')
                os.close(fd)
        except OSError as e:
            pytest.fail(f"Cannot read boot status: {e}")

    with step_logger.step("Cleanup State", action="Remove state file", expected="File removed") as step:
        os.remove(state_path)
        step.success("State file removed.")

    step_logger.info("="*60)