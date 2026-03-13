"""Shared async utility stubs — replace with real business logic as the app grows."""
import os
import dotenv
dotenv.load_dotenv()

async def dummy_function() -> dict:
    return {"message": "Helper function called successfully!", "status": "positive"}



def read_secrets() -> dict:
    PORT = int(os.getenv('PORT', '8080'))
    DB_USER = os.getenv('DB_USER', 'psqlroot')
    DB_PASSWORD = os.getenv('DB_PASSWORD', 'password')
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', 5432))
    DB_NAME = os.getenv('DB_NAME', 'finance-manager')
    DB_SCHEMA = os.getenv('DB_SCHEMA', 'finance')
    APP_ENV = os.getenv('APP_ENV', 'dev')

    return {
        "APP_ENV" : APP_ENV,
        "PORT": PORT,
        "DB_USER": DB_USER,
        "DB_PASSWORD": DB_PASSWORD,
        "DB_HOST": DB_HOST,
        "DB_PORT": DB_PORT,
        "DB_NAME": DB_NAME,
        "DB_SCHEMA": DB_SCHEMA
    }