from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="AoC Scoreboard", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


app = create_app()
