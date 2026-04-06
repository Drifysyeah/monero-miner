import os
import platform
import shutil
import socket
import zipfile
import tarfile
import requests
import json
import subprocess

# =====================================
# PATHS
# =====================================

HOME = os.path.expanduser("~")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # Get script directory
INSTALL_DIR = os.path.join(HOME, "xmrig-miner")
PROJECT_CONFIG = os.path.join(SCRIPT_DIR, "config.json")  # Use absolute path to config.json

# =====================================
# UI
# =====================================

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def separator():
    print("\n" + "=" * 60 + "\n")


def pause():
    input("Press ENTER to continue...")


# =====================================
# CONFIG LOADING
# =====================================

def load_project_config():
    if not os.path.exists(PROJECT_CONFIG):
        raise Exception("config.json not found")

    with open(PROJECT_CONFIG, "r") as f:
        return json.load(f)


def get_wallet():
    cfg = load_project_config()

    wallet = cfg.get("wallet")
    if not wallet:
        raise Exception("Wallet missing in config.json")

    return wallet


# =====================================
# SYSTEM
# =====================================

def get_hostname():
    return socket.gethostname()


def xmrig_binary():
    if platform.system() == "Windows":
        return os.path.join(INSTALL_DIR, "xmrig.exe")
    return os.path.join(INSTALL_DIR, "xmrig")


# =====================================
# VERSION CHECK
# =====================================

def get_installed_version():
    binary = xmrig_binary()

    if not os.path.exists(binary):
        return None

    try:
        out = subprocess.check_output([binary, "--version"], text=True)
        return out.split("\n")[0]
    except:
        return "unknown"


# =====================================
# GITHUB RELEASE
# =====================================

def get_latest_release():
    api = "https://api.github.com/repos/xmrig/xmrig/releases/latest"
    r = requests.get(api, timeout=20)

    if r.status_code != 200:
        raise Exception("GitHub API error")

    return r.json()


def get_download_asset(release):
    system = platform.system().lower()

    for asset in release["assets"]:
        name = asset["name"].lower()

        # -------------------------
        # WINDOWS (x64)
        # -------------------------
        if system == "windows":
            # accept BOTH official windows builds
            if "windows" in name and "x64" in name and name.endswith(".zip"):
                return asset["browser_download_url"], ".zip"

        # -------------------------
        # LINUX (x64 static preferred)
        # -------------------------
        else:
            if "linux-static" in name and "x64" in name and name.endswith(".tar.gz"):
                return asset["browser_download_url"], ".tar.gz"

    # Debug output if nothing matched
    print("\nAvailable assets:")
    for asset in release["assets"]:
        print(" -", asset["name"])

    raise Exception("No compatible xmrig build found")


# =====================================
# DOWNLOAD
# =====================================

def download_file(url, dest):
    print("[INFO] Downloading XMRig...")

    r = requests.get(url, stream=True, timeout=60)

    if r.status_code != 200:
        raise Exception(f"Download failed ({r.status_code})")

    with open(dest, "wb") as f:
        for chunk in r.iter_content(1024 * 1024):
            if chunk:
                f.write(chunk)

    print("[OK] Download complete.")


# =====================================
# EXTRACTION
# =====================================

def extract_archive(path):
    print("[INFO] Extracting...")

    if path.endswith(".zip"):
        with zipfile.ZipFile(path, "r") as z:
            z.extractall(INSTALL_DIR)

    elif path.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as t:
            t.extractall(INSTALL_DIR)


def flatten_directory():
    for name in os.listdir(INSTALL_DIR):
        full = os.path.join(INSTALL_DIR, name)

        if os.path.isdir(full) and name.startswith("xmrig"):
            for item in os.listdir(full):
                shutil.move(
                    os.path.join(full, item),
                    os.path.join(INSTALL_DIR, item)
                )
            shutil.rmtree(full)
            break


# =====================================
# SAFE CONFIG PATCH (CORRECTED)
# =====================================

def patch_config():
    config_path = os.path.join(INSTALL_DIR, "config.json")

    if not os.path.exists(config_path):
        print("[WARN] xmrig config.json missing.")
        return

    wallet = get_wallet()
    hostname = get_hostname()

    print("[INFO] Updating pool settings...")

    with open(config_path, "r") as f:
        config = json.load(f)

    # modify ONLY pools
    config["pools"] = [{
        "algo": "rx/0",
        "coin": "monero",
        "url": "gulf.moneroocean.stream:10128",
        "user": wallet,
        "pass": hostname,
        "keepalive": True,
        "tls": False
    }]

    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

    print("[OK] Pool configured safely.")


# =====================================
# INSTALL / UPDATE CORE
# =====================================

def install_or_update():
    release = get_latest_release()
    url, ext = get_download_asset(release)

    os.makedirs(INSTALL_DIR, exist_ok=True)

    archive = os.path.join(INSTALL_DIR, "xmrig" + ext)

    download_file(url, archive)
    extract_archive(archive)
    flatten_directory()

    os.remove(archive)

    patch_config()

    print("✅ XMRig ready.")


# =====================================
# INSTALL
# =====================================

def install_xmrig():
    separator()

    if os.path.exists(INSTALL_DIR):
        print("[INFO] Existing install detected — updating.\n")

    install_or_update()
    pause()


# =====================================
# UPDATE
# =====================================

def check_update():
    separator()

    print("[INFO] Checking for updates...")

    installed = get_installed_version()
    latest = get_latest_release()["tag_name"]

    print(f"Installed: {installed}")
    print(f"Latest:    {latest}\n")

    install_or_update()
    pause()


# =====================================
# UNINSTALL
# =====================================

def uninstall_xmrig():
    separator()

    if not os.path.exists(INSTALL_DIR):
        print("Not installed.")
        pause()
        return

    confirm = input("Uninstall XMRig? (y/n): ").lower()

    if confirm != "y":
        print("Cancelled.")
        pause()
        return

    shutil.rmtree(INSTALL_DIR)
    print("✅ Removed.")
    pause()


# =====================================
# MENU
# =====================================

def menu():
    while True:
        clear()

        separator()
        print("XMRig Installer")
        separator()

        print("1) Install XMRig")
        print("2) Uninstall XMRig")
        print("3) Check for Updates")
        print("4) Exit\n")

        choice = input("Choose: ").strip()

        if choice == "1":
            install_xmrig()
        elif choice == "2":
            uninstall_xmrig()
        elif choice == "3":
            check_update()
        elif choice == "4":
            break


# =====================================
# MAIN
# =====================================

if __name__ == "__main__":
    menu()