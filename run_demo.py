import subprocess
import json
import sys

for s in (sys.stdout, sys.stderr):
    if hasattr(s, "reconfigure"):
        s.reconfigure(encoding="utf-8", errors="backslashreplace")

def send_msg(proc, msg):
    line = json.dumps(msg) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()

def read_msg(proc):
    line = proc.stdout.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8"))

def wait_for_response(proc, req_id):
    while True:
        msg = read_msg(proc)
        if msg is None:
            return None
        if msg.get("id") == req_id:
            return msg

print("=== STARTING MCP SERVER (server.py) ===")
proc = subprocess.Popen(
    [sys.executable, "D:/antigravity-mcp/server.py"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    bufsize=0
)

try:
    # 1. Initialize
    print("\n1. Gui yeu cau Khoi tao (MCP Initialize)...")
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"}
        }
    }
    send_msg(proc, init_req)
    res = read_msg(proc)
    print("-> Server phan hoi initialize:")
    print(json.dumps(res, indent=2))

    # Send initialized notification
    send_msg(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 2. List Tools
    print("\n2. Kiem tra danh sach Tools (tools/list)...")
    send_msg(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools_res = read_msg(proc)
    tools = [t["name"] for t in tools_res.get("result", {}).get("tools", [])]
    print("-> Danh sach tools kha dung:", tools)

    # 3. List Resources
    print("\n3. Kiem tra danh sach Resources (resources/list)...")
    send_msg(proc, {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
    res_list = read_msg(proc)
    resources = [r["uri"] for r in res_list.get("result", {}).get("resources", [])]
    print("-> Danh sach resources kha dung:", resources)

    # 4. List Prompts
    print("\n4. Kiem tra danh sach Prompts (prompts/list)...")
    send_msg(proc, {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}})
    prompt_list = read_msg(proc)
    prompts = [p["name"] for p in prompt_list.get("result", {}).get("prompts", [])]
    print("-> Danh sach prompts kha dung:", prompts)

    # 5. Call tool: ping
    print("\n5. Goi tool 'ping'...")
    send_msg(proc, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "ping", "arguments": {}}})
    ping_res = read_msg(proc)
    print("-> Ket qua ping:", ping_res.get("result", {}).get("content", [{}])[0].get("text"))

    # 6. Call tool: ask-antigravity with a coding question
    print("\n6. Goi tool 'ask-antigravity' (Gemini 3.8 Flash Coder)...")
    prompt_text = "Viet mot ham Python tinh giai thua (factorial) va giai thich ngan gon trong 1 cau."
    send_msg(proc, {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "ask-antigravity",
            "arguments": {
                "prompt": prompt_text,
                "model": "gemini-3.8-flash-high"
            }
        }
    })
    
    # Read response
    call_res = wait_for_response(proc, 6)
    ans = call_res.get("result", {}).get("content", [{}])[0].get("text") if call_res else "No response"
    print("\n-> PHAN HOI TU ANTIGRAVITY AGENT:\n")
    print(ans)

finally:
    proc.terminate()
    print("\n=== DA HOAN THANH CHAY THU ===")