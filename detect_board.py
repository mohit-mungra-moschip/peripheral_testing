import yaml
import os
import argparse
import subprocess
import sys
import re

# Predefined standard templates for supported boards
TEMPLATES = {
    "raspberry_pi_5": {
        "chip": "/dev/gpiochip4",
        "out_pin": 17,
        "in_pin": 27,
        "mux_test_pin": 14,
        "expected_mux": "TXD0",
        "i2c_bus": 1,
        "spi_bus": 0,
        "spi_device": 0,
        "sclk_monitor_pin": 27,
        "mosi_monitor_pin": 22,
        "uart_port": "/dev/ttyAMA0",
        "uart_baud": 115200,
        "cts_driver_gpio": 26,
        "secondary_uart_port": "/dev/ttyAMA2",
        "watchdog_path": "/dev/watchdog0",
        "watchdog_timeout": 15,
        "watchdog_feed_cycles": 3,
        "allow_destructive_reboot": True,
        "timer_jitter_target_sleep": 0.01,
        "timer_jitter_iterations": 100,
        "timer_acceptable_jitter_max": 0.005,
        "timer_stress_thread_count": 100,
        "timer_stress_max_delay": 1.0,
        "cron_test_marker_file": "/tmp/pytest_cron_test.txt",
        "eth_interface": "eth0",
        "eth_ping_target": "8.8.8.8",
        "eth_max_latency_ms": 50.0,
        "eth_iperf_server": ""
    },
    "raspberry_pi_4": {
        "chip": "/dev/gpiochip4",
        "out_pin": 17,
        "in_pin": 27,
        "mux_test_pin": 14,
        "expected_mux": "TXD0",
        "i2c_bus": 1,
        "spi_bus": 0,
        "spi_device": 0,
        "sclk_monitor_pin": 27,
        "mosi_monitor_pin": 22,
        "uart_port": "/dev/ttyAMA0",
        "uart_baud": 115200,
        "cts_driver_gpio": 26,
        "secondary_uart_port": "/dev/ttyAMA1",
        "watchdog_path": "/dev/watchdog0",
        "watchdog_timeout": 15,
        "watchdog_feed_cycles": 3,
        "allow_destructive_reboot": True,
        "timer_jitter_target_sleep": 0.01,
        "timer_jitter_iterations": 100,
        "timer_acceptable_jitter_max": 0.005,
        "timer_stress_thread_count": 100,
        "timer_stress_max_delay": 1.0,
        "cron_test_marker_file": "/tmp/pytest_cron_test.txt",
        "eth_interface": "eth0",
        "eth_ping_target": "8.8.8.8",
        "eth_max_latency_ms": 50.0,
        "eth_iperf_server": ""
    }
}

def ai_generate_config(host, user, password, model_name):
    """Uses LLM to probe the remote board and generate a YAML configuration."""
    probe_commands = [
        "ls -1 /dev/gpiochip* 2>/dev/null || true",
        "ls -1 /dev/i2c* 2>/dev/null || true",
        "ls -1 /dev/spidev* 2>/dev/null || true",
        "ls -1 /dev/watchdog* 2>/dev/null || true",
        "ls -1 /dev/ttyAMA* /dev/ttyS* /dev/ttyUSB* 2>/dev/null || true",
        "ls -1 /sys/class/net/ 2>/dev/null || true"
    ]
    
    probe_results = ""
    for cmd in probe_commands:
        try:
            ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{user}@{host}", cmd]
            res = subprocess.run(ssh_cmd, capture_output=True, text=True, check=True)
            probe_results += f"$ {cmd}\n{res.stdout}\n"
        except Exception:
            pass
            
    print("[INFO]  [Detect] Sending hardware topology to AI for schema synthesis...")
    
    try:
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import SystemMessage, HumanMessage
        from dotenv import load_dotenv
        
        load_dotenv()
        
        llm_model = os.environ.get("MODEL_NAME", "openai/gpt-oss-120b:free")
        llm = ChatOpenAI(
            model=llm_model,
            temperature=0.1,
            openai_api_key=os.environ.get("OPENROUTER_API_KEY", "missing_key"),
            openai_api_base="https://openrouter.ai/api/v1"
        )
        
        prompt = f"""
        You are a Linux BSP validation expert. We are configuring a new hardware board: "{model_name}".
        We need to create a testing configuration profile in YAML format.
        
        Here is the hardware probe from the device:
        {probe_results}
        
        Map these discovered peripherals to our test framework schema. 
        Output ONLY a valid YAML configuration block (without markdown codeblock formatting if possible, or inside ```yaml).
        The schema requires these keys exactly as written:
          chip: (path to gpiochip, e.g., "/dev/gpiochip4")
          out_pin: (integer, guess a safe pin like 17)
          in_pin: (integer, guess a safe pin like 27)
          mux_test_pin: (integer, guess 14)
          expected_mux: (string, e.g. "TXD0")
          i2c_bus: (integer, e.g., 1 if /dev/i2c-1 exists)
          spi_bus: (integer, e.g., 0 if /dev/spidev0.0 exists)
          spi_device: (integer, e.g., 0)
          sclk_monitor_pin: (integer, guess 27)
          mosi_monitor_pin: (integer, guess 22)
          uart_port: (string, e.g., "/dev/ttyS0" or "/dev/ttyAMA0")
          uart_baud: 115200
          cts_driver_gpio: (integer, guess 26)
          secondary_uart_port: (string, guess another uart)
          watchdog_path: (string, e.g. "/dev/watchdog0")
          watchdog_timeout: 15
          watchdog_feed_cycles: 3
          allow_destructive_reboot: True
          timer_jitter_target_sleep: 0.01
          timer_jitter_iterations: 100
          timer_acceptable_jitter_max: 0.005
          timer_stress_thread_count: 100
          timer_stress_max_delay: 1.0
          cron_test_marker_file: "/tmp/pytest_cron_test.txt"
          eth_interface: (string, e.g. "eth0" or "end0")
          eth_ping_target: "8.8.8.8"
          eth_max_latency_ms: 50.0
          eth_iperf_server: ""
          
        Make educated guesses based on the hardware dump. If multiple devices exist, pick the primary one (e.g. index 0 or 1).
        DO NOT include the board name as the root key, just output the dictionary key-values. Do not add any extra keys.
        """
        
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        
        # Extract yaml from markdown if present
        yaml_match = re.search(r"```(?:yaml)?\n(.*?)\n```", content, re.DOTALL | re.IGNORECASE)
        if yaml_match:
            content = yaml_match.group(1).strip()
            
        config = yaml.safe_load(content)
        
        if not isinstance(config, dict):
            raise ValueError("Parsed AI output is not a dictionary.")
            
        return config
    except Exception as e:
        print(f"[ERROR] [Detect] AI Generation failed: {e}")
        return None

