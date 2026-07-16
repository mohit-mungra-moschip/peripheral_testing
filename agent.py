import os
import sys
import json
import yaml
from typing import TypedDict, Annotated, List, Dict, Any
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from fabric import Connection
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Graph State Definition ---
class AgentState(TypedDict):
    """The State of the BSP Validation Agent."""
    pytest_report: Dict[str, Any]
    failed_tests: List[Dict[str, Any]]
    dmesg_logs: str
    irq_logs: str
    diagnosis: str
    suggested_action: str
    board_config: Dict[str, Any]
    report_file: str
    board_name: str

# --- Nodes ---

def load_pytest_report(state: AgentState) -> AgentState:
    """Reads the JSON output from the latest Pytest run."""
    print("[INFO]  [Agent] Analyzing Pytest Report...")
    try:
        report_file = state.get("report_file", ".report.json")
        with open(report_file, "r") as f:
            report = json.load(f)
            
        failed_tests = [
            test for test in report.get("tests", []) 
            if test.get("outcome") == "failed"
        ]
        return {"pytest_report": report, "failed_tests": failed_tests}
    except FileNotFoundError:
        print(f"[WARN]  [Agent] No {state.get('report_file')} found! Ensure tests run with --json-report")
        return {"pytest_report": {}, "failed_tests": []}

def gather_diagnostics(state: AgentState) -> AgentState:
    """Uses Fabric to SSH into the Pi and pull live diagnostic data (dmesg, IRQs)."""
    if not state.get("failed_tests"):
        print("[PASS]  [Agent] No failed tests detected. Skipping diagnostics.")
        return {"dmesg_logs": "", "irq_logs": ""}

    print("[INFO]  [Agent] Gathering Live Diagnostics from Hardware...")
    
    # Load board config to get SSH credentials
    board_name = state.get("board_name", "raspberry_pi_5")
    with open("boards.yaml", "r") as f:
        configs = yaml.safe_load(f)
        config = configs.get(board_name, {})
        
    remote = config.get("remote", {})
    host = remote.get("host", "127.0.0.1")
    user = remote.get("user", "root")
    password = remote.get("password", "")
    
    connect_kwargs = {"password": password, "timeout": 5} if password else {"timeout": 5}
    
    try:
        with Connection(host=host, user=user, connect_kwargs=connect_kwargs) as c:
            dmesg = c.run("dmesg | tail -n 100", hide=True, in_stream=False).stdout
            irqs = c.run("cat /proc/interrupts", hide=True, in_stream=False).stdout
            return {"dmesg_logs": dmesg, "irq_logs": irqs}
    except Exception as e:
        print(f"[ERROR] [Agent] SSH Diagnostic Failed: {e}")
        return {"dmesg_logs": f"Error gathering dmesg: {e}", "irq_logs": ""}

def analyze_failure(state: AgentState) -> AgentState:
    """Uses LLM to analyze the test failures alongside hardware logs."""
    if not state.get("failed_tests"):
        return {"diagnosis": "All tests passed. System is healthy."}

    print("[INFO]  [Agent] Reasoning over Hardware Failures...")
    
    model_name = os.environ.get("MODEL_NAME", "openai/gpt-oss-120b:free")
    
    llm = ChatOpenAI(
        model=model_name,
        temperature=0,
        openai_api_key=os.environ.get("OPENROUTER_API_KEY", "missing_key"),
        openai_api_base="https://openrouter.ai/api/v1",
        timeout=15.0,
        max_retries=0
    )
    
    prompt = f"""
    You are an expert Linux Kernel and BSP Validation Engineer.
    The automated hardware test suite failed on a Raspberry Pi 5.
    
    Failed Tests:
    {json.dumps(state['failed_tests'], indent=2)}
    
    Recent dmesg logs:
    {state['dmesg_logs']}
    
    Analyze the logs and the failed tests. Provide a concise root cause analysis (RCA).
    """
    
    print("[INFO]  [Agent] Enforcing 3-second cooldown to respect OpenRouter's 30 RPM limit...")
    import time
    time.sleep(3)
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = llm.invoke([SystemMessage(content=prompt)])
            break
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[WARN]  [Agent] API Error encountered ({e}). Sleeping for 60 seconds before retrying...")
                time.sleep(60)
            else:
                return {"diagnosis": f"Analysis aborted due to repeated API errors: {e}"}
                
    print(f"\n[DIAGNOSIS]\n{response.content}\n")
    return {"diagnosis": response.content}


def propose_remediation(state: AgentState) -> AgentState:
    """Generates a standalone Markdown RCA report based on the diagnosis."""
    if not state.get("failed_tests"):
        return {"suggested_action": "None"}
        
    print("[INFO]  [Agent] Generating Standalone RCA Report...")
    
    report_content = f"# BSP Validation Agent RCA Report\n\n"
    report_content += f"## Failed Tests\n"
    for test in state.get("failed_tests", []):
        report_content += f"- `{test.get('nodeid')}`\n"
        
    report_content += f"\n## Agent Diagnosis\n"
    report_content += f"{state.get('diagnosis', 'No diagnosis available.')}\n"
    
    report_content += f"\n## Recommended Remediation\n"
    report_content += f"Review the diagnosis above. If this is a software regression, apply the necessary patches. If it is a physical layer issue, check connections and reboot the hardware.\n"
    
    board_name = state.get("board_name", "unknown")
    report_file = f"bsp_rca_report_{board_name}.md"
    with open(report_file, "w") as f:
        f.write(report_content)
        
    print(f"[PASS]  [Agent] Saved Root Cause Analysis to '{report_file}'")
    return {"suggested_action": "Report Generated"}

# --- Graph Definition ---

workflow = StateGraph(AgentState)

workflow.add_node("load_pytest", load_pytest_report)
workflow.add_node("gather_diagnostics", gather_diagnostics)
workflow.add_node("analyze_failure", analyze_failure)
workflow.add_node("propose_remediation", propose_remediation)

workflow.set_entry_point("load_pytest")

# If there are no failed tests, we skip diagnostics and analysis
def should_diagnose(state: AgentState):
    if state.get("failed_tests"):
        return "gather_diagnostics"
    return END

workflow.add_conditional_edges(
    "load_pytest",
    should_diagnose,
    {
        "gather_diagnostics": "gather_diagnostics",
        END: END
    }
)

workflow.add_edge("gather_diagnostics", "analyze_failure")
workflow.add_edge("analyze_failure", "propose_remediation")
workflow.add_edge("propose_remediation", END)

# Compile the graph
app = workflow.compile()

if __name__ == "__main__":
    import glob
    print("\n[INFO]  Starting Autonomous BSP Validation Agent...")
    
    reports = glob.glob(".report_*.json")
    if not reports:
        # Fallback if just .report.json exists
        if os.path.exists(".report.json"):
            reports = [".report.json"]
            
    if not reports:
        print("[INFO]  No reports found to analyze.")
        sys.exit(0)
        
    for report in reports:
        board_name = report.replace(".report_", "").replace(".json", "")
        if board_name == ".report": 
            board_name = "raspberry_pi_5" # Default if legacy name
            
        print(f"\n[INFO]  --- Analyzing Board: {board_name} ({report}) ---")
        final_state = app.invoke({
            "board_config": {},
            "report_file": report,
            "board_name": board_name
        })
        
    print("\n[INFO]  Agent Execution Complete.")
