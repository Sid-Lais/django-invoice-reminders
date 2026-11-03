"""Check the dashboard through Gunicorn using an allowed local Host header."""

import os
from urllib.request import Request, urlopen

host = os.environ.get("ALLOWED_HOSTS", "localhost").split(",")[0].strip().lstrip(".")
request = Request("http://127.0.0.1:8000/", headers={"Host": host.replace("*", "localhost")})
with urlopen(request, timeout=5) as response:  # noqa: S310 - fixed local HTTP URL
    if response.status != 200:
        raise SystemExit(1)
