import pytest
import yaml
import sys
import subprocess
import os
from datetime import datetime
import contextlib

def pytest_addoption(parser):
    """Allows us to pass the board target."""
    parser.addoption("--board", action="store", default="raspberry_pi_5")

def pytest_cmdline_main(config):
    """INTERCEPTOR: Hijacks the local pytest command and routes it to the target board."""
    
    # 1. If we are already executing ON the Pi, do not intercept. Let pytest run normally.
    if os.environ.get("RUNNING_ON_PI"):
        return None 

    board_arg = config.getoption("--board")
    
    with open("boards.yaml", "r") as f:
        configs = yaml.safe_load(f)
    
    target_boards = []
    if board_arg == "all":
        target_boards = [name for name, conf in configs.items() if isinstance(conf, dict) and "remote" in conf]
    else:
        target_boards = [b.strip() for b in board_arg.split(',')]
        
    if not target_boards:
        return None
        
    os.makedirs("logs", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    args = " ".join(config.invocation_params.args)
    
    test_name = "all"
    for arg in config.invocation_params.args:
        if not arg.startswith('-'):
            basename = os.path.basename(arg)
            if basename.startswith("test_") and basename.endswith(".py"):
                test_name = basename[5:-3] # Extract 'ethernet' from 'test_ethernet.py'
                break
            elif basename.endswith(".py"):
                test_name = basename[:-3]
                break

    from concurrent.futures import ThreadPoolExecutor
    import time
    import glob
    import json
    
    print(f"\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO]  [HOST] Starting concurrent test execution on boards: {', '.join(target_boards)}")
    
    def execute_on_board(board_name):
        board = configs.get(board_name)
        if not board or "remote" not in board:
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [WARN]  [{board_name}] No remote configuration found. Skipping.")
            return board_name, 0
            
        host = board["remote"]["host"]
        user = board["remote"]["user"]
        remote_dir = f"~/hw-val-framework"
        log_file = f"logs/{test_name}_{board_name}.log"
        json_out_file = f".report_{board_name}.json"
        
        def log_status(msg):
            print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO]  [{board_name}] {msg}")
            
        log_status(f"Deploying to {user}@{host}... Live logs at: {log_file}")
        
        # Cleanup any old parts from previous runs
        for f in glob.glob(f".report_part_{board_name}_*.json"):
            os.remove(f)
            
        session_part = 1
        
        @contextlib.contextmanager
        def host_iperf_server():
            proc = None
            if "ethernet" in test_name or "all" in test_name:
                log_status("Starting iperf3 server on host...")
                try:
                    proc = subprocess.Popen(["iperf3", "-s"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except FileNotFoundError:
                    log_status("[WARN] iperf3 not installed on host.")
            try:
                yield
            finally:
                if proc:
                    proc.terminate()
                    proc.wait()
        
        with open(log_file, "w") as log, host_iperf_server():
            log_status("Installing OS dependencies...")
            os_deps_cmd = f"ssh {user}@{host} 'sudo apt-get update && sudo apt install -y i2c-tools python3-venv python3-pip gpiod libgpiod-dev speedtest-cli iperf3 pciutils nvme-cli fio'"
            subprocess.run(os_deps_cmd, shell=True, stdout=log, stderr=subprocess.STDOUT)
            
            log_status("Syncing code to target...")
            sync_cmd = f"tar --exclude='venv' --exclude='__pycache__' --exclude='.pytest_cache' -czf - . | ssh {user}@{host} 'mkdir -p {remote_dir} && cd {remote_dir} && tar -xzf -'"
            subprocess.run(sync_cmd, shell=True, stdout=log, stderr=subprocess.STDOUT)
            
            log_status("Configuring Python Environment...")
            setup_cmd = f"ssh {user}@{host} 'cd {remote_dir} && python3 -m venv venv && source venv/bin/activate && pip install -q -r requirements.txt'"
            subprocess.run(setup_cmd, shell=True, stdout=log, stderr=subprocess.STDOUT)
            
            subprocess.run(f"ssh {user}@{host} 'rm -f {remote_dir}/pytest_attempted.txt'", shell=True, stdout=log, stderr=subprocess.STDOUT)
            
            log_status("Executing test suite in continuous session mode...")
            while True:
                # Wait for board to be online before starting
                board_online = False
                for _ in range(36): # 3 minutes
                    ping_proc = subprocess.run(f"ssh -o ConnectTimeout=3 {user}@{host} 'echo ready'", shell=True, capture_output=True)
                    if ping_proc.returncode == 0:
                        board_online = True
                        break
                    time.sleep(5)
                    
                if not board_online:
                    log_status("ERROR: Board failed to come online. Aborting run.")
                    return board_name, 1
                    
                if session_part > 1:
                    subprocess.run(f"scp -q {user}@{host}:{remote_dir}/.report.json .report_part_{board_name}_{session_part-1}.json 2>/dev/null", shell=True)

                run_cmd = f"ssh {user}@{host} 'cd {remote_dir} && source venv/bin/activate && export RUNNING_ON_PI=1 && pytest {args} --board={board_name} --tb=short -v -s -o asyncio_default_fixture_loop_scope=function'"
                test_proc = subprocess.Popen(run_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                
                for line in test_proc.stdout:
                    log.write(line)
                    log.flush()
                test_proc.wait()
                
                subprocess.run(f"scp -q {user}@{host}:{remote_dir}/.report.json .report_part_{board_name}_{session_part}.json 2>/dev/null", shell=True)
                
                if test_proc.returncode in (2, 255):
                    log_status(f"Pytest Session Halted (Code {test_proc.returncode} - Reboot triggered). Waiting to recover...")
                    if test_proc.returncode == 2:
                        time.sleep(20)
                    session_part += 1
                    continue
                else:
                    log_status("Pulling final HTML and XML test reports...")
                    subprocess.run(f"scp -q -r {user}@{host}:{remote_dir}/logs/pytest_html_report ./logs/pytest_html_report_{board_name} 2>/dev/null", shell=True)
                    subprocess.run(f"scp -q {user}@{host}:{remote_dir}/logs/test-results.xml ./logs/test-results_{board_name}.xml 2>/dev/null", shell=True)
                    
                    parts = sorted(glob.glob(f".report_part_{board_name}_*.json"))
                    merged = None
                    for part in parts:
                        try:
                            with open(part, "r") as f:
                                data = json.load(f)
                            if merged is None:
                                merged = data
                            else:
                                merged["duration"] += data.get("duration", 0)
                                for k, v in data.get("summary", {}).items():
                                    if k == "collected":
                                        merged.setdefault("summary", {})[k] = max(merged.get("summary", {}).get(k, 0), v)
                                    else:
                                        merged.setdefault("summary", {})[k] = merged.get("summary", {}).get(k, 0) + v
                                    merged.setdefault("tests", []).extend(data.get("tests", []))
                        except Exception:
                            pass
                            
                    if merged:
                        with open(json_out_file, "w") as f:
                            json.dump(merged, f, indent=2)
                        
                    for part in parts:
                        os.remove(part)
                    
                    log_status("Remote execution complete.")
                    return board_name, test_proc.returncode

    overall_exit_code = 0
    with ThreadPoolExecutor(max_workers=len(target_boards)) as executor:
        futures = [executor.submit(execute_on_board, b) for b in target_boards]
        for future in futures:
            board_name, rc = future.result()
            if rc != 0:
                overall_exit_code = 1
                
    if os.path.exists("agent.py"):
        print("\n" + "="*60)
        print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO]  [HOST] Launching Autonomous AI Agent for Analysis...")
        subprocess.run(["python3", "agent.py"])
        
    sys.exit(overall_exit_code)


# --- Hardware Fixtures (Executed only on the Pi) ---

@pytest.fixture(scope="session")
def board_config(request):
    """Reads the boards.yaml file."""
    board = request.config.getoption("--board")
    with open("boards.yaml", "r") as file:
        configs = yaml.safe_load(file)
    return configs[board]

@pytest.fixture(scope="function")
def loopback_pins(board_config):
    """Generic hardware setup using the modern gpiod v2 API."""
    import gpiod
    from gpiod.line import Direction
    
    out_pin = board_config["out_pin"]
    in_pin = board_config["in_pin"]
    
    # v2 API: We request multiple lines in one atomic action
    request = gpiod.request_lines(
        board_config["chip"],
        consumer="pytest_loopback",
        config={
            out_pin: gpiod.LineSettings(direction=Direction.OUTPUT),
            in_pin: gpiod.LineSettings(direction=Direction.INPUT)
        }
    )
    
    yield request
    
    request.release()

def pytest_runtest_teardown(item):
    """Incremental Save: Save JSON report after every test so data isn't lost if the kernel panics."""
    if os.environ.get("RUNNING_ON_PI"):
        json_plugin = item.config.pluginmanager.getplugin("json-report")
        if json_plugin and hasattr(json_plugin, "report"):
            import json
            try:
                with open(".report.json", "w") as f:
                    json.dump(json_plugin.report, f)
            except Exception:
                pass

def pytest_runtest_setup(item):
    """Tracker: Record test as attempted before it runs, so we skip it upon reboot resumption."""
    if os.environ.get("RUNNING_ON_PI"):
        with open("pytest_attempted.txt", "a") as f:
            f.write(item.nodeid + "\n")

def pytest_collection_modifyitems(config, items):
    """Tracker: Remove already attempted tests from the queue upon resumption."""
    if os.environ.get("RUNNING_ON_PI"):
        try:
            with open("pytest_attempted.txt", "r") as f:
                completed = set(f.read().splitlines())
            
            # Keep only items that haven't been attempted yet
            items[:] = [item for item in items if item.nodeid not in completed]
        except FileNotFoundError:
            pass
import contextlib

class TestLogger:
    def __init__(self, nodeid):
        self.nodeid = nodeid
        self.step_counter = 1
        self.is_first_log = True

    def _log(self, level, msg, indent=0):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"{timestamp} [{level:5}] "
        indent_str = "  " * indent
        
        if self.is_first_log:
            print()
            self.is_first_log = False
            
        for line in str(msg).split('\n'):
            print(f"{prefix}{indent_str}{line}", flush=True)

    def info(self, msg, indent=0):
        self._log("INFO", msg, indent)
        
    def error(self, msg, indent=0):
        self._log("ERROR", msg, indent)
        
    def pass_mark(self, msg, indent=0):
        self._log("PASS", msg, indent)

    def fail_mark(self, msg, indent=0):
        self._log("FAIL", msg, indent)

    def skip_mark(self, msg, indent=0):
        self._log("SKIP", msg, indent)

    @contextlib.contextmanager
    def step(self, name, action, expected):
        step_num = self.step_counter
        self.step_counter += 1
        
        self.info(f"STEP {step_num}: {name}")
        self.info(f"Action: {action}", indent=1)
        self.info(f"Expected: {expected}", indent=1)
        
        class StepResult:
            def __init__(self):
                self.actual = None
            def success(self, actual):
                self.actual = actual
        
        result = StepResult()
        
        try:
            yield result
            if result.actual is not None:
                self.info(f"Actual: {result.actual}", indent=1)
            else:
                self.info(f"Actual: Matches expected", indent=1)
            self.pass_mark(f"STEP {step_num}: {name}", indent=1)
        except AssertionError as e:
            self.error(f"Actual: Assertion Failed - {str(e)}", indent=1)
            self.fail_mark(f"STEP {step_num}: {name}", indent=1)
            raise
        except BaseException as e:
            if type(e).__name__ == "Skipped":
                self.info(f"Actual: Skipped - {str(e)}", indent=1)
                self.skip_mark(f"STEP {step_num}: {name}", indent=1)
                self.info("="*60)
                raise
            if isinstance(e, Exception):
                self.error(f"Actual: Exception Raised - {type(e).__name__}: {str(e)}", indent=1)
                self.fail_mark(f"STEP {step_num}: {name}", indent=1)
            raise

@pytest.fixture
def step_logger(request):
    return TestLogger(request.node.nodeid)
