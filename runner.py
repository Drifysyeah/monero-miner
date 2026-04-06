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

def check_self_update():
    try:
        r = requests.get("https://raw.githubusercontent.com/Drifysyeah/monero-miner/main/version.txt", timeout=5)
        latest = r.text.strip()

        local = "1.0.0"  # change when you release

        if latest != local:
            print("[INFO] New version detected. Updating...")
            subprocess.Popen([sys.executable, os.path.join(SCRIPT_DIR, "updater.py")])
            sys.exit(0)
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
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

# =====================================
# SYSTEM INFO
# =====================================

def hostname():
    return socket.gethostname()

def cpu_name():
    cpu = platform.processor() or platform.machine()

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

HASHRATE_REGEX = re.compile(r"speed.*?([\d]+\.\d+)")

# =====================================
# XMRIG OUTPUT READER (PTY)
# =====================================

def start_output_reader(master_fd):
    def reader():
        global current_hashrate

        try:
            with os.fdopen(master_fd, "r", errors="ignore") as pipe:
                while running:
                    try:
                        line = pipe.readline()
                        if not line:
                            break
                    except OSError:
                        break

                    line = line.rstrip("\n")
                    print(line)

                    if "speed" not in line:
                        continue

                    match = HASHRATE_REGEX.search(line)
                    if match:
                        try:
                            new_hr = float(match.group(1))
                            with hash_lock:
                                current_hashrate = new_hr
                        except Exception:
                            pass
        except Exception:
            pass

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
    global miner_process

    binary = xmrig_binary()

    if not os.path.exists(binary):
        print("[ERROR] XMRig not found:", binary)
        sys.exit(1)

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

        os.close(slave_fd)
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
    global running, miner_process

    if not running:
        return

    print("\n[INFO] Shutdown requested...")
    running = False

    if miner_process and miner_process.poll() is None:
        try:
            os.killpg(os.getpgid(miner_process.pid), signal.SIGTERM)
        except Exception:
            pass

# =====================================
# HEARTBEAT
# =====================================

HASHRATE_TOLERANCE = 0.01

def send_heartbeat_if_changed(cfg):
    global last_sent_hashrate

    with hash_lock:
        hr = current_hashrate

    if last_sent_hashrate is None or abs(hr - last_sent_hashrate) >= HASHRATE_TOLERANCE:
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
            if resp.status_code == 200:
                last_sent_hashrate = hr
                print(f"[HB] sent | {hr:.2f} H/s")
            else:
                print(f"[HB] sent (status {resp.status_code}) | {hr:.2f} H/s")
        except Exception:
            print("[HB] failed")
    else:
        print(f"[HB] skipped | same {hr:.2f} H/s")

# =====================================
# MAIN LOOP
# =====================================

def run_loop():
    check_self_update()
    global running
    set_background_state(True)

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
            print("[INFO] Stop requested.")
            break

        if cfg.get("runtime_limit") and time.time() >= cfg["runtime_limit"]:
            print("[INFO] Runtime finished.")
            break

        send_heartbeat_if_changed(cfg)

        for _ in range(HEARTBEAT_INTERVAL):
            if not running:
                break
            time.sleep(5)

    stop_miner()
    set_background_state(False)

    if os.environ.get("PREFIX", "").startswith("/data/data/com.termux"):
        try:
            subprocess.run(["termux-wake-unlock"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    print("[INFO] Runner stopped.")
    input("Press ENTER to close...")

# =====================================
# ENTRY
# =====================================

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    run_loop()