import pytest
import time
import subprocess
import os
import threading
from datetime import datetime

def test_timer_jitter(board_config, step_logger):
    """
    Validates the hardware/OS timer precision by checking for sleep jitter.
    Expects standard 10ms sleeps to complete within an acceptable margin.
    """
    step_logger.info("="*60)
    step_logger.info("TIMER VALIDATION: JITTER TEST")

    target_sleep = board_config.get("timer_jitter_target_sleep", 0.01)
    iterations = board_config.get("timer_jitter_iterations", 100)
    acceptable_jitter_max = board_config.get("timer_acceptable_jitter_max", 0.005)

    with step_logger.step("Measure Timer Jitter", action=f"Sleep {target_sleep}s for {iterations} iterations", expected=f"Max jitter <= {acceptable_jitter_max}s") as step:
        differences = []

        for i in range(iterations):
            start = time.perf_counter()
            time.sleep(target_sleep)
            end = time.perf_counter()
            
            actual_sleep = end - start
            jitter = abs(actual_sleep - target_sleep)
            differences.append(jitter)

        max_jitter = max(differences)
        avg_jitter = sum(differences) / len(differences)

        step_logger.info(f"Jitter Stats over {iterations} iterations of {target_sleep*1000:.1f}ms sleep:")
        step_logger.info(f"  Max: {max_jitter*1000:.3f} ms")
        step_logger.info(f"  Avg: {avg_jitter*1000:.3f} ms")

        assert max_jitter <= acceptable_jitter_max, (
            f"Timer jitter exceeded acceptable threshold.\\n"
            f"Max jitter was {max_jitter*1000:.3f}ms (threshold: {acceptable_jitter_max*1000:.1f}ms)."
        )

        step.success("Timer jitter is within acceptable limits.")
        
    step_logger.info("="*60)


def test_timer_stress(board_config, step_logger):
    """
    Spawns multiple concurrent timers to ensure the system scheduler
    and timer queues handle high concurrency gracefully.
    """
    import random

    step_logger.info("="*60)
    step_logger.info("TIMER VALIDATION: STRESS TEST")

    thread_count = board_config.get("timer_stress_thread_count", 100)
    max_delay = board_config.get("timer_stress_max_delay", 1.0)
    results = [False] * thread_count
    
    def timer_callback(idx):
        results[idx] = True

    with step_logger.step("Concurrent Timers", action=f"Spawn {thread_count} timers up to {max_delay}s delay", expected="All timers fire successfully") as step:
        timers = []
        step_logger.info(f"Scheduling {thread_count} concurrent timers...")
        
        delays = [random.uniform(0.1, max_delay) for _ in range(thread_count)]
        
        for i in range(thread_count):
            t = threading.Timer(delays[i], timer_callback, args=(i,))
            timers.append(t)
            
        start_time = time.time()
        for t in timers:
            t.start()

        wait_time = max_delay + 0.5
        step_logger.info(f"Waiting {wait_time:.1f}s for all timers to fire...")
        time.sleep(wait_time)

        success_count = sum(results)
        
        step_logger.info(f"{success_count}/{thread_count} timers fired successfully.")
        
        assert success_count == thread_count, f"Stress test failed: only {success_count} out of {thread_count} timers fired."
        
        step.success(f"System handled {thread_count} concurrent timers.")
        
    step_logger.info("="*60)


def test_timer_cron(board_config, step_logger):
    """
    Validates that the cron daemon is actively monitoring the system clock
    and dispatching tasks at the correct minute boundaries.
    """
    step_logger.info("="*60)
    step_logger.info("TIMER VALIDATION: CRON TEST")

    marker_file = board_config.get("cron_test_marker_file", "/tmp/pytest_cron_test.txt")
    cron_job = f"* * * * * touch {marker_file}\n"
    
    if os.path.exists(marker_file):
        os.remove(marker_file)

    with step_logger.step("Backup Crontab", action="crontab -l", expected="Crontab read successfully") as step:
        try:
            backup_out = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            original_crontab = backup_out.stdout if backup_out.returncode == 0 else ""
            step.success("Backed up current crontab.")
        except Exception as e:
            pytest.fail(f"Could not read crontab: {e}")

    try:
        new_crontab = original_crontab + cron_job

        with step_logger.step("Inject Test Cron Job", action="crontab -", expected="Cron job injected successfully") as step:
            process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
            process.communicate(new_crontab)
            if process.returncode != 0:
                pytest.fail("Failed to install temporary crontab.")
            step.success("Injected test cron job.")

        with step_logger.step("Wait for Minute Boundary", action="Wait until next minute + 5s", expected="Marker file created by cron") as step:
            now = datetime.now()
            seconds_to_next_minute = 60 - now.second
            wait_time = seconds_to_next_minute + 5
            
            step_logger.info(f"Waiting {wait_time}s for the next minute boundary...")
            time.sleep(wait_time)

            assert os.path.exists(marker_file), (
                f"Cron job failed to create {marker_file} at the minute boundary.\n"
                f"Ensure the 'cron' daemon is running on the Pi."
            )

            step.success("Marker file created! Cron successfully fired.")

    finally:
        with step_logger.step("Restore Crontab", action="crontab - or crontab -r", expected="Crontab restored") as step:
            try:
                if original_crontab.strip():
                    process = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
                    process.communicate(original_crontab)
                else:
                    subprocess.run(["crontab", "-r"], capture_output=True)
                    
                if os.path.exists(marker_file):
                    os.remove(marker_file)
                step.success("Restored original crontab and cleaned up.")
            except Exception as e:
                step_logger.info(f"[WARN] Cleanup warning: {e}")

    step_logger.info("="*60)
