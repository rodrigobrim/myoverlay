class NoDeviceError(RuntimeError):
    """Neither transport could reach a logger."""


class UserInputNeededError(RuntimeError):
    """A logger is in range but needs something only the user can give.

    Which of several loggers to talk to, or the password for the one chosen.
    Distinct from NoDeviceError because it is not a "nothing there" result:
    `connect` lets it through instead of folding it into the tried-everything
    summary, so the message reaches the user intact.
    """
