import os
import shutil
import tkinter as tk
from functools import cmp_to_key

from gui.job import *

import utils as U

# ----------------------------------------------------------------------
#
#  UTILITIES
#


def walk_jobs(p, basedir=None):
    """
    Iterator to walk through the hierarchy of job and normal folders under a given base job directory,
    yielding paths and information about whether entries are folders or individual jobs.

    :param p: The path to start walking from, as a string.
    :param basedir: The base directory for the job structure, as a string. If None, 'p' is used.
    """
    if basedir is None:
        basedir = p

    model_dir = os.path.join(basedir, "MODELS")

    for entry in os.listdir(p):
        full_path = os.path.join(p, entry)
        if full_path.startswith(model_dir):
            continue
        if os.path.exists(os.path.join(full_path, "config", "config")):
            yield (full_path, "JOB")
        elif os.path.isdir(full_path):
            if not os.listdir(full_path):  # Check if the folder is empty
                yield (full_path, "FOLDER")
            else:
                yield from walk_jobs(full_path, basedir)


def job_split(jfull: str):
    """
    Splits a full job path into directories and job name.

    :param jfull: The full path to the job.
    :return: A tuple containing a list of directories and the job name.
    """
    d, j = os.path.split(jfull)
    ds = []
    while d:
        d, d1 = os.path.split(d)
        if d1:  # Avoid appending empty strings for root directory
            ds.append(d1)
    ds.reverse()
    return ds, j


# Global variable to store the folder image, ensuring it's loaded only once.
folder_image = None


def folder_img() -> tk.PhotoImage:
    """
    Returns the icon image to use for folders in the job tree view.

    :return: A tkinter.PhotoImage object for the folder icon.
    """
    global folder_image
    if folder_image is None:
        folder_image_path = os.path.join(
            U.ctoaster_root, "tools", "images", "status-FOLDER.gif"
        )
        folder_image = tk.PhotoImage(file=folder_image_path)
    return folder_image


# ----------------------------------------------------------------------
#
#  MAIN JOB FOLDER CLASS
#

# Job folder management: abstraction for the folder hierarchy under
# the ctoaster-jobs directory.


