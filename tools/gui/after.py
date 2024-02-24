import tkinter as tk
from tkinter import ttk

#  AFTER TIMER HANDLING
#
# This is a little nasty: we use a lot of "after" timers, and Tkinter
# doesn't seem to have a built-in way to clean them all up before
# exit.  If you don't clean them up, the application hangs on exit
# with a bunch of error messages.  So, we override the after handling
# here to make sure everything does get cleaned up before exit.  This
# class is used as a mixin to the main application class.


class AfterHandler:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.callback_to_id = {}
        self.id_to_callback = {}
        self.sequence_number = 0

    def after(self, ms: int, func=None, *args):
        callback_id = self.root.after(
            ms, self.trigger, self.sequence_number, func, *args
        )
        self.callback_to_id[self.sequence_number] = callback_id
        self.id_to_callback[callback_id] = self.sequence_number
        self.sequence_number += 1
        return callback_id

    def after_cancel(self, callback_id: int) -> None:
        del self.callback_to_id[self.id_to_callback[callback_id]]
        del self.id_to_callback[callback_id]
        self.root.after_cancel(callback_id)

    def trigger(self, sequence_number: int, func, *args) -> None:
        del self.id_to_callback[self.callback_to_id[sequence_number]]
        del self.callback_to_id[sequence_number]
        if func:
            func(*args)

    def quit(self) -> None:
        # Attempt to cancel all known after callbacks.
        for callback_id in list(self.id_to_callback.keys()):
            self.root.after_cancel(callback_id)

        # Additional safeguard to cancel any lingering or newly scheduled callbacks.
        self.root.after(100, self.force_quit)

    def force_quit(self) -> None:
        # Force the application to quit by destroying the root window and calling quit again.
        # This is a fallback method in case some after callbacks were missed.
        self.root.destroy()
        self.root.quit()
