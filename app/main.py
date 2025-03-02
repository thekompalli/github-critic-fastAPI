from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import repositories

app = FastAPI(
    title="GitHub Critic",
    description="An API that retrieves GitHub repositories and roasts their code.",
    version="0.1.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(repositories.router, prefix="/api")

@app.get("/")
async def root():
    return {
        "message": "Welcome to the GitHub Critic API",
        "docs": "/docs",
        "redoc": "/redoc"
    }