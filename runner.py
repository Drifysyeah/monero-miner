#!/usr/bin/env python3
import os
import sys
import json
import time
import socket
import platform
import subprocess
import requests
import signal
import threading
import re
if platform.system() != "Windows":
    import pty
# =====================================
# CONFIG
# =====================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # Get script directory
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")  # Absolute path to config.json
HEARTBEAT_INTERVAL = 10

HOME = os.path.expanduser("~")
XMRIG_DIR = os.path.join(HOME, "xmrig-miner")

START_TIME = time.time()
miner_process = None
running = True

# live hashrate + last sent value protected by lock
current_hashrate = 0.0
last_sent_hashrate = None
hash_lock = threading.Lock()

# =====================================
# CONFIG HELPERS
# =====================================

def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

def set_background_state(state: bool):
    cfg = load_config()
    cfg.setdefault("runner", {})
    cfg["runner"]["background_running"] = state
    save_config(cfg)

def background_running():
    cfg = load_config()
    return cfg.get("runner", {}).get("background_running", False)

# =====================================
# SYSTEM INFO
# =====================================

def hostname():
    return socket.gethostname()

def cpu_name():
    cpu = platform.processor() or platform.machine()

    # Android / Termux improvement
    if cpu in ("", "unknown", None):
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "Hardware" in line or "model name" in line:
                        return line.split(":")[1].strip()
        except Exception:
            pass

    return cpu

def uptime_seconds():
    return int(time.time() - START_TIME)

# =====================================
# HASHRATE PARSER
# =====================================

# loose regex: capture first floating number after "speed"
HASHRATE_REGEX = re.compile(r"speed.*?([\d]+\.\d+)")

# =====================================
# XMRIG OUTPUT READER (PTY)
# =====================================

def start_output_reader(master_fd):
    """
    Reads output from the PTY master fd. Updates current_hashrate when a valid
    speed line is parsed.
    """
    def reader():
        global current_hashrate
        with os.fdopen(master_fd, "r", errors="ignore") as pipe:
            while running:
                line = pipe.readline()
                if not line:
                    break

                line = line.rstrip("\n")
                print(line)

                # only attempt parsing lines that include "speed"
                if "speed" not in line:
                    continue

                match = HASHRATE_REGEX.search(line)
                if match:
                    try:
                        new_hr = float(match.group(1))
                    except Exception:
                        continue

                    # update shared value under lock
                    with hash_lock:
                        current_hashrate = new_hr

                    print(f"[HASHRATE] {new_hr:.2f} H/s")

    threading.Thread(target=reader, daemon=True).start()

# =====================================
# MINER CONTROL
# =====================================

def xmrig_binary():
    return os.path.join(
        XMRIG_DIR,
        "xmrig.exe" if platform.system() == "Windows" else "xmrig"
    )

def xmrig_output_reader(pipe):
    """
    Fallback reader used on Windows where we read process.stdout directly.
    """
    global current_hashrate
    while running:
        line = pipe.readline()
        if not line:
            break

        line = line.rstrip("\n")
        print(line)

        if "speed" not in line:
            continue

        match = HASHRATE_REGEX.search(line)
        if match:
            try:
                new_hr = float(match.group(1))
            except Exception:
                continue

            with hash_lock:
                current_hashrate = new_hr

            print(f"[HASHRATE] {new_hr:.2f} H/s")

