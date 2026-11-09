import argparse
import base64
import ctypes
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import shorts


ROOT = Path(__file__).resolve().parent
PREVIEW = ROOT / "demo/preview.mp4"
MEDIA = {"/": ROOT / "studio.html", "/studio.html": ROOT / "studio.html",
         "/preview.mp4": PREVIEW, "/characters/A-indoor.png": ROOT / "characters/A-indoor.png",
         **{f"/characters/{host}.png": ROOT / "characters" / f"{host}.png" for host in shorts.HOSTS}}
RUNS = {}
LOCK = threading.Lock()
STAGES = {
    "prepare": ("상품 사진 확인", 8),
    "host": ("호스트 기준 이미지 생성", 45),
    "script": ("대본 작성", 15),
    "voice": ("음성 생성", 35),
    "veo": ("Veo 인트로 생성", 120),
    "render": ("영상 합성과 MP4 저장", 20),
}
KEY_FILE = shorts.DATA_ROOT / ".gemini-key.dpapi"


def saved_videos(token=None):
    folder = shorts.DATA_ROOT / "output/jobs"
    pattern = f"web-{token}/*/short.mp4" if token else "web-*/*/short.mp4"
    for file in folder.glob(pattern):
        if not re.fullmatch(r"web-[a-f0-9]{16}", file.parent.parent.name):
            continue
        if re.fullmatch(r"[a-f0-9]{20}", file.parent.name) and (file.parent / "result.json").is_file():
            try:
                yield file.stat().st_mtime, file
            except FileNotFoundError:
                continue


def saved_video(token=None):
    return max(saved_videos(token), default=(0, None))[1]


def video_info(file):
    token = file.parent.parent.name.removeprefix("web-")
    return {"id": token, "video": f"/video/web-{token}.mp4", "filePath": str(file)}


