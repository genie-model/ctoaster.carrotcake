import os
import os.path
import subprocess as sp
import sys
import tkinter as tk
import tkinter.messagebox as tkMB
from tkinter import ttk
from typing import List, Optional

from gui.tailer import *
from gui.util import *

import utils as U

# Fixed version of base dialog class from tkSimpleDialog.  The default
# tkSimpleDialog class doesn't deal well with resizing of dialogs,
# which we really need for the model build output window.  See the
# original tkSimpleDialog code for documentation.


class SimpleDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget, title: str = None) -> None:
        super().__init__(parent)
        self.withdraw()
        if parent.winfo_viewable():
            self.transient(parent)
        if title:
            self.title(title)
        self.parent = parent
        self.result = None
        body = ttk.Frame(self)
        self.initial_focus = self.body(body)
        body.grid(row=0, column=0, sticky=tk.N + tk.E + tk.S + tk.W)
        box = self.buttonbox()
        box.grid(row=1, column=0, sticky=tk.E + tk.S + tk.W)
        top = self.winfo_toplevel()
        top.rowconfigure(0, weight=1)
        top.columnconfigure(0, weight=1)
        if not self.initial_focus:
            self.initial_focus = self
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        if self.parent is not None:
            self.geometry(
                "+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50)
            )
        self.deiconify()  # Make the window visible.
        self.initial_focus.focus_set()  # Focus on the initial widget.
        self.wait_visibility()  # Wait until the window is visible.
        self.grab_set()  # Make modal.
        self.wait_window(self)  # Wait until the window is destroyed.

    def destroy(self) -> None:
        """
        Destroy the dialog, cleaning up any references to widget objects.
        """
        self.initial_focus = None
        super().destroy()

    def body(self, master: tk.Widget) -> None:
        """
        Create the body of the dialog. Intended to be overridden in subclasses.

        :param master: The parent widget.
        """
        pass  # Designed to be overridden in subclass.

    def buttonbox(self) -> ttk.Frame:
        """
        Create the standard button box. Override if you don't want the
        standard buttons.

        :return: The frame containing the buttons.
        """
        box = ttk.Frame(self)
        self.cancel_button = ttk.Button(
            box, text="Cancel", width=10, command=self.cancel
        )
        self.cancel_button.pack(side=tk.RIGHT, padx=5, pady=5)

        self.ok_button = ttk.Button(
            box, text="OK", width=10, command=self.ok, default=tk.ACTIVE
        )
        self.ok_button.pack(side=tk.RIGHT, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        return box

    def ok(self, event: Optional[tk.Event] = None) -> None:
        """
        Handles the 'OK' action of the dialog.

        :param event: The event that triggered this method, if any.
        """
        if not self.validate():
            self.initial_focus.focus_set()  # Put focus back if validation fails.
            return
        self.withdraw()
        self.update_idletasks()
        try:
            self.apply()
        finally:
            self.cancel()

    def cancel(self, event: Optional[tk.Event] = None) -> None:
        """
        Cancels the dialog, reverting any temporary changes.

        :param event: The event that triggered this method, if any.
        """
        if self.parent is not None:
            self.parent.focus_set()
        self.destroy()

    def validate(self) -> bool:
        """
        Validates the inputs in the dialog.

        :return: True if the validation passes, otherwise False.
        """
        return (
            True  # Initially returns True as a placeholder for actual validation logic.
        )

    def apply(self) -> None:
        """
        Applies the changes specified in the dialog.
        """
        pass  # Placeholder for the method to apply changes, to be overridden in subclasses.


# Dialog for moving and/or renaming jobs and folders.  Has an option
# menu allowing the user to select a new folder to move the item into,
# plus a text field to allow the user to give a new name for the item.


class MoveRenameDialog(SimpleDialog):
    def __init__(
        self,
        full_path: str,
        is_folder: bool,
        folders: List[str],
        parent: Optional[tk.Widget] = None,
    ) -> None:
        """
        Initialize a dialog for moving or renaming a file or folder.

        :param full_path: The full path to the file or folder being moved or renamed.
        :param is_folder: Boolean indicating whether the item is a folder.
        :param folders: A list of folder paths for validating the new location.
        :param parent: The parent widget. Defaults to tk._default_root if not provided.
        """
        if not parent:
            parent = tk._default_root
        self.orig_folder, self.orig_name = os.path.split(full_path)
        self.is_folder = is_folder
        self.new_folder = None
        self.new_name = None
        self.folder_changed = False
        self.name_changed = False
        self.folders = folders
        self.result = False
        super().__init__(parent, "Move/rename job")

    def body(self, master: tk.Widget) -> ttk.Entry:
        """
        Create the dialog body.

        :param master: The parent widget.
        :return: The widget that should have initial focus.
        """
        # Label for the folder selection
        folder_label = ttk.Label(master, text="Folder:")
        folder_label.grid(column=0, row=0, pady=5, padx=5, sticky=tk.W)
        self.folder = ttk.Combobox(
            master, values=self.folders, width=50, state=["readonly"]
        )
        self.folder.grid(column=1, row=0, pady=5, sticky=tk.W + tk.E)
        self.folder.set(self.orig_folder)

        # Label for the name entry
        name_label = ttk.Label(master, text="Name:")
        name_label.grid(column=0, row=1, pady=5, padx=5, sticky=tk.W)
        self.name = ttk.Entry(master, width=50)
        self.name.grid(column=1, row=1, pady=5, sticky=tk.W)
        self.name.insert(0, self.orig_name)

        return self.name

    def validate(self) -> bool:
        """
        Validates the user input before closing the dialog.

        :return: True if the validation passes, False otherwise.
        """
        if len(self.name.get()) == 0:
            tkMB.showwarning("Illegal value", "New name can't be empty!", parent=self)
            return False
        if self.is_folder and self.folder.get().startswith(self.orig_folder):
            tkMB.showwarning(
                "Illegal move",
                "Can't move a folder into one of its own descendants!",
                parent=self,
            )
            return False
        return True

    def apply(self) -> None:
        """
        Applies the user input, preparing the result to be used after the dialog closes.
        """
        self.new_folder = self.folder.get()
        self.new_name = self.name.get()
        self.folder_changed = self.new_folder != self.orig_folder
        self.name_changed = self.new_name != self.orig_name
        self.result = self.folder_changed or self.name_changed


# Dialog for managing model rebuilds.  This has a little state machine
# for keeping track of whether the build is running or not and uses a
# Tailer object to capture the build output into a text widget.  The
# build itself is done using the "go" script in a particular job
# directory.


class BuildExecutableDialog(SimpleDialog):
    def __init__(self, app, dir: str, parent: Optional[tk.Widget] = None) -> None:
        """
        Initialize a dialog for building the model executable.

        :param app: The application object.
        :param dir: The directory where the build should take place.
        :param parent: The parent widget. Defaults to the root Tk widget if None.
        """
        self.app = app
        self.dir = dir
        self.state = "PENDING"
        self.result = False
        self.tailer = None
        self.pipe = None
        if not parent:
            parent = tk._default_root
        super().__init__(parent, "Build model executable")

    def destroy(self) -> None:
        """
        Clean up resources and terminate any running processes before destroying the dialog.
        """
        if self.state == "RUNNING":
            if self.tailer:
                self.tailer.stop()
            self.tailer = None
            self.message("KILLING EXECUTABLE BUILD")
            if self.pipe:
                self.pipe.terminate()
                self.pipe.wait()
        super().destroy()

    def body(self, master: tk.Widget) -> tk.Text:
        """
        Create the body of the build executable dialog.

        :param master: The parent widget.
        :return: The text widget that will display the build output.
        """
        msg = "cTOASTER executable needs to be rebuilt\n\nPlease wait..."
        lab = ttk.Label(master, text=msg, font=self.app.bold_font)
        lab.grid(column=0, row=0, pady=5, padx=5, sticky=tk.W + tk.N)

        self.out_frame = ttk.Frame(master)
        self.out = tk.Text(
            self.out_frame, width=80, font=self.app.mono_font, state=tk.DISABLED
        )
        self.out_scroll = ttk.Scrollbar(self.out_frame, command=self.out.yview)
        self.out.config(yscrollcommand=self.out_scroll.set)

        master.rowconfigure(1, weight=1)
        master.columnconfigure(0, weight=1)
        self.out_frame.grid(column=0, row=1, padx=5, pady=5, sticky="nesw")
        self.out.grid(row=0, column=0, sticky="nesw")
        self.out_scroll.grid(row=0, column=1, sticky="ns")

        self.start_build_process()
        return self.out

    def start_build_process(self) -> None:
        """
        Starts the build process for the executable and initializes tailing the build log.
        """
        self.message("STARTING EXECUTABLE BUILD")
        go = os.path.join(U.ctoaster_root, "tools", "go.py")
        cmd = [sys.executable, "-u", go, "build"]
        model_config = U.ModelConfig(
            "ship", self.dir
        )  # Assuming U.ModelConfig is defined elsewhere
        model_dir = model_config.directory()
        log = os.path.join(model_dir, "build.log")

        if os.path.exists(log):
            os.remove(log)

        # Redirect subprocess output to avoid blocking the GUI
        with open(os.devnull, "w") as sink:
            try:
                self.pipe = sp.Popen(cmd, cwd=self.dir, stdout=sink, stderr=sink)
                self.state = "RUNNING"
                self.tailer = Tailer(self.app, log)
                self.tailer.start(self.add_output)
            except Exception as e:
                self.message(f"FAILED TO START BUILD:\n\n{e}\n")
                self.state = "FAILED"

    def buttonbox(self) -> ttk.Frame:
        """
        Create the button box for the dialog, initially disabling the OK button.

        :return: The frame containing the buttons.
        """
        box = super().buttonbox()
        enable(
            self.ok_button, False
        )  # Assuming 'enable' is defined to toggle widget state.
        return box

    def message(self, s: str) -> None:
        """
        Display a message in the dialog's output text area.

        :param s: The message string to display.
        """
        self.out.config(state=tk.NORMAL)  # Enable text widget to modify its contents.
        separator = 79 * "*" + "\n"
        self.out.insert(tk.END, separator)
        self.out.insert(tk.END, "\n")
        self.out.insert(
            tk.END, f"    {s}\n"
        )  # Use f-string for cleaner string formatting.
        self.out.insert(tk.END, "\n")
        self.out.insert(tk.END, separator)
        self.out.insert(tk.END, "\n")
        self.out.config(
            state=tk.DISABLED
        )  # Disable text widget to prevent user modification.
        self.out.see(
            tk.END
        )  # Scroll to the end of the text widget to show the latest message.

    def add_output(self, t: str) -> None:
        """
        Adds output text to the dialog's output area and checks the build process's completion.

        :param t: Text to be added to the output area.
        """
        if self.pipe.poll() is not None:
            self.state = "COMPLETE"
            self.message("BUILD COMPLETE")
            enable(self.ok_button, True)  # Assuming 'enable' is defined elsewhere

        self.out.config(state=tk.NORMAL)
        at_end = self.out_scroll.get()[1] == 1.0
        self.out.insert(tk.END, t)
        self.out.config(state=tk.DISABLED)

        if at_end:
            self.out.see(tk.END)

    def validate(self) -> bool:
        """
        Validates whether the build process has completed or failed before closing the dialog.

        :return: True if the build is complete or failed, False otherwise.
        """
        if self.state not in ("COMPLETE", "FAILED"):
            tkMB.showwarning("Building", "Build is not yet complete!", parent=self)
            return False
        return True
