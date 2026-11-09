import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import shorts


ROOT = Path(__file__).resolve().parent
PREVIEW = ROOT / "demo/preview.mp4"
MEDIA = {"/": ROOT / "studio.html", "/studio.html": ROOT / "studio.html",
         "/preview.mp4": PREVIEW,
         **{f"/characters/{host}.png": ROOT / "characters" / f"{host}.png" for host in shorts.HOSTS}}
RUNS = {}
LOCK = threading.Lock()


def run_job(state, job, key, photos):
    try:
        directory = shorts.DATA_ROOT / "output/web" / job["id"]
        directory.mkdir(parents=True)
        paths = []
        for index, photo in enumerate(photos):
            suffix = Path(photo["name"]).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError("PNG, JPG, WebP 이미지를 선택하세요.")
            image = directory / f"photo-{index}{suffix}"
            image.write_bytes(base64.b64decode(photo["data"], validate=True))
            paths.append(image.name)
        manifest = directory / "products.json"
        job["images"] = paths
        shorts.normalize(job, directory, 0)
        shorts.write_json(manifest, [job])
        env = os.environ.copy()
        env["GEMINI_API_KEY"] = key
        commands = [["build", str(manifest)]]
        if not (shorts.DATA_ROOT / "hosts" / f'{job["host"]}.png').is_file():
            commands.insert(0, ["hosts", job["host"]])
        for command in commands:
            with subprocess.Popen(
                [sys.executable, "-X", "utf8", "-u", str(ROOT / "shorts.py"), *command],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            ) as process:
                for line in process.stdout:
                    with LOCK:
                        state["log"] = (state["log"] + line.replace(key, "[숨김]"))[-4500:]
                if process.wait():
                    raise ValueError("제작을 완료하지 못했습니다. 실행 기록을 확인하세요.")
        result = next((shorts.DATA_ROOT / "output/jobs" / job["id"]).glob("*/short.mp4"))
        with LOCK:
            state.update(status="done", video=f'/video/{job["id"]}.mp4', file=result)
    except (OSError, ValueError, StopIteration, KeyError, TypeError) as error:
        with LOCK:
            state.update(status="error", error=str(error).replace(key, "[숨김]"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if not self.local_request():
            return self.send_error(403)
        path = urlsplit(self.path).path
        if path == "/api/status":
            with LOCK:
                busy = any(run["status"] == "running" for run in RUNS.values())
            return self.send_json(200, {"connected": True, "key": bool(os.environ.get("GEMINI_API_KEY")), "busy": busy})
        if match := re.fullmatch(r"/api/run/([a-f0-9]{16})", path):
            with LOCK:
                state = RUNS.get(match[1])
                response = {k: v for k, v in state.items() if k != "file"} if state else None
            return self.send_json(200 if response else 404, response or {"error": "제작 기록을 찾을 수 없습니다."})
        file = MEDIA.get(path)
        if match := re.fullmatch(r"/video/web-([a-f0-9]{16})\.mp4", path):
            with LOCK:
                file = RUNS.get(match[1], {}).get("file")
        if not file or not file.is_file():
            return self.send_error(404)
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if (not self.local_request() or self.path != "/api/run"
                or self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}"):
            return self.send_error(403)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 1 <= length <= 30_000_000:
                raise ValueError("요청이 너무 큽니다. 상품 사진 용량을 줄여 주세요.")
            payload = json.loads(self.rfile.read(length))
            key = os.environ.get("GEMINI_API_KEY") or payload["key"].strip()
            if not key:
                raise ValueError("Gemini API 키를 입력하세요.")
            photos = payload["photos"]
            if not isinstance(photos, list) or not 1 <= len(photos) <= 4:
                raise ValueError("상품 사진을 1~4장 선택하세요.")
            job = {name: payload["job"][name] for name in
                   ("host", "seconds", "veo", "product", "facts", "affiliate_url")}
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            return self.send_json(400, {"error": str(error)})
        token = uuid.uuid4().hex[:16]
        job["id"] = f"web-{token}"
        with LOCK:
            busy = any(run["status"] == "running" for run in RUNS.values())
            if not busy:
                state = {"status": "running", "log": "작업을 준비하고 있습니다.\n"}
                RUNS[token] = state
        if busy:
            return self.send_json(409, {"error": "이미 제작 중입니다. 현재 작업이 끝난 뒤 다시 실행하세요."})
        threading.Thread(target=run_job, args=(state, job, key, photos), daemon=True).start()
        self.send_json(202, {"id": token})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        address = f"http://127.0.0.1:{server.server_port}/"
        print(f"Local URL: {address}", flush=True)
        if not args.no_browser:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
