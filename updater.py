import os
import sys
import shutil
import zipfile
import requests
import subprocess
import tempfile

REPO_ZIP = "https://github.com/Drifysyeah/monero-miner/archive/refs/heads/main.zip"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = tempfile.mkdtemp()

# Files and folders to never overwrite
IGNORE = {
    "config.json",
    "xmrig-miner",
    "readme.md",
    "readme.txt",
    "readme",
    ".gitignore",
    ".git",
    "license",
    "license.md",
    "license.txt",
}


def download_repo():
    print("[UPDATER] Downloading latest version...")
    zip_path = os.path.join(TEMP_DIR, "repo.zip")

    r = requests.get(REPO_ZIP, timeout=30)
    with open(zip_path, "wb") as f:
        f.write(r.content)

    return zip_path


def extract_repo(zip_path):
    print("[UPDATER] Extracting...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(TEMP_DIR)

    for name in os.listdir(TEMP_DIR):
        if name.endswith("-main"):
            return os.path.join(TEMP_DIR, name)

    raise Exception("Repo folder not found")


def replace_files(repo_path):
    print("[UPDATER] Replacing files...")

    for fname in os.listdir(repo_path):
        if fname.lower() in IGNORE:
            print(f"  skipped: {fname}")
            continue

        src = os.path.join(repo_path, fname)
        dst = os.path.join(SCRIPT_DIR, fname)

        if os.path.isfile(src):
            shutil.copy2(src, dst)
            print(f"  updated: {fname}")
        elif os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            print(f"  updated dir: {fname}")


def restart_runner():
    print("[UPDATER] Starting runner...")
    subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "runner.py")],
        close_fds=True
    )
    sys.exit(0)


def main():
    zip_path = download_repo()
    repo_path = extract_repo(zip_path)
    replace_files(repo_path)
    restart_runner()


if __name__ == "__main__":
    main()