def protect_key(value, *, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.c_void_p)]

    try:
        raw = base64.b64decode(value, validate=True) if decrypt else value.encode("ascii")
        buffer = ctypes.create_string_buffer(raw)
        source = Blob(len(raw), ctypes.addressof(buffer))
        target = Blob()
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        transform = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        transform.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
        transform.restype = ctypes.c_int
        if not transform(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            output = ctypes.string_at(target.data, target.size)
            return output.decode("ascii") if decrypt else base64.b64encode(output).decode("ascii")
        finally:
            release = ctypes.WinDLL("kernel32").LocalFree
            release.argtypes = [ctypes.c_void_p]
            release.restype = ctypes.c_void_p
            release(target.data)
    except (OSError, ValueError):
        raise ValueError("Windows에서 저장된 키를 처리하지 못했습니다. Gemini API 키를 다시 입력하세요.") from None


def save_key(key):
    encrypted = protect_key(key)
    with tempfile.NamedTemporaryFile(dir=KEY_FILE.parent, delete=False) as temp:
        pending = Path(temp.name)
        try:
            temp.write(encrypted.encode("ascii"))
            temp.close()
            pending.replace(KEY_FILE)
        finally:
            pending.unlink(missing_ok=True)


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
        env["OPPA_WEB_PROGRESS"] = "1"
        commands = [["build", str(manifest)]]
        footwear = job.get("footwear")
        if not shorts.host_image(job["host"], footwear).is_file():
            flags = ["--footwear", footwear] if footwear else []
            commands.insert(0, ["hosts", job["host"], *flags])
        else:
            with LOCK:
                state["stages"].remove("host")
        for command in commands:
            if command[0] == "hosts":
                with LOCK:
                    state.update(stage="host", stage_started=time.monotonic())
            with subprocess.Popen(
                [sys.executable, "-X", "utf8", "-u", str(ROOT / "shorts.py"), *command],
                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            ) as process:
                for line in process.stdout:
                    with LOCK:
                        if line.startswith("OPPA_STAGE ") and (stage := line.removeprefix("OPPA_STAGE ").strip()) in STAGES:
                            state.update(stage=stage, stage_started=time.monotonic())
                            continue
                        state["log"] = (state["log"] + line.replace(key, "[숨김]"))[-4500:]
                if process.wait():
                    raise ValueError("제작을 완료하지 못했습니다. 실행 기록을 확인하세요.")
        result = saved_video(job["id"].removeprefix("web-"))
        if not result:
            raise ValueError("저장된 완성 MP4를 찾을 수 없습니다.")
        with LOCK:
            state.update(status="done", file=result, finished=time.monotonic())
    except (OSError, ValueError, KeyError, TypeError) as error:
        with LOCK:
            state.update(status="error", error=str(error).replace(key, "[숨김]"), finished=time.monotonic())


def progress(state):
    stages = state["stages"]
    index = stages.index(state["stage"])
    now = state.get("finished", time.monotonic())
    label, expected = STAGES[state["stage"]]
    remaining_stage = expected - (now - state["stage_started"])
    remaining = max(0, remaining_stage) + sum(STAGES[stage][1] for stage in stages[index + 1:])
    return {
        "status": state["status"], "label": label, "step": index + 1, "steps": len(stages),
        "elapsed": int(now - state["started"]),
        "remaining": round(remaining) if state["status"] == "running" and remaining_stage > 0 else None,
        **{name: state[name] for name in ("log", "error") if name in state},
        **(video_info(state["file"]) if state["status"] == "done" else {}),
    }


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
                active = next((key for key, run in RUNS.items() if run["status"] == "running"), None)
            env_key = bool(os.environ.get("GEMINI_API_KEY"))
            saved = KEY_FILE.is_file()
            latest = saved_video()
            return self.send_json(200, {"connected": True, "key": env_key or saved,
                                        "envKey": env_key, "savedKey": saved, "busy": active is not None,
                                        "activeRun": active, "latestVideo": video_info(latest) if latest else None})
        if match := re.fullmatch(r"/api/run/([a-f0-9]{16})", path):
            with LOCK:
                state = RUNS.get(match[1])
                response = progress(state) if state else None
            return self.send_json(200 if response else 404, response or {"error": "제작 기록을 찾을 수 없습니다."})
        file = MEDIA.get(path)
        if match := re.fullmatch(r"/video/web-([a-f0-9]{16})\.mp4", path):
            with LOCK:
                file = RUNS.get(match[1], {}).get("file")
            file = file or saved_video(match[1])
        if not file or not file.is_file():
            return self.send_error(404)
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if (not self.local_request()
                or self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}"):
            return self.send_error(403)
        if match := re.fullmatch(r"/api/open-folder/([a-f0-9]{16})", self.path):
            file = saved_video(match[1])
            if not file:
                return self.send_json(404, {"error": "저장된 MP4를 찾을 수 없습니다."})
            try:
                os.startfile(file.parent)
            except OSError:
                return self.send_json(500, {"error": "탐색기를 열지 못했습니다. 표시된 경로를 사용하세요."})
            return self.send_json(200, {"opened": True})
        if self.path not in {"/api/run", "/api/key"}:
            return self.send_error(404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            limit = 2048 if self.path == "/api/key" else 30_000_000
            if not 1 <= length <= limit:
                raise ValueError("요청이 너무 큽니다. 상품 사진 용량을 줄여 주세요.")
            payload = json.loads(self.rfile.read(length))
            typed_key = payload.get("key", "").strip()
            if typed_key and (len(typed_key) > 512 or not typed_key.isascii() or any(ch.isspace() for ch in typed_key)):
                raise ValueError("Gemini API 키 형식을 확인하세요.")
            if self.path == "/api/key":
                if payload.get("remove") is True:
                    KEY_FILE.unlink(missing_ok=True)
                    return self.send_json(200, {"savedKey": False})
                if not typed_key:
                    raise ValueError("저장할 Gemini API 키를 입력하세요.")
                save_key(typed_key)
                return self.send_json(200, {"savedKey": True})
            key = os.environ.get("GEMINI_API_KEY") or typed_key
            if not key and KEY_FILE.is_file():
                key = protect_key(KEY_FILE.read_text(encoding="ascii"), decrypt=True)
            if not key:
                raise ValueError("Gemini API 키를 입력하세요.")
            photos = payload["photos"]
            if not isinstance(photos, list) or not 1 <= len(photos) <= 4:
                raise ValueError("상품 사진을 1~4장 선택하세요.")
            job = {name: payload["job"][name] for name in
                   ("host", "seconds", "veo", "product", "facts", "affiliate_url")}
            footwear = payload["job"].get("footwear")
            shorts.check_footwear(job["host"], footwear)
            if footwear:
                job["footwear"] = footwear
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            return self.send_json(400, {"error": str(error)})
        except OSError:
            return self.send_json(500, {"error": "저장된 Gemini API 키 파일에 접근할 수 없습니다."})
        token = uuid.uuid4().hex[:16]
        job["id"] = f"web-{token}"
        with LOCK:
            busy = any(run["status"] == "running" for run in RUNS.values())
            if not busy:
                now = time.monotonic()
                state = {"status": "running", "log": "", "stage": "prepare", "stages": list(STAGES),
                         "started": now, "stage_started": now}
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
