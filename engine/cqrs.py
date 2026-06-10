from __future__ import annotations
from engine.command import Command, CommandBus, CommandHandler, logging_middleware, timing_middleware, validation_middleware
from engine.query import Query, QueryBus, QueryHandler
__all__ = ["Command","CommandBus","CommandHandler","Query","QueryBus","QueryHandler","create_default_command_bus","create_default_query_bus"]

def create_default_command_bus() -> CommandBus:
    bus = CommandBus()
    bus.use(logging_middleware); bus.use(validation_middleware); bus.use(timing_middleware)
    return bus

def create_default_query_bus() -> QueryBus:
    return QueryBus()
