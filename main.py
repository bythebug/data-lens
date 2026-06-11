from fastapi import FastAPI

from api.routes import router

app = FastAPI(title="data-lens")
app.include_router(router)
