from fastapi import FastAPI

from app.routers import accounts, entries


def create_app() -> FastAPI:
    app = FastAPI(title="mini-ledger", version="0.1.0")
    app.include_router(accounts.router)
    app.include_router(entries.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        # Deliberately does not touch the database. Liveness, not readiness.
        return {"status": "ok"}

    return app


app = create_app()
