from fastapi import FastAPI

from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    config.validate_for_process("web")
    app = FastAPI(title="AoC Scoreboard", docs_url=None, redoc_url=None)
    app.state.settings = config

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


app = create_app()
