"""Check the real local API, including an authenticated database request."""
import http.cookiejar
import json
import urllib.request


def check():
    jar = http.cookiejar.CookieJar()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with client.open("http://127.0.0.1:8765/", timeout=3) as response:
        assert b"JARVIS" in response.read()
    with client.open("http://127.0.0.1:8765/api/status", timeout=3) as response:
        assert json.load(response)["ok"]
    with client.open("http://127.0.0.1:8765/api/tasks", timeout=3) as response:
        assert isinstance(json.load(response), list)
    print("JARVIS dashboard and authenticated planner API are responding.")


if __name__ == "__main__":
    check()
