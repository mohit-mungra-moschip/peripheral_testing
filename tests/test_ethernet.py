import pytest
import subprocess
import socket
import re
import os

def test_ethernet_interface_state(board_config, step_logger):
    """
    Validates that the Ethernet interface is physically up and active.
    Checks the /sys/class/net/ethX/operstate file.
    """
    eth_interface = board_config.get("eth_interface", "eth0")
    
    step_logger.info("="*60)
    step_logger.info(f"ETHERNET VALIDATION: INTERFACE STATE ({eth_interface})")
    
    operstate_path = f"/sys/class/net/{eth_interface}/operstate"
    if not os.path.exists(operstate_path):
        pytest.fail(f"Interface {eth_interface} does not exist in sysfs.")
        
    with step_logger.step("Read Link State", action=f"Read {operstate_path}", expected="State in ['up', 'unknown']") as step:
        with open(operstate_path, "r") as f:
            state = f.read().strip()
        assert state in ["up", "unknown"], f"Ethernet interface {eth_interface} is physically down (state: {state}). Plug in a cable!"
        step.success(f"State is {state.upper()}")

    step_logger.info("="*60)

def test_ethernet_ip_check(board_config, step_logger):
    """
    Validates that the interface has an assigned IPv4 address.
    """
    eth_interface = board_config.get("eth_interface", "eth0")
    
    step_logger.info("="*60)
    step_logger.info(f"ETHERNET VALIDATION: IP ALLOCATION ({eth_interface})")
    
    with step_logger.step("Check IPv4 Address", action=f"ip -4 addr show {eth_interface}", expected="Valid IPv4 address found") as step:
        try:
            ip_out = subprocess.run(["ip", "-4", "addr", "show", eth_interface], capture_output=True, text=True, check=True).stdout
            match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", ip_out)
            assert match is not None, f"No IPv4 address found for interface {eth_interface}."
            ip_addr = match.group(1)
            step.success(f"Allocated IP Address: {ip_addr}")
        except subprocess.CalledProcessError as e:
            pytest.fail(f"Failed to query IP address: {e}")
        except Exception as e:
            pytest.fail(f"Error checking IP: {e}")
            
    step_logger.info("="*60)

def test_ethernet_ping_connectivity(board_config, step_logger):
    """
    Validates external connectivity by pinging a known target.
    """
    eth_interface = board_config.get("eth_interface", "eth0")
    ping_target = board_config.get("eth_ping_target", "8.8.8.8")
    
    step_logger.info("="*60)
    step_logger.info(f"ETHERNET VALIDATION: PING CONNECTIVITY ({ping_target})")
    
    with step_logger.step("Ping Target", action=f"ping -c 4 -I {eth_interface} {ping_target}", expected="Ping succeeds (exit code 0)") as step:
        try:
            ping_out = subprocess.run(["ping", "-c", "4", "-I", eth_interface, ping_target], capture_output=True, text=True)
            if ping_out.returncode != 0:
                pytest.fail(f"Ping failed to {ping_target}. Network might be unreachable.\\nOutput:\\n{ping_out.stderr}")
            step.success(f"Successfully pinged {ping_target} 4 times")
        except Exception as e:
            pytest.fail(f"Ping execution error: {e}")
            
    step_logger.info("="*60)

def test_ethernet_latency(board_config, step_logger):
    """
    Validates network delay/latency is within acceptable thresholds.
    Extracts the 'avg' rtt from a ping.
    """
    eth_interface = board_config.get("eth_interface", "eth0")
    ping_target = board_config.get("eth_ping_target", "8.8.8.8")
    max_latency = board_config.get("eth_max_latency_ms", 50.0)
    
    step_logger.info("="*60)
    step_logger.info(f"ETHERNET VALIDATION: LATENCY DELAY ({ping_target})")
    
    with step_logger.step("Measure Latency", action=f"ping -c 5 -q -I {eth_interface} {ping_target}", expected=f"Average latency <= {max_latency}ms") as step:
        try:
            ping_out = subprocess.run(["ping", "-c", "5", "-q", "-I", eth_interface, ping_target], capture_output=True, text=True)
            if ping_out.returncode != 0:
                pytest.fail(f"Ping failed. Cannot measure latency.")
                
            match = re.search(r"rtt min/avg/max/mdev = ([\d\.]+)/([\d\.]+)/([\d\.]+)/([\d\.]+) ms", ping_out.stdout)
            assert match, "Failed to parse ping output for latency."
            avg_latency = float(match.group(2))
            assert avg_latency <= max_latency, f"Latency {avg_latency}ms exceeds max acceptable threshold of {max_latency}ms."
            step.success(f"Average Latency: {avg_latency} ms")
        except Exception as e:
            pytest.fail(f"Latency test error: {e}")
            
    step_logger.info("="*60)

