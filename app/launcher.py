"""Start and stop Vaga.

    python launcher.py           start it (or just open the tab if it's up)
    python launcher.py stop
    python launcher.py restart
    python launcher.py status

Double-clicking `Vaga.cmd` runs the first of these. The server itself is plain
`serve.py`, so nothing here is required — this only saves remembering to start
Ollama, waiting for the port, and finding the tab again.
"""

import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx

APP = Path(__file__).parent
URL = "http://localhost:8000"
OLLAMA_URL = "http://localhost:11434"
PIDFILE = APP.parent / "data" / ".vaga.pid"

# Windows: keep the server out of a console window so closing a terminal, or
# double-clicking the shortcut, doesn't take the app down with it.
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def pid_on_port(port):
    """Whatever is listening on this port, however it was started."""
    if sys.platform != "win32":
        out = subprocess.run(["lsof", "-ti", ":%d" % port],
                             capture_output=True, text=True, check=False).stdout
        return out.splitlines()[0].strip() if out.strip() else None
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                         capture_output=True, text=True, check=False).stdout
    for line in out.splitlines():
        bits = line.split()
        if len(bits) >= 5 and bits[3] == "LISTENING" and bits[1].endswith(":%d" % port):
            return bits[4]
    return None


def kill(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, check=False)
    else:
        import os
        import signal
        os.kill(int(pid), signal.SIGTERM)


def responding(url, path="/", timeout=1.5):
    try:
        httpx.get(url + path, timeout=timeout)
        return True
    except httpx.HTTPError:
        return False


def windowless_python():
    """pythonw.exe where it exists, so no console lingers behind the app."""
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    return str(quiet if quiet.exists() else exe)


def find_ollama():
    for candidate in [
        Path.home() / "AppData/Local/Programs/Ollama/ollama.exe",
        Path("C:/Program Files/Ollama/ollama.exe"),
    ]:
        if candidate.exists():
            return str(candidate)
    from shutil import which
    return which("ollama")


def ensure_ollama():
    """Start Ollama if it isn't already listening. Not fatal if it fails."""
    if responding(OLLAMA_URL, "/api/tags"):
        return True
    exe = find_ollama()
    if not exe:
        print("  Ollama not found — descriptions won't be read, everything else works")
        return False
    print("  starting Ollama…")
    subprocess.Popen([exe, "serve"], creationflags=NO_WINDOW,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        if responding(OLLAMA_URL, "/api/tags"):
            return True
        time.sleep(0.5)
    print("  Ollama didn't come up; the assistant will be unavailable")
    return False


def start(open_browser=True):
    if responding(URL, "/status"):
        print("Vaga is already running at " + URL)
        if open_browser:
            webbrowser.open(URL)
        return 0

    ensure_ollama()
    print("  starting Vaga…")
    proc = subprocess.Popen([windowless_python(), str(APP / "serve.py")],
                            cwd=str(APP), creationflags=NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    PIDFILE.write_text(str(proc.pid), encoding="utf-8")

    for _ in range(40):                       # uvicorn needs a moment
        if responding(URL, "/status"):
            print("Vaga is running at " + URL)
            if open_browser:
                webbrowser.open(URL)
            return 0
        if proc.poll() is not None:
            print("serve.py exited immediately — run `python serve.py` to see why")
            return 1
        time.sleep(0.5)
    print("Vaga didn't answer in 20s. Run `python serve.py` directly to see the error.")
    return 1


def stop():
    if not responding(URL, "/status"):
        PIDFILE.unlink(missing_ok=True)
        print("Vaga isn't running.")
        return 0

    # Ask the app to close itself: it refuses while a search is mid-run, which
    # is the one moment killing it could leave jobs.json half-written.
    try:
        r = httpx.post(URL + "/shutdown", timeout=5)
        if r.status_code == 409:
            print("A search is running — let it finish, or stop it from the page.")
            return 1
    except httpx.HTTPError:
        pass

    for _ in range(20):
        if not responding(URL, "/status"):
            PIDFILE.unlink(missing_ok=True)
            print("Vaga stopped.")
            return 0
        time.sleep(0.5)

    # It ignored us — an older build with no /shutdown route, or a wedged
    # worker. Fall back to the recorded pid, then to whatever holds the port:
    # a stop command has to work regardless of how the server was started.
    pid = None
    if PIDFILE.exists():
        pid = PIDFILE.read_text(encoding="utf-8").strip() or None
    pid = pid or pid_on_port(8000)
    if pid:
        kill(pid)
        PIDFILE.unlink(missing_ok=True)
        for _ in range(10):
            if not responding(URL, "/status"):
                print("Vaga stopped.")
                return 0
            time.sleep(0.5)
    print("Couldn't stop it. Close the python process holding port 8000 by hand.")
    return 1


def status():
    up = responding(URL, "/status")
    print("Vaga   : %s" % ("running at " + URL if up else "stopped"))
    print("Ollama : %s" % ("running" if responding(OLLAMA_URL, "/api/tags") else "stopped"))
    return 0 if up else 1


if __name__ == "__main__":
    action = (sys.argv[1] if len(sys.argv) > 1 else "start").lower()
    if action == "stop":
        sys.exit(stop())
    if action == "status":
        sys.exit(status())
    if action == "restart":
        stop()
        time.sleep(1)
        sys.exit(start())
    sys.exit(start())
