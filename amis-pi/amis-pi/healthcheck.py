"""Container liveness check without exposing configuration or credentials."""
import sys
import urllib.error
import urllib.request
from . import config


def main():
    try:
        cfg = config.load(sys.argv[1] if len(sys.argv) > 1 else "/config/config.toml")
        if not cfg["web"]["enabled"]:
            return 0  # No HTTP endpoint requested; container process is still supervised.
        host = cfg["web"]["host"]
        if host in ("", "0.0.0.0"):
            host = "127.0.0.1"
        with urllib.request.urlopen("http://%s:%s/livez" % (host, cfg["web"]["port"]), timeout=3) as res:
            return 0 if res.status == 200 else 1
    except (OSError, ValueError, urllib.error.URLError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
