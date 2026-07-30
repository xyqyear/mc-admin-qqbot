from ..services.game_commands.bind import BindCommand
from .commands.bind import BindGameCommand
from .router import GameCommandRouter


def build_game_command_router() -> GameCommandRouter:
    return GameCommandRouter({"bind": BindGameCommand(BindCommand())})
