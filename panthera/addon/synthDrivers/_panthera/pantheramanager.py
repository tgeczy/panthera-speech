# -*- coding: utf-8 -*-
"""The speech data manager: what you have, and how to get the rest.

This is the answer to the only real complaint about how this project ships.
The engine cannot be included -- it is Apple's, and no amount of arguing about
whether Apple would notice changes who owns it -- so the friction had to go
somewhere else.  It went here: pick your disc image, and the add-on does the
rest.

**No Python install, no command line, no 7-Zip, and nothing downloaded.**  The
whole reader is `pantherahfs`, and it opens every Mac OS X installer from 10.4
to 10.7 directly.

The work runs on a worker thread and the dialog is only ever touched through
`wx.CallAfter`.  Extraction is file reads and zlib, both of which let go of the
GIL, so NVDA keeps speaking while it runs -- which matters more here than
usual, because the person running it may be doing so *in order* to get a voice
back.
"""
import os
import threading

import gui
import wx
from logHandler import log
import ui

import pantheradiscs

#: How much of the way through before saying so out loud.
#:
#: Every percent would be unusable and none would be worse.  Quarters, and the
#: label updates silently in between for anyone reading it with review keys.
ANNOUNCE_EVERY = 25


def _folder_for(key, generations):
    """-> where this generation's engine belongs, from the plugin's table."""
    for gen in generations:
        if gen["key"] == key:
            return gen["tree"].config_dir()
    return None


