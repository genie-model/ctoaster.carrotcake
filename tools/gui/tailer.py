import os
import os.path
from typing import Callable, Optional

# Class for "following" files, i.e. capturing changes in a file as a
# process writes output into it.  Used for capturing cTOASTER model and
# build process output for display in the GUI.

class Tailer:
    """
    Class for "following" files, capturing changes in a file as a process writes output into it.
    Used for capturing GENIE model and build process output for display in the GUI.
    """

    def __init__(self, app, fname: str) -> None:
        """
        Initializes the Tailer object.

        :param app: A Tkinter application object used for timers.
        :param fname: The filename of the file to follow.
        """
        self.app = app  # Tkinter application.
        self.fname: str = fname  # Filename to follow.
        self.pos: int = 0  # Current position in the file.
        self.fp: Optional[os.fdopen] = None  # Current file handle, initially None.
        self.after_id: Optional[int] = (
            None  # ID of the current "after" timer, initially None.
        )
        self.cb: Optional[Callable] = (
            None  # Callback for reporting file changes, initially None.
        )

    def start(self, cb: Callable) -> None:
        """
        Start tailing the file, calling the given callback with new data as it's written.

        :param cb: A callback function that takes no arguments and returns None.
        """
        self.cb = cb
        # Simplified condition check for readability
        if not self.after_id:
            self.after_id = self.app.after(0, self.read)

    def stop(self) -> None:
        """
        Stop tailing the file and clean up resources.
        """
        # Use a guard clause to reduce nesting and improve readability
        if self.after_id:
            self.app.after_cancel(self.after_id)
            self.after_id = None

        if self.fp:
            self.fp.close()
            self.fp = None

    def read(self) -> None:
        """
        Attempt to read new data from the tailed file and report to the callback.
        """
        # Check if the file is not open yet and if it exists.
        if not self.fp and os.path.exists(self.fname):
            # Open the file and start from the beginning.
            self.fp = open(self.fname, "r")  # Specify mode explicitly for clarity
            self.pos = 0

        if self.fp:
            # Determine the current size of the file.
            self.fp.seek(0, os.SEEK_END)
            size = self.fp.tell()  # Local variable for clarity
            if size > self.pos:
                # If new content exists, read it and update the position.
                self.fp.seek(self.pos, os.SEEK_SET)
                new_data = self.fp.read(
                    size - self.pos
                )  # More descriptive variable name
                self.pos = size
                if self.cb:  # Ensure callback is not None before calling
                    self.cb(new_data)

        # Schedule the next read.
        self.after_id = self.app.after(500, self.read)
