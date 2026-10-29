"""Safe multi-user entry point: python cloud_app.py -p 7788."""
import os
os.environ.setdefault("COSIGHT_DATA_DIR", str(__import__("pathlib").Path(__file__).resolve().parent / "data"))
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from cosight_server.deep_research.services.account_middleware import AccountMiddleware
from cosight_server.deep_research.routers.accounts import accountRouter
from cosight_server.deep_research.routers.learning import learningRouter
from cosight_server.deep_research.routers.private_files import privateFileRouter
from cosight_server.deep_research.routers.conversations import conversationRouter
from cosight_server.deep_research.routers.mistakes import mistakeRouter
from cosight_server.deep_research.routers.plans import planRouter
from cosight_server.deep_research.routers.experience import experienceRouter


def create_app():
    app = FastAPI(title="Co-Sight智学", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(AccountMiddleware)
    app.include_router(accountRouter)
    prefix = "/api/nae-deep-research/v1"
    for router in (learningRouter, privateFileRouter, conversationRouter, mistakeRouter, planRouter, experienceRouter):
        app.include_router(router, prefix=prefix)
    from cosight_server.deep_research.routers.study_router import studyRouter
    from cosight_server.deep_research.routers.study_socket import studySocketRouter
    app.include_router(studyRouter)
    app.include_router(studySocketRouter)
    web_root = Path(__file__).resolve().parent / "cosight_server" / "web"

    @app.get("/cosight/")
    def study_home(request: Request):
        if not request.state.user:
            return RedirectResponse("/cosight/login.html", status_code=303)
        return FileResponse(web_root / "index.html", media_type="text/html")

    app.mount("/cosight", StaticFiles(directory=web_root, html=True))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "mode": "langgraph-study-agent"}

    @app.get("/")
    def home():
        return RedirectResponse("/cosight/")

    return app


app = create_app()

if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", type=int, default=7790)
    args = parser.parse_args()
    uvicorn.run(app, host=os.getenv("COSIGHT_HOST", "127.0.0.1"), port=args.port, ws_max_size=128 * 1024)