def discover_and_update_board(yaml_path="boards.yaml", host=None, user=None, password=None):
    """
    Connects to the target board, reads its devicetree model, 
    and updates boards.yaml if the board isn't configured yet.
    Returns the detected board key (e.g., 'raspberry_pi_5').
    """
    with open(yaml_path, "r") as f:
        configs = yaml.safe_load(f) or {}

    # If credentials are not provided, find the first remote in boards.yaml
    remote_config = None
    if host and user:
        remote_config = {"host": host, "user": user, "password": password or ""}
    else:
        for board, conf in configs.items():
            if isinstance(conf, dict) and "remote" in conf:
                remote_config = conf["remote"]
                break
                
    if not remote_config:
        print("[ERROR] [Detect] No remote configuration found in boards.yaml to perform discovery.")
        return None
        
    target_host = remote_config.get("host")
    target_user = remote_config.get("user")
    
    print(f"[INFO]  [Detect] Connecting to {target_user}@{target_host} to discover hardware...")
    
    try:
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", f"{target_user}@{target_host}", "cat /sys/firmware/devicetree/base/model"]
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=True)
        model_string = result.stdout.strip().rstrip('\x00')
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] [Detect] Failed to connect and read device tree. SSH exited with {e.returncode}")
        print(f"Error output: {e.stderr}")
        return None
    except Exception as e:
        print(f"[ERROR] [Detect] Failed to connect and read device tree: {e}")
        return None
        
    print(f"[INFO]  [Detect] Found hardware: {model_string}")
    
    detected_board = None
    if "Raspberry Pi 5" in model_string:
        detected_board = "raspberry_pi_5"
    elif "Raspberry Pi 4" in model_string:
        detected_board = "raspberry_pi_4"
    else:
        # Fallback to AI Generation
        clean_model_name = re.sub(r'[^a-zA-Z0-9]+', '_', model_string).strip('_').lower()
        detected_board = clean_model_name
        
        print(f"[WARN]  [Detect] Unrecognized board '{model_string}'. Invoking AI for synthesis...")
        ai_config = ai_generate_config(target_host, target_user, remote_config.get("password"), model_string)
        
        if ai_config:
            TEMPLATES[detected_board] = ai_config
            print(f"[PASS]  [Detect] AI successfully generated configuration for {detected_board}.")
            
            print("\n" + "="*60)
            print("[INFO]  AI-GENERATED CONFIGURATION (PLEASE REVIEW):")
            print("="*60)
            print(yaml.dump({detected_board: ai_config}, default_flow_style=False, sort_keys=False))
            print("="*60)
            
            try:
                sys.stdout.flush()
                # Pytest can sometimes suppress stdin, but during initialization (conftest) it often works
                # Read from /dev/tty directly to bypass pytest stdin capture if necessary
                try:
                    with open('/dev/tty', 'r') as tty:
                        print("Do you approve this configuration? (y/N): ", end="", flush=True)
                        choice = tty.readline().strip().lower()
                except OSError:
                    choice = input("Do you approve this configuration? (y/N): ").strip().lower()
                    
                if choice != 'y':
                    print("[ERROR] [Detect] User rejected AI configuration. Exiting.")
                    return None
            except Exception as e:
                print(f"[WARN]  [Detect] Could not prompt user: {e}. Proceeding cautiously...")
        else:
            print("[ERROR] [Detect] AI synthesis failed. Falling back to generic template.")
            detected_board = "generic_board"
            TEMPLATES["generic_board"] = TEMPLATES["raspberry_pi_5"].copy()

    # Update boards.yaml if the board isn't there
    if detected_board not in configs:
        print(f"[INFO]  [Detect] Adding new profile '{detected_board}' to {yaml_path}...")
        configs[detected_board] = TEMPLATES[detected_board].copy()
        configs[detected_board]["remote"] = remote_config
        
        with open(yaml_path, "w") as f:
            yaml.dump(configs, f, default_flow_style=False, sort_keys=False)
    else:
        print(f"[INFO]  [Detect] Profile '{detected_board}' already exists in {yaml_path}.")
            
    return detected_board

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-detect hardware board and update boards.yaml")
    parser.add_argument("--host", help="Target SSH host (IP/hostname)")
    parser.add_argument("--user", help="Target SSH user")
    parser.add_argument("--password", help="Target SSH password")
    
    args = parser.parse_args()
    
    board = discover_and_update_board(host=args.host, user=args.user, password=args.password)
    if board:
        print(f"[PASS]  Board detection complete. Target board is: {board}")
    else:
        print("[ERROR] Board detection failed.")