def start_xmrig():
    """
    Start xmrig attached to a PTY (Linux/macOS) or normal subprocess on Windows.
    """
    global miner_process

    binary = xmrig_binary()

    if not os.path.exists(binary):
        print("[ERROR] XMRig not found:", binary)
        sys.exit(1)
        # ensure executable (Termux fix)
    try:
        os.chmod(binary, 0o755)
    except Exception:
        pass

    print("[INFO] Starting XMRig...")
    print("[DEBUG] binary path:", binary)

    if platform.system() == "Windows":
        miner_process = subprocess.Popen(
            [binary],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        threading.Thread(
            target=xmrig_output_reader,
            args=(miner_process.stdout,),
            daemon=True
        ).start()

    else:
        # real PTY so xmrig flushes as if on terminal
        master_fd, slave_fd = pty.openpty()
        print("[DEBUG] launching:", binary)
        miner_process = subprocess.Popen(
            [binary],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            close_fds=True
        )

        # close slave in parent
        os.close(slave_fd)

        # start reader on master side
        start_output_reader(master_fd)

def stop_miner():
    global miner_process
    if not miner_process:
        return

    print("[INFO] Stopping XMRig...")

    try:
        if platform.system() == "Windows":
            miner_process.terminate()
        else:
            os.killpg(os.getpgid(miner_process.pid), signal.SIGTERM)

        miner_process.wait(timeout=5)
    except Exception:
        pass

    miner_process = None

# =====================================
# SIGNAL HANDLING
# =====================================

def shutdown_handler(sig, frame):
    global running
    if not running:
        return
    print("\n[INFO] Shutdown requested...")
    running = False

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# =====================================
# BACKGROUND MODE
# =====================================

def relaunch_background():
    print("[INFO] Launching in background...")
    if platform.system() == "Windows":
        subprocess.Popen(
            ["cmd", "/c", "start", "/B", sys.executable, __file__, "--daemon"]
        )
    else:
        subprocess.Popen(
            ["setsid", sys.executable, __file__, "--daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    sys.exit(0)

def stop_background_runner():
    cfg = load_config()
    cfg.setdefault("runner", {})
    cfg["runner"]["background_running"] = False
    save_config(cfg)
    print("✅ Background runner stop signal sent.")
    input("Press ENTER...")

# =====================================
# HEARTBEAT (only send when hashrate changes)
# =====================================

# threshold below which we consider values "the same" (H/s)
HASHRATE_TOLERANCE = 0.01

def send_heartbeat_if_changed(cfg):
    """
    Send heartbeat only when the current_hashrate differs meaningfully from
    the last_sent_hashrate. Always sends if last_sent_hashrate is None.
    """
    global last_sent_hashrate

    with hash_lock:
        hr = current_hashrate

    # compare using tolerance
    if last_sent_hashrate is None or abs(hr - last_sent_hashrate) >= HASHRATE_TOLERANCE:
        # Build payload (include CPU & uptime as asked)
        payload = {
            "hostname": hostname(),
            "cpu": cpu_name(),
            "uptime": uptime_seconds(),
            "hashrate": hr
        }
        try:
            resp = requests.post(
                cfg["server_url"].rstrip("/") + "/api/heartbeat",
                json=payload,
                timeout=8
            )
            # optionally check resp.status_code
            if resp.status_code == 200:
                last_sent_hashrate = hr
                print(f"[HB] sent | {hr:.2f} H/s")
            else:
                print(f"[HB] sent (status {resp.status_code}) | {hr:.2f} H/s")
        except Exception:
            print("[HB] failed")
    else:
        # skip sending, show small debug
        print(f"[HB] skipped | same {hr:.2f} H/s")

# =====================================
# MENUS
# =====================================

def runtime_menu():
    print("\nRunner Options")
    print("1) No time limit")
    print("2) Set time limit")
    print("3) Exit")

    choice = input("Choose: ").strip()

    if choice == "3":
        sys.exit(0)

    cfg = load_config()

    if choice == "1":
        cfg["runtime_limit"] = None

    elif choice == "2":
        print("\n1) Minutes\n2) Hours\n3) Days")
        unit = input("Choose: ").strip()
        try:
            amount = int(input("How many?: ").strip())
        except Exception:
            print("Invalid number")
            return runtime_menu()

        mult = {"1":60,"2":3600,"3":86400}.get(unit)
        if not mult:
            return runtime_menu()

        cfg["runtime_limit"] = int(time.time()) + amount * mult

    save_config(cfg)

def background_menu():
    print("\nRun in background?")
    print("1) Yes")
    print("2) No")
    print("3) Exit")

    c = input("Choose: ").strip()

    if c == "3":
        sys.exit(0)

    if c == "1":
        relaunch_background()

def main_menu():
    while True:
        print("\nRunner Menu")
        print("1) Start Runner")

        if background_running():
            print("2) Stop Background Runner")
            print("3) Exit")
        else:
            print("2) Exit")

        choice = input("Choose: ").strip()

        if choice == "1":
            runtime_menu()
            background_menu()
            return

        if background_running() and choice == "2":
            stop_background_runner()
        else:
            sys.exit(0)

# =====================================
# MAIN LOOP
# =====================================

def run_loop():
    global running
    set_background_state(True)
    # Android wakelock (Termux only)
    if os.environ.get("PREFIX", "").startswith("/data/data/com.termux"):
        try:
            subprocess.run(["termux-wake-lock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    start_xmrig()

    while running:
        if miner_process and miner_process.poll() is not None:
            print("[INFO] XMRig exited.")
            break

        cfg = load_config()

        if not cfg.get("runner", {}).get("background_running", True):
            print("[INFO] Background stop requested.")
            break

        if cfg.get("runtime_limit") and time.time() >= cfg["runtime_limit"]:
            print("[INFO] Runtime finished.")
            break

        # send heartbeat only if hashrate changed
        send_heartbeat_if_changed(cfg)

        # interruptible wait
        for _ in range(HEARTBEAT_INTERVAL):
            if not running:
                break
            time.sleep(5)

    stop_miner()
    set_background_state(False)
    # release wakelock if running in Termux
    if os.environ.get("PREFIX", "").startswith("/data/data/com.termux"):
        try:
            subprocess.run(["termux-wake-unlock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        print("[INFO] Runner stopped.")

# =====================================
# ENTRY
# =====================================

if __name__ == "__main__":
    daemon = "--daemon" in sys.argv
    if not daemon:
        main_menu()
    run_loop()