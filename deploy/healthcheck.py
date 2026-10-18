"""Container HEALTHCHECK: exit 0 when the GUI server answers HTTP at all.

On a non-loopback bind the GUI demands a token, so an unauthenticated probe is
answered 401. That still proves the process is serving; only a refused or
timed-out connection counts as unhealthy.
"""
import sys
import urllib.error
import urllib.request

try:
    urllib.request.urlopen("http://127.0.0.1:8777/", timeout=4)
except urllib.error.HTTPError:
    pass
except Exception:
    sys.exit(1)
