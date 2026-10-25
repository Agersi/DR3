"""Upload a local file to the VPS via SFTP. Reuses tools/vps.py credentials."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vps import Vps


def main():
    if not (len(sys.argv) == 3 or (len(sys.argv) == 4 and sys.argv[1] == "--get")):
        print(__doc__ + "\n--get <remote> <local>")
        sys.exit(2)
    if sys.argv[1] == "--get":
        remote, local = sys.argv[2], sys.argv[3]
        mode = "get"
    else:
        local, remote = sys.argv[1], sys.argv[2]
        mode = "put"
    vps = Vps()
    try:
        sftp = vps.client.open_sftp()
        if mode == "put":
            sftp.put(local, remote)
            stat = sftp.stat(remote)
            print(f"uploaded {local} -> {remote} ({stat.st_size} bytes)")
        else:
            sftp.get(remote, local)
            print(f"downloaded {remote} -> {local}")
        sftp.close()
    finally:
        vps.close()


if __name__ == "__main__":
    main()
