from fastapi import FastAPI

from bleep.config import Settings
from bleep.wiring import build_parts


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Bleep")
    app.state.settings = settings
    app.state.parts = build_parts(settings)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
