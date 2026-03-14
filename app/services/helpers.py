"""Shared async utility stubs — replace with real business logic as the app grows."""
import os
import dotenv

dotenv.load_dotenv()

async def dummy_function() -> dict:
    return {"message": "Helper function called successfully!", "status": "positive"}


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)
