from .application import Application

_application: Application | None = None


def set_application(application: Application) -> None:
    global _application
    _application = application


def get_application() -> Application:
    if _application is None:
        raise RuntimeError("Application has not been configured")
    return _application