class SpeechDataDialog(wx.Dialog):
    """One dialog: what is installed, and a way to install more."""

    _instance = None

    @classmethod
    def show(cls, parent, generations, report_lines):
        """Only ever one, and bring it forward if it is already up."""
        if cls._instance is not None:
            try:
                cls._instance.Raise()
                return
            except RuntimeError:
                cls._instance = None
        dialog = cls(parent, generations, report_lines)
        cls._instance = dialog
        dialog.Show()

    def __init__(self, parent, generations, report_lines):
        # Translators: the title of the Mac OS X speech data dialog.
        super().__init__(parent, title=_("Mac OS X speech data"))
        self._generations = generations
        self._busy = False
        self._lastAnnounced = -1

        main = wx.BoxSizer(wx.VERTICAL)
        pad = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Translators: label for the list of speech engines and their state.
        label = wx.StaticText(pad, label=_("Speech &engines:"))
        sizer.Add(label, 0, wx.ALL, 4)
        self.list = wx.ListBox(pad, size=(560, 160), style=wx.LB_SINGLE)
        sizer.Add(self.list, 1, wx.EXPAND | wx.ALL, 4)

        # Translators: label for the box explaining the selected engine.
        sizer.Add(wx.StaticText(pad, label=_("&Details:")), 0, wx.ALL, 4)
        self.details = wx.TextCtrl(
            pad, size=(560, 150),
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP)
        sizer.Add(self.details, 1, wx.EXPAND | wx.ALL, 4)

        row = wx.BoxSizer(wx.HORIZONTAL)
        # Translators: a button that reads a Mac OS X disc image.
        self.addButton = wx.Button(pad, label=_("&Get speech data from a disc "
                                                "image..."))
        self.addButton.Bind(wx.EVT_BUTTON, self.onAdd)
        row.Add(self.addButton, 0, wx.ALL, 4)
        # Translators: a button that opens the folder the engines live in.
        self.openButton = wx.Button(pad, label=_("&Open the speech data "
                                                 "folder"))
        self.openButton.Bind(wx.EVT_BUTTON, self.onOpen)
        row.Add(self.openButton, 0, wx.ALL, 4)
        sizer.Add(row, 0, wx.ALL, 2)

        status = wx.BoxSizer(wx.HORIZONTAL)
        self.statusText = wx.StaticText(pad, label="")
        status.Add(self.statusText, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.gauge = wx.Gauge(pad, range=100, size=(180, -1))
        # Named, or a screen reader has nothing to call it.
        self.gauge.SetName(_("Progress"))
        self.gauge.Hide()
        status.Add(self.gauge, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(status, 0, wx.EXPAND | wx.ALL, 4)

        self.closeButton = wx.Button(pad, wx.ID_CLOSE)
        self.closeButton.Bind(wx.EVT_BUTTON, self.onClose)
        sizer.Add(self.closeButton, 0, wx.ALIGN_RIGHT | wx.ALL, 4)
        self.SetEscapeId(wx.ID_CLOSE)

        pad.SetSizer(sizer)
        main.Add(pad, 1, wx.EXPAND)
        self.SetSizerAndFit(main)
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.list.Bind(wx.EVT_LISTBOX, lambda evt: self._showDetails())

        self._report = report_lines
        self.refresh()
        self.list.SetFocus()

    # -- what is installed ------------------------------------------------

    def refresh(self):
        selected = self.list.GetSelection()
        self.list.Clear()
        self._rows = []
        for gen in self._generations:
            folder = gen["tree"].config_dir()
            voices = pantheradiscs.installed_voices(folder)
            ok, lines = gen["tree"].explain()
            if voices and ok:
                # Translators: {name} is an engine, {count} how many voices.
                state = _("{count} voices").format(count=len(voices))
            elif voices:
                # Translators: some files are there but it will not run.
                state = _("incomplete")
            else:
                # Translators: no speech data for this engine yet.
                state = _("not installed")
            self.list.Append("%s -- %s" % (gen["label"], state))
            self._rows.append((gen, folder, voices, lines))
        if self._rows:
            self.list.SetSelection(max(0, min(selected, len(self._rows) - 1)))
        self._showDetails()

    def _showDetails(self):
        index = self.list.GetSelection()
        if index < 0 or index >= len(self._rows):
            self.details.SetValue("")
            return
        gen, folder, voices, lines = self._rows[index]
        text = [gen["source"], "", "%s:" % folder]
        if voices:
            text.append("")
            # Translators: the voices found, listed.
            text.append(_("Voices: %s") % ", ".join(voices))
        text.append("")
        text.extend(lines)
        self.details.SetValue("\n".join(text))

    # -- reading a disc image ---------------------------------------------

    def onAdd(self, evt):
        if self._busy:
            return
        # Translators: the file picker for a Mac OS X disc image.
        with wx.FileDialog(
                self, _("Choose a Mac OS X install disc image"),
                wildcard=_("Disc images") + " (*.iso;*.dmg;*.cdr)|"
                                            "*.iso;*.dmg;*.cdr|"
                         + _("All files") + " (*.*)|*.*",
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as picker:
            if picker.ShowModal() != wx.ID_OK:
                return
            path = picker.GetPath()
        self._run(path)

    def _run(self, path):
        self._setBusy(True)
        # Translators: shown while the disc image is being identified.
        self._status(_("Examining the disc image..."), None, announce=True)

        def work():
            try:
                disc = pantheradiscs.identify(path)
            except Exception as error:            # never take NVDA with it
                log.error("Panthera: identifying %s" % path, exc_info=True)
                wx.CallAfter(self._failed, str(error))
                return
            wx.CallAfter(self._identified, disc)

        threading.Thread(target=work, name="panthera-identify",
                         daemon=True).start()

    def _identified(self, disc):
        if not disc.usable:
            self._setBusy(False)
            self._status("", None)
            gui.messageBox(
                disc.problem or _("This image cannot be used."),
                _("Mac OS X speech data"), wx.OK | wx.ICON_INFORMATION, self)
            return
        folder = _folder_for(disc.generation.key, self._generations)
        if folder is None:
            self._setBusy(False)
            self._status("", None)
            gui.messageBox(
                # Translators: recognised, but this add-on has no driver.
                _("This is %s, which this add-on cannot use yet.")
                % disc.label,
                _("Mac OS X speech data"), wx.OK | wx.ICON_INFORMATION, self)
            return
        existing = pantheradiscs.installed_voices(folder)
        # Translators: the confirmation before extracting. {label} is the
        # release, {folder} where it will be written.
        question = _(
            "This is {label}.\n\n"
            "The speech engine will be copied from it into:\n{folder}\n\n"
            "Nothing is sent anywhere and nothing is downloaded. This can "
            "take a few minutes and needs up to a gigabyte of free space.\n\n"
            "Continue?").format(label=disc.label, folder=folder)
        if existing:
            question += "\n\n" + _(
                "There are already %d voices there. Files with the same name "
                "will be replaced.") % len(existing)
        if gui.messageBox(question, _("Mac OS X speech data"),
                          wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            self._setBusy(False)
            self._status("", None)
            return
        self._extract(disc, folder)

    def _extract(self, disc, folder):
        self._lastAnnounced = -1
        self.gauge.Show()
        self.Layout()

        def report(percent, message):
            wx.CallAfter(self._status, message, percent)

        def work():
            try:
                counts = pantheradiscs.extract(disc, folder, progress=report)
            except Exception as error:
                log.error("Panthera: extracting %s" % disc.path, exc_info=True)
                wx.CallAfter(self._failed, str(error))
                return
            wx.CallAfter(self._done, disc, folder, counts)

        threading.Thread(target=work, name="panthera-extract",
                         daemon=True).start()

    # -- reporting back ----------------------------------------------------

    def _status(self, message, percent=None, announce=False):
        self.statusText.SetLabel(message)
        if percent is not None:
            self.gauge.SetValue(max(0, min(100, int(percent))))
            step = int(percent) // ANNOUNCE_EVERY
            if 0 < int(percent) < 100 and step > self._lastAnnounced:
                self._lastAnnounced = step
                announce = True
        self.Layout()
        if announce and message:
            ui.message(message if percent is None else
                       # Translators: spoken progress. {percent} is a number.
                       _("{percent} percent, {what}").format(
                           percent=int(percent), what=message))

    def _failed(self, message):
        self._setBusy(False)
        self.gauge.Hide()
        self._status("", None)
        self.Layout()
        gui.messageBox(
            # Translators: shown when reading the image went wrong.
            _("The speech data could not be read from that image.\n\n%s")
            % message, _("Mac OS X speech data"), wx.OK | wx.ICON_ERROR, self)

    def _done(self, disc, folder, counts):
        self._setBusy(False)
        self.gauge.Hide()
        self._status("", None)
        self.refresh()
        voices = pantheradiscs.installed_voices(folder)
        # Translators: the summary after a successful extraction.
        message = _(
            "{label} is installed.\n\n"
            "{files} files, {mb} MB.\n"
            "{count} voices: {voices}\n\n"
            "Choose {driver} in NVDA's synthesizer list to use it."
        ).format(label=disc.label, files=counts["files"],
                 mb=int(counts["bytes"] / 1e6), count=len(voices),
                 voices=", ".join(voices),
                 driver=disc.generation.label)
        if counts["skipped"]:
            message += "\n\n" + _(
                "%d file(s) were skipped because Windows cannot name them. "
                "None of them are needed.") % len(counts["skipped"])
        ui.message(_("Finished. %d voices.") % len(voices))
        gui.messageBox(message, _("Mac OS X speech data"),
                       wx.OK | wx.ICON_INFORMATION, self)

    def _setBusy(self, busy):
        self._busy = busy
        self.addButton.Enable(not busy)
        self.openButton.Enable(not busy)
        self.closeButton.Enable(not busy)

    # -- the rest ----------------------------------------------------------

    def onOpen(self, evt):
        index = self.list.GetSelection()
        if index < 0 or index >= len(self._rows):
            return
        folder = self._rows[index][1]
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except OSError:
            log.error("Panthera: could not open %s" % folder, exc_info=True)

    def onClose(self, evt):
        if self._busy:
            # Reading a disc image writes hundreds of megabytes; closing the
            # window mid-way would leave a half-written folder that looks
            # installed and is not.
            ui.message(_("Still reading the disc image."))
            return
        type(self)._instance = None
        self.Destroy()