class JobFolder:
    def __init__(self, path: str, name: str, tree, app) -> None:
        """
        Initializes a JobFolder instance.

        :param path: The base directory path for this job folder.
        :param name: The name used for the base directory in the job tree.
        :param tree: The job tree widget.
        :param app: The application object used for timers.
        """
        self.app = app
        self.base_path = path
        self.name = name
        self.tree = tree
        self.selected = None  # Initially, no item is selected in the job tree.

        # Initialize dictionaries to record job tree entries as folders or by their job status.
        self.folders = {path: 1}
        self.status = {}

        # Insert the root entry to the job tree and mark it as the current selection.
        self.selected = self.tree.insert(
            "", "end", self.base_path, text=self.name, open=True
        )

        # Walk through the hierarchy of jobs and folders under the base directory to populate the job tree.
        for p, entry_type in walk_jobs(self.base_path):
            if entry_type == "JOB":
                self.add_job(os.path.relpath(p, self.base_path))
            else:  # If it's a folder
                self.add_folder(os.path.relpath(p, self.base_path))

        # Sort children of the current selection in the job tree and update the selection.
        self.sort_children(self.selected)
        self.tree.selection_set(self.selected)

        # Schedule a timer to periodically update the status icons of jobs in the job tree.
        self.app.after(500, self.set_statuses)

    def possible_folders(self):
        """
        Returns a sorted list of folder paths in the job tree. These paths represent
        potential destinations for moving job entries.

        :return: A sorted list of folder paths.
        """
        # Retrieve the folder paths from the folders dictionary.
        folder_paths = list(self.folders.keys())
        # Sort the list of folder paths.
        folder_paths.sort()
        return folder_paths

    def add_job(self, jfull: str, sort: bool = False) -> None:
        """
        Adds a job to the job tree, including creating necessary folder entries.

        :param jfull: The full path of the job relative to the base job directory.
        :param sort: Whether to sort the job tree after adding the job.
        """
        # Determine the containing folders for the job.
        folders, job_name = job_split(jfull)

        # Recursively insert folder entries into the tree.
        parent_path = self.base_path
        for folder in folders:
            parent = parent_path
            parent_path = os.path.join(parent_path, folder)
            if not self.tree.exists(parent_path):
                self.folders[parent_path] = 1
                self.tree.insert(
                    parent, "end", parent_path, text=folder, image=folder_img()
                )

        # Create a job, determine its current status, and create an entry in the job tree.
        job_path = os.path.join(self.base_path, jfull)
        job = Job(job_path, self)
        self.status[jfull] = job.status
        self.tree.insert(
            parent_path, "end", job_path, text=job_name, image=job.status_img()
        )

        # Optionally sort the children of the selected node in the tree.
        if sort:
            self.sort_children(self.selected)

    def add_folder(self, ffull: str, sort: bool = False) -> None:
        """
        Adds a folder to the job tree, including creating necessary parent folder entries.

        :param ffull: The full path of the folder relative to the base job directory.
        :param sort: Whether to sort the job tree after adding the folder.
        """
        # Use a placeholder "DUMMY" to facilitate splitting the path into directories and the folder name.
        folders, folder_name = job_split(os.path.join(ffull, "DUMMY"))

        parent_path = self.base_path
        for folder in folders:
            parent = parent_path
            parent_path = os.path.join(parent_path, folder)
            if not self.tree.exists(parent_path):
                self.folders[parent_path] = 1
                self.tree.insert(
                    parent, "end", parent_path, text=folder, image=folder_img()
                )

        # Optionally sort the children of the selected node in the tree.
        if sort:
            self.sort_children(self.selected)

    def is_folder(self, p: str) -> bool:
        """
        Check if a given entry is recognized as a folder.

        :param p: The path to check.
        :return: True if the entry is a folder, False otherwise.
        """
        return p in self.folders

    def delete(self, p: str) -> None:
        """
        Delete an entry from the job tree. If the entry is a folder,
        also delete any child folders and their associated status.

        :param p: The path of the entry to delete.
        """
        # Delete the entry from the tree view.
        self.tree.delete(p)

        # For folders, delete any child folders from the folders dictionary.
        if self.is_folder(p):
            child_folders = [f for f in self.folders if f.startswith(p)]
            for child in child_folders:
                del self.folders[child]

        # Delete the entry's status, if it exists.
        self.status.pop(p, None)

    def move(self, fr: str, to: str) -> None:
        """
        Move an entry in the job tree and on disk.

        :param fr: The original path of the entry.
        :param to: The new path for the entry.
        """
        # Move on disk.
        shutil.move(fr, to)
        is_folder = fr in self.folders
        to_relative = os.path.relpath(to, self.base_path)

        # Move in the tree by deleting and re-adding.
        self.delete(fr)
        if is_folder:
            self.add_folder(to_relative, sort=True)
        else:
            self.add_job(to_relative, sort=True)

    def clone(self, fr: str, to: str) -> None:
        """
        Clone a job entry in the job tree and on disk.

        :param fr: The source path of the job.
        :param to: The destination path where the job will be cloned.
        """
        # Clone on disk.
        shutil.copytree(fr, to)

        # Clone in the tree.
        to_relative = os.path.relpath(to, self.base_path)
        self.add_job(to_relative, sort=True)

    def sort_children(self, f):
        """
        Sort the children of a given entry in the job tree. Folders sort before jobs,
        and otherwise, entries sort alphabetically.
        """

        # Key function for sorting based on folder/job determination and name.
        def sort_key(x):
            return (not self.is_folder(x), x)

        # Get children of the required entry.
        children = list(self.tree.get_children(f))
        # Sort children using the key function.
        sorted_children = sorted(children, key=sort_key)

        # Correctly move children within the Treeview.
        for i, child in enumerate(sorted_children):
            # Moving each child to the "end" effectively reorders them.
            self.tree.move(child, f, i)

        # Recursively sort descendants.
        for child in sorted_children:
            self.sort_children(child)

    def set_statuses(self):
        """
        Timer-driven routine to maintain job status icons in the job tree.

        This method checks each job's current status against the stored status
        and updates the icon in the tree if necessary. It schedules itself to
        run again after a delay, ensuring that job statuses are kept up to date.
        """
        # Track paths that no longer exist or whose status has changed.
        dels = []

        # Iterate over current status entries to check for updates.
        for p, s in list(self.status.items()):
            pfull = os.path.join(self.base_path, p)
            schk = job_status(pfull)

            if not schk:
                # Record non-existent items for deletion.
                dels.append(p)
            elif schk != s:
                # Update the status and icon for changed items.
                self.status[p] = schk
                self.tree.item(pfull, image=job_status_img(schk))

        # Remove entries for non-existent items.
        for p in dels:
            del self.status[p]

        # Schedule the next status update.
        self.app.after(500, self.set_statuses)

    def find_restart_jobs(self):
        """
        Find all jobs that are suitable for use as restart jobs, i.e., all completed jobs.

        Returns:
            A sorted list of job paths that have been completed, with a "<None>"
            entry to represent the option of not using a restart job.
        """
        # Use list comprehension for a more concise and Pythonic approach
        # to filter completed jobs and sort them.
        completed_jobs = [p for p, v in self.status.items() if v == "COMPLETE"]

        # Add a "<None>" entry for the case where no restart is used and sort the list.
        completed_jobs.append("<None>")
        completed_jobs.sort()

        return completed_jobs
