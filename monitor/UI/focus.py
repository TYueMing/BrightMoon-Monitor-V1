"""Focus queries that also support widgets created internally by Tcl/Tk."""
import tkinter as tk


def focus_path(widget):
    """Return the Tcl window path without resolving it to a Python widget.

    ttk.Combobox's popdown/listbox is created by Tcl, so focus_get() can raise
    KeyError in nametowidget(). Drawing only needs identity or ancestry, not
    the Python object. An empty path means no focus or a torn-down interpreter.
    """
    try:
        return str(widget.tk.call('focus'))
    except tk.TclError:
        return ''
