import os
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "products.json"
DEFAULT_REFERENCE = Path.home() / "Downloads" / "gemini_generated_video_10230075.mp4"


def run_console(args):
    status = subprocess.call([sys.executable, "-X", "utf8", str(ROOT / "shorts.py"), *args], cwd=ROOT)
    print("\n완료" if status == 0 else f"실행 실패 (코드 {status})")
    input("Enter를 누르면 이 창을 닫습니다.")
    return status


def main():
    window = tk.Tk()
    window.title("중년 남성 코디 쇼츠 제작기")
    window.minsize(590, 335)
    window.columnconfigure(0, weight=1)
    panel = ttk.Frame(window, padding=20)
    panel.grid(sticky="nsew")
    panel.columnconfigure(0, weight=1)
    panel.columnconfigure(1, weight=1)
    manifest = tk.StringVar(value=str(DEFAULT_MANIFEST))

    def report(action):
        try:
            action()
        except OSError as error:
            messagebox.showerror("실행 오류", str(error), parent=window)

    def start(*args):
        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        report(lambda: subprocess.Popen(
            [str(python), "-X", "utf8", str(Path(__file__).resolve()), "--run", *map(str, args)],
            cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE,
        ))

    def browse_manifest():
        path = filedialog.askopenfilename(parent=window, initialdir=ROOT,
                                         filetypes=[("상품 목록", "*.json")])
        if path:
            manifest.set(path)

    def edit_manifest():
        path = Path(manifest.get().strip()).expanduser()
        if path == DEFAULT_MANIFEST and not path.exists():
            shutil.copyfile(ROOT / "products.example.json", path)
        if not path.is_file():
            messagebox.showinfo("상품 목록", "먼저 편집할 JSON 파일을 선택하세요.", parent=window)
            return
        subprocess.Popen(["notepad.exe", str(path)], cwd=ROOT)

    def build(*flags):
        path = Path(manifest.get().strip()).expanduser()
        if not path.is_file():
            messagebox.showinfo("상품 목록", "상품 목록을 편집하거나 기존 JSON 파일을 선택하세요.", parent=window)
            return
        start("build", path.resolve(), *flags)

    def preview():
        path = DEFAULT_REFERENCE
        if not path.is_file():
            chosen = filedialog.askopenfilename(parent=window, title="참고 영상 선택",
                                                filetypes=[("MP4 영상", "*.mp4")])
            if not chosen:
                return
            path = Path(chosen)
        start("build", ROOT / "demo.json", "--preview", path)

    ttk.Label(panel, text="중년 남성 코디 쇼츠 제작기", font=("맑은 고딕", 17, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    ttk.Label(panel, text="한유나 · 서혜원 · 윤서하  |  10~20초 세로 쇼츠").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))
    ttk.Label(panel, text="상품 목록 파일").grid(row=2, column=0, sticky="w")
    ttk.Entry(panel, textvariable=manifest).grid(row=3, column=0, sticky="ew", pady=(3, 9))
    ttk.Button(panel, text="파일 선택", command=browse_manifest).grid(
        row=3, column=1, sticky="ew", padx=(8, 0), pady=(3, 9))

    actions = [
        ("상품 목록 편집", lambda: report(edit_manifest)),
        ("캐릭터 준비", lambda: start("hosts", "all")),
        ("Veo 예상 비용", lambda: build("--estimate")),
        ("쇼츠 제작", build),
        ("무료 미리보기", preview),
        ("결과 폴더 열기", lambda: report(open_output)),
    ]
    for index, (label, action) in enumerate(actions):
        ttk.Button(panel, text=label, command=action).grid(
            row=4 + index // 2, column=index % 2, sticky="ew", padx=(0, 8) if index % 2 == 0 else 0,
            pady=3, ipady=4,
        )
    ttk.Label(panel, text="유료 API 작업은 별도 콘솔에서 진행되며 필요한 경우 키 입력을 요청합니다.").grid(
        row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))
    window.mainloop()


def open_output():
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    os.startfile(output)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--run"]:
        sys.exit(run_console(sys.argv[2:]))
    main()
