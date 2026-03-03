from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


app = FastAPI(
    title="Vallum Jetson Dashboard",
    description="Unified lights/motors/inspection dashboard running on Jetson",
    version="0.1.0",
)

# Resolve static directory relative to this file so it works no matter
# where the process is started from (project root or webapp/).
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ASSETS_DIR = BASE_DIR.parent  # project root (contains Vallum-Software-Logo.png)

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
app.mount("/assets", StaticFiles(directory=ASSETS_DIR, html=False), name="assets")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )

