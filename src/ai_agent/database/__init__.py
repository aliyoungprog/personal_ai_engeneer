from loguru import logger
from tortoise import Tortoise

from ai_agent.settings import settings

tortoise_config = {
    "connections": {"default": settings.database_url},
    "apps": {
        "models": {
            "models": ["ai_agent.database.models", "aerich.models"],
            "default_connection": "default",
        },
    },
}


async def database_connect() -> None:
    logger.info("Connecting to database")
    await Tortoise.init(config=tortoise_config)


async def database_disconnect() -> None:
    logger.info("Disconnecting from database")
    await Tortoise.close_connections()


__all__ = (
    "database_connect",
    "database_disconnect",
    "tortoise_config",
)
