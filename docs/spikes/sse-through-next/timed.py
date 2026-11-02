"""Print when each server-sent event arrives, in seconds from the request.

Usage: python3 timed.py URL [--gzip]. With --gzip it asks for compression as a browser does.
"""

import sys
import time
import urllib.request
import zlib

url = sys.argv[1]
headers = {"Accept": "text/event-stream", "Last-Event-ID": "7", "Cookie": "bleep_session=test"}
if "--gzip" in sys.argv:
    headers["Accept-Encoding"] = "gzip, deflate, br"
start = time.time()
response = urllib.request.urlopen(urllib.request.Request(url, headers=headers))
encoding = response.headers.get("content-encoding")
print("content-encoding:", encoding)
gunzip = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
while chunk := response.read1(65536):
    text = gunzip.decompress(chunk) if gunzip else chunk
    for line in text.decode().splitlines():
        if line.startswith("id:"):
            print(f"{time.time() - start:5.2f}s {line}")
