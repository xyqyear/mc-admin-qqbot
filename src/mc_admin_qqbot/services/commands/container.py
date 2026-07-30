from dataclasses import dataclass

from ...config import Settings
from ...mcadmin import McAdminClient
from ...repositories import Repository
from .ban import BanCommand
from .banlist import BanlistCommand
from .list import ListCommand
from .restart import RestartCommand
from .say import SayCommand
from .targeting import ServerTargetResolver
from .unban import UnbanCommand
from .whitelist import WhitelistCommand


@dataclass(frozen=True)
class CommandServices:
    list_servers: ListCommand
    say: SayCommand
    whitelist: WhitelistCommand
    ban: BanCommand
    unban: UnbanCommand
    banlist: BanlistCommand
    restart: RestartCommand


def build_command_services(
    settings: Settings,
    client: McAdminClient,
    repository: Repository,
) -> CommandServices:
    targets = ServerTargetResolver(settings, client)
    return CommandServices(
        list_servers=ListCommand(settings, client),
        say=SayCommand(settings, client, repository),
        whitelist=WhitelistCommand(client, targets),
        ban=BanCommand(client, targets),
        unban=UnbanCommand(client, targets),
        banlist=BanlistCommand(client, targets),
        restart=RestartCommand(settings, client, repository, targets),
    )
