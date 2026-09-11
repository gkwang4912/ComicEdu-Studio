import config

config.apply()

from backend.app.main import app


def frontend_url() -> str:
    port = "" if int(config.PORT) == 80 else f":{int(config.PORT)}"
    path = str(config.FRONTEND_PATH or "/Portal.html")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"http://127.0.0.1{port}{path}"


def open_frontend_when_ready(url: str) -> None:
    import time
    import urllib.request
    import webbrowser

    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    print(f"[ComicEdu] Frontend: {url}", flush=True)
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.5)
    print(f"[ComicEdu] Backend 已啟動，請手動開啟前端: {url}", flush=True)


if __name__ == "__main__":
    import threading
    import uvicorn

    print(f"[ComicEdu] Backend listening: http://{config.HOST}:{config.PORT}", flush=True)
    print(f"[ComicEdu] Health URL: http://127.0.0.1:{config.PORT}/api/v1/health", flush=True)
    print(f"[ComicEdu] Frontend origin: {config.FRONTEND_ORIGIN or '(not configured; local bundled frontend available)'}", flush=True)
    print(f"[ComicEdu] ComfyUI URL: {config.COMFYUI_URL}", flush=True)
    url = frontend_url()
    if config.AUTO_OPEN_BROWSER:
        threading.Thread(target=open_frontend_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(app, host=config.HOST, port=config.PORT)
