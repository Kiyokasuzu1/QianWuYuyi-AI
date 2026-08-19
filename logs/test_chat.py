# -*- coding: utf-8 -*-
"""Send a real user message to pipeline_server and capture response."""
import json
import sys
from datetime import datetime
from urllib import request as urlreq
from urllib.error import HTTPError, URLError

URL = "http://127.0.0.1:8765/chat"
MSG = "我今天完成了一个重要的项目,感觉很有成就感"

def utc_now():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")

if __name__ == "__main__":
    body = json.dumps({"message": MSG}, ensure_ascii=False).encode("utf-8")
    req = urlreq.Request(URL, data=body, method="POST", headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urlreq.urlopen(req, timeout=60) as resp:
            data = resp.read().decode("utf-8")
            print(f"[{utc_now()}] STATUS={resp.status}")
            print(f"[{utc_now()}] RESPONSE={data}")
    except HTTPError as e:
        data = e.read().decode("utf-8")
        print(f"[{utc_now()}] HTTP_ERROR={e.code} BODY={data}")
    except URLError as e:
        print(f"[{utc_now()}] URL_ERROR={e}")
    except Exception as e:
        print(f"[{utc_now()}] ERROR={type(e).__name__}: {e}")