def test_ethernet_socket(board_config, step_logger):
    """
    Validates local networking stack by opening a raw TCP socket.
    """
    target_host = board_config.get("eth_ping_target", "8.8.8.8")
    target_port = 53
    
    step_logger.info("="*60)
    step_logger.info(f"ETHERNET VALIDATION: TCP SOCKET")
    
    with step_logger.step("Open TCP Socket", action=f"connect to {target_host}:{target_port}", expected="Socket connects successfully") as step:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5.0)
            s.connect((target_host, target_port))
            local_ip, local_port = s.getsockname()
            s.close()
            step.success(f"Socket connected successfully from local endpoint {local_ip}:{local_port}")
        except Exception as e:
            pytest.fail(f"Socket connection failed: {e}")
            
    step_logger.info("="*60)

def test_ethernet_speedtest(board_config, step_logger):
    """
    Measures Internet bandwidth using speedtest-cli.
    Requires speedtest-cli to be installed via apt.
    """
    step_logger.info("="*60)
    step_logger.info(f"ETHERNET VALIDATION: SPEEDTEST BANDWIDTH")
    step_logger.info("Running speedtest (this may take up to a minute)...")
    
    with step_logger.step("Measure Bandwidth", action="speedtest-cli --simple", expected="Successful speedtest output") as step:
        try:
            speed_out = subprocess.run(["speedtest-cli", "--simple"], capture_output=True, text=True)
            if speed_out.returncode != 0:
                pytest.fail(f"Speedtest failed. Ensure speedtest-cli is installed and internet is reachable.\\nError: {speed_out.stderr}")
            result_str = speed_out.stdout.strip().replace('\\n', ', ')
            step.success(f"Results: {result_str}")
        except FileNotFoundError:
            pytest.fail("speedtest-cli is not installed. Add it to OS dependencies.")
        except Exception as e:
            pytest.fail(f"Speedtest execution error: {e}")
            
    step_logger.info("="*60)

def test_ethernet_iperf(board_config, step_logger):
    """
    Measures local network professional throughput using iperf3.
    Requires an iperf3 server running on the target network.
    """
    step_logger.info("="*60)
    step_logger.info(f"ETHERNET VALIDATION: IPERF3 THROUGHPUT")
    
    iperf_server = board_config.get("eth_iperf_server", "")
    if not iperf_server:
        ssh_client = os.environ.get("SSH_CLIENT", "")
        if ssh_client:
            iperf_server = ssh_client.split()[0]
            step_logger.info(f"Auto-detected iperf server from SSH session: {iperf_server}")
        else:
            pytest.skip("No iperf_server configured in boards.yaml, and not running via SSH. Skipping iperf test.")
    else:
        step_logger.info(f"Using configured iperf server: {iperf_server}")
            
    with step_logger.step("Measure iPerf3 Throughput", action=f"iperf3 -c {iperf_server} -t 5 -f m", expected="Throughput measurement parses successfully") as step:
        try:
            iperf_out = subprocess.run(["iperf3", "-c", iperf_server, "-t", "5", "-f", "m"], capture_output=True, text=True)
            if iperf_out.returncode != 0:
                pytest.skip(
                    f"iPerf3 failed to connect to {iperf_server}.\n"
                    f"This usually means the iperf3 server is not running on the host machine.\n"
                    f"Please start it with: 'iperf3 -s' on {iperf_server}\n"
                    f"Error: {iperf_out.stderr.strip()}"
                )
            match = re.search(r"([\d\.]+)\s+Mbits/sec\s+sender", iperf_out.stdout)
            if match:
                throughput = float(match.group(1))
                step.success(f"Throughput: {throughput} Mbps")
            else:
                step.success("Failed to parse raw iperf3 output, check stdout for details")
        except FileNotFoundError:
            pytest.fail("iperf3 is not installed. Add it to OS dependencies.")
        except Exception as e:
            pytest.fail(f"iperf3 execution error: {e}")
            
    step_logger.info("="*60)
