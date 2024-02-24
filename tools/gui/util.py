from tkinter import Widget  # Import the base class for Tkinter widgets


def enable(w: Widget, on: bool) -> None:
    """
    Enable or disable a Tkinter widget.

    Parameters:
    - w: The Tkinter widget to be enabled or disabled.
    - on: A boolean value where True enables the widget and False disables it.
    """
    state_action = "!disabled" if on else "disabled"
    w.state([state_action])
