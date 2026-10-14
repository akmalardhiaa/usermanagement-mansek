"""Container readiness; AD mode only checks the Java HTTP service, not an LDAP bind."""
import sys
import urllib.request

url = "http://ad-service:8081/v1/health" if "--ad" in sys.argv else "http://127.0.0.1:3000/healthz"
with urllib.request.urlopen(url, timeout=4) as response:
    if response.status != 200:
        raise SystemExit(1)
