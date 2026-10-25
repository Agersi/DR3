"""SSH helper for the byagersi.com VPS.

Reads credentials from .env at the project root. Never prints the password.

Usage:  python tools/vps.py "command" ["cmd2" ...]   (each runs, exit codes shown)
        python tools/vps.py --sudo "command"
"""
import os
import sys

import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env():
    env = {}
    with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Vps:
    def __init__(self):
        e = load_env()
        self.user, self.host = e["SSH_IP"].split("@")
        self.password = e["SSH_PW"]
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(self.host, username=self.user, password=self.password, timeout=30)

    def run(self, cmd, timeout=120):
        stdin, stdout, stderr = self.client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out, err

    def sudo(self, cmd, timeout=120):
        rc, out, err = self.run("sudo -n true", 30)
        if rc == 0:
            return self.run("sudo " + cmd, timeout)
        chan = self.client.get_transport().open_session()
        chan.exec_command("sudo -S " + cmd)
        chan.sendall((self.password + "\n").encode())
        chan.shutdown_write()
        out_b, err_b = b"", b""
        while not chan.exit_status_ready():
            if chan.recv_ready():
                out_b += chan.recv(65536)
            if chan.recv_stderr_ready():
                err_b += chan.recv_stderr(65536)
        rc = chan.recv_exit_status()
        return rc, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")

    def run_script(self, local_path, timeout=300, use_sudo=False):
        """Upload a bash script to /tmp and execute it remotely."""
        with open(local_path, "rb") as f:
            data = f.read().replace(b"\r\n", b"\n")
        sftp = self.client.open_sftp()
        with sftp.open("/tmp/dr3_remote.sh", "wb") as rf:
            rf.write(data)
        if not use_sudo:
            return self.run("bash /tmp/dr3_remote.sh", timeout)
        rc, out, err = self.run("sudo -n true", 30)
        if rc == 0:
            return self.run("sudo bash /tmp/dr3_remote.sh", timeout)
        chan = self.client.get_transport().open_session()
        chan.exec_command("sudo -S bash /tmp/dr3_remote.sh")
        chan.sendall((self.password + "\n").encode())
        chan.shutdown_write()
        out_b, err_b = b"", b""
        while not chan.exit_status_ready():
            if chan.recv_ready():
                out_b += chan.recv(65536)
            if chan.recv_stderr_ready():
                err_b += chan.recv_stderr(65536)
        rc = chan.recv_exit_status()
        return rc, out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")

    def close(self):
        self.client.close()


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(2)
    use_sudo = False
    if args[0] == "--sudo":
        use_sudo = True
        args = args[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    vps = Vps()
    try:
        if args[0] == "--run-script" and len(args) >= 2:
            rc, out, err = vps.run_script(args[1], use_sudo=use_sudo)
            print(out, end=(" " if out and not out.endswith("\n") else ""))
            if err.strip():
                print("[stderr]\n" + err, end=(" " if not err.endswith("\n") else ""))
            print(f"[exit {rc}]" if rc else "")
            return
        for cmd in args:
            fn = vps.sudo if use_sudo else vps.run
            rc, out, err = fn(cmd)
            print(f"$ {cmd}\n{out}", end=(" " if out and not out.endswith("\n") else ""))
            if err.strip():
                print("[stderr]\n" + err, end=(" " if not err.endswith("\n") else ""))
            print(f"[exit {rc}]" if rc else "")
    finally:
        vps.close()


if __name__ == "__main__":
    main()
