import os
import time
import socket
import json
import requests

# =====================================
# CONFIG
# =====================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # Get script directory
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")  # Absolute path to config.json
POLL_INTERVAL = 3


def load_config():
    if not os.path.exists(CONFIG_FILE):
        print("[ERROR] config.json missing.")
        input("Press ENTER...")
        exit(1)

    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


CONFIG = load_config()
SERVER_URL = CONFIG["server_url"].rstrip("/")


# =====================================
# UTILS
# =====================================

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def separator():
    print("\n" + "=" * 60 + "\n")


def get_hostname():
    return socket.gethostname()


# =====================================
# TIME SELECTION (TEMP PAIR)
# =====================================

def choose_temp_duration():
    separator()
    print("Temporary Pair Duration")
    separator()

    print("Choose unit:")
    print("1) Minutes")
    print("2) Hours")
    print("3) Days\n")

    unit = input("Select (1-3): ").strip()

    if unit not in ["1", "2", "3"]:
        print("Invalid choice.")
        return None

    amount = input("Enter amount: ").strip()

    if not amount.isdigit():
        print("Invalid number.")
        return None

    amount = int(amount)

    seconds = amount
    if unit == "1":
        seconds *= 60
    elif unit == "2":
        seconds *= 3600
    elif unit == "3":
        seconds *= 86400

    expires_at = int(time.time()) + seconds

    return expires_at


# =====================================
# PAIR REQUEST
# =====================================

def start_pairing(temp=False, expires_at=None):

    clear()
    separator()
    print("Link this computer to an account")
    separator()

    hostname = get_hostname()

    payload = {
        "hostname": hostname,
        "temporary": temp
    }

    if temp:
        payload["expires_at"] = expires_at

    print("[INFO] Requesting pairing token...")

    try:
        r = requests.post(
            f"{SERVER_URL}/api/pair/create",
            json=payload,
            timeout=10
        )
    except Exception as e:
        print("[ERROR] Server unreachable:", e)
        input("Press ENTER...")
        return

    if r.status_code != 200:
        print("[ERROR] Server error:", r.text)
        input("Press ENTER...")
        return

    data = r.json()

    token = data["token"]
    url = data["url"]

    separator()
    print("✅ OPEN THIS URL:\n")
    print(url)
    separator()

    print("[INFO] Waiting for confirmation...\n")

    while True:
        try:
            time.sleep(POLL_INTERVAL)

            r = requests.post(
                f"{SERVER_URL}/api/pair/status",
                json={"token": token},
                timeout=10
            )

            if r.status_code != 200:
                continue

            status = r.json()

            if status.get("paired"):
                finalize_pair(status, temp, expires_at)
                separator()
                print("✅ Pairing complete!")
                separator()
                input("Press ENTER...")
                return

            print("[INFO] Waiting...")

        except KeyboardInterrupt:
            print("\nCancelled.")
            return
        except Exception:
            print("[WARN] Retry connection...")


# =====================================
# SAVE PAIR RESULT
# =====================================

def finalize_pair(status, temp, expires_at):

    cfg = load_config()

    cfg["machine_id"] = status.get("machine_id")

    if temp:
        cfg["temp_pair"] = {
            "enabled": True,
            "expires_at": expires_at
        }
    else:
        cfg["temp_pair"] = {
            "enabled": False
        }

    save_config(cfg)

    print(f"[INFO] Machine ID saved: {cfg['machine_id']}")

    if temp:
        print("[INFO] Temporary pairing enabled.")
        print(f"[INFO] Expires at UNIX time: {expires_at}")


# =====================================
# MENU
# =====================================

def menu():

    while True:
        clear()
        separator()
        print("Pairing Menu")
        separator()

        print("1) Permanent Pair")
        print("2) Temporary Pair")
        print("3) Exit\n")

        choice = input("Choose: ").strip()

        if choice == "1":
            start_pairing(temp=False)

        elif choice == "2":
            expires_at = choose_temp_duration()
            if expires_at:
                start_pairing(temp=True, expires_at=expires_at)
            input("\nPress ENTER...")

        elif choice == "3":
            break


# =====================================
# MAIN
# =====================================

if __name__ == "__main__":
    menu()