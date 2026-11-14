from __future__ import annotations

import uvicorn

from air_rag_service.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "air_rag_service.main:app",
        host=settings.app.host,
        port=settings.app.port,
        workers=1,
    )


if __name__ == "__main__":
    main()
