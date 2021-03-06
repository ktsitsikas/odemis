# -*- coding: utf-8 -*-
"""
:created: 2016-28-01
:author: Kimon Tsitsikas
:copyright: © 2015-2016 Kimon Tsitsikas, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division

import logging
from odemis import model
from odemis.dataio import get_converter
from odemis.gui.comp import popup
from odemis.gui.util import formats_to_wildcards
from odemis.gui.util import get_picture_folder, call_in_wx_main
from odemis.gui.util.img import ar_to_export_data, spectrum_to_export_data, images_to_export_data, line_to_export_data
import os
import time
import wx


PR_PREFIX = "Print-ready"
PP_PREFIX = "Post-processing"

# Dict str -> (tuple of str, tuple of str):
#   export type -> possible exporters for PR, possible exporters for PP
EXPORTERS = {"spatial": (("PNG", "TIFF"), ("Serialized TIFF",)),
             "AR": (("PNG", "TIFF"), ("CSV",)),
             "spectrum": (("PNG", "TIFF"), ("CSV",)),
             "spectrum-line": (("PNG", "TIFF"), ("CSV",))}


class ExportController(object):
    """
    Manages the export of the data displayed in the focused view of a tab to
    an easy for post-process format.
    """

    def __init__(self, tab_data, main_frame, tab_panel, viewports):
        """
        tab_data: MicroscopyGUIData -- the representation of the microscope GUI
        main_frame: (wx.Frame): the whole GUI frame
        """

        self._data_model = tab_data
        self._main_data_model = tab_data.main
        self._main_frame = main_frame
        self._tab_panel = tab_panel

        # Listen to "acquire image" button
        self._tab_panel.btn_secom_export.Bind(wx.EVT_BUTTON, self.start_export_as_viewport)

        self._viewports = viewports.keys()

        wx.EVT_MENU(self._main_frame,
                    self._main_frame.menu_item_export_as.GetId(),
                    self.start_export_as_viewport)
        self._main_frame.menu_item_export_as.Enable(False)

        # subscribe to get notified about tab changes
        self._main_data_model.tab.subscribe(self.on_tab_change, init=True)

    def on_tab_change(self, tab):
        """ Subscribe to the the current tab """
        if tab is not None and tab.name == 'analysis':
            # Only let export item to be enabled in AnalysisTab
            tab.tab_data_model.streams.subscribe(self.on_streams_change, init=True)
        else:
            self._main_frame.menu_item_export_as.Enable(False)
            self._tab_panel.btn_secom_export.Enable(False)

    def on_streams_change(self, streams):
        """ Enable Export menu item iff the tab has at least one stream """

        enabled = (len(streams) > 0)
        self._main_frame.menu_item_export_as.Enable(enabled)
        self._tab_panel.btn_secom_export.Enable(enabled)

    def start_export_as_viewport(self, event):
        """ Wrapper to run export_viewport in a separate thread."""
        filepath, export_format, export_type = self._get_export_info()
        if filepath is not None:
            # TODO: run the real export function in a future, and based on the
            # result, show a message box or popup or log message
            self.export_viewport(filepath, export_format, export_type)

    def _get_export_info(self):
        """
        Return str, str, str: full filename, exporter name, export type
          Full filename is None if cancelled by user
        """
        # Set default to the first of the list
        export_type = self.get_export_type(self._data_model.focussedView.value)
        formats = EXPORTERS[export_type]
        default_exporter = get_converter(formats[0][0])
        extension = default_exporter.EXTENSIONS[0]
        # TODO: default to the same path/filename as current file (but different extension)
        basename = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        filepath = os.path.join(get_picture_folder(), basename + extension)
        # filepath will be None if cancelled by user
        filepath, export_format, export_type = self.ShowExportFileDialog(filepath, default_exporter)
        # get rid of the prefix before you ask for the exporter
        if any(prefix in export_format.split(' ') for prefix in (PR_PREFIX, PP_PREFIX)):
            export_format = export_format.split(' ', 1)[1]

        return filepath, export_format, export_type

    @call_in_wx_main
    def export_viewport(self, filepath, export_format, export_type):
        """ Export the image from the focused view to the filesystem.

        :param filepath: (str) full path to the destination file
        :param export_format: (str) the format name
        :param export_type: (str) spatial, AR, spectrum or spectrum-line
        """
        try:
            exporter = get_converter(export_format)
            raw = export_format in EXPORTERS[export_type][1]
            exported_data = self.export(export_type, raw)
            # record everything to a file
            exporter.export(filepath, exported_data)

            popup.show_message(self._main_frame,
                               "Exported in %s" % (filepath,),
                               timeout=3
                               )

            logging.info("Exported a %s view into file '%s'.", export_type, filepath)
        except LookupError as ex:
            logging.info("Export of a %s view as %s seems to contain no data.",
                         export_type, export_format, exc_info=True)
            dlg = wx.MessageDialog(self._main_frame,
                                   "Failed to export: %s\n"
                                   "Please make sure that at least one stream is visible in the current view." % (ex,),
                                   "No data to export",
                                   wx.OK | wx.ICON_WARNING)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception:
            logging.exception("Failed to export a %s view as %s", export_type, export_format)

    def get_export_type(self, view):
        """
        Based on the given view gives the corresponding export type
        return (string): spatial, AR, spectrum or spectrum-line
        """
        view_name = view.name.value
        # TODO: just use another dict
        if view_name == 'Angle-resolved':
            export_type = 'AR'
        elif view_name == 'Spectrum plot':
            export_type = 'spectrum'
        elif view_name == 'Spatial spectrum':
            export_type = 'spectrum-line'
        else:
            export_type = 'spatial'
        return export_type

    def export(self, export_type, raw=False):
        """
        Returns the data to be exported with respect to the settings and options.

        :param export_type (string): spatial, AR, spectrum or spectrum-line
        :param raw (boolean): raw data format if True
        :param interpolate_data (boolean): apply interpolation on data if True

        returns DataArray or list of DataArray: the data to be exported, either
          an RGB image or raw data.
        raises:
            LookupError: if no data found to export
        """
        fview = self._data_model.focussedView.value
        vp = self.get_viewport_by_view(fview)
        streams = fview.getStreams()
        if export_type == 'AR':
            # TODO: set MD_ACQ_TYPE in stream code
            for i, _ in enumerate(streams):
                for r in streams[i].raw:
                    r.metadata[model.MD_ACQ_TYPE] = model.MD_AT_AR
            exported_data = ar_to_export_data(streams, raw)
        elif export_type == 'spectrum':
            spectrum = vp.stream.get_pixel_spectrum()
            spectrum_range, unit = vp.stream.get_spectrum_range()
            spectrum.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
            exported_data = spectrum_to_export_data(spectrum, raw, unit, spectrum_range)
        elif export_type == 'spectrum-line':
            spectrum_md = vp.stream.get_pixel_spectrum().metadata
            spectrum = vp.stream.get_line_spectrum()
            # copy only non existed values (we mainly care about MD_WL_*)
            spectrum.metadata.update({k: v for k, v in spectrum_md.iteritems() if k not in spectrum.metadata})
            spectrum_range, unit = vp.stream.get_spectrum_range()
            spectrum.metadata[model.MD_ACQ_TYPE] = model.MD_AT_SPECTRUM
            exported_data = line_to_export_data(spectrum, raw, unit, spectrum_range)
        else:
            export_type = 'spatial'
            view_px = tuple(vp.canvas.ClientSize)
            view_mpp = fview.mpp.value
            view_hfw = (view_mpp * view_px[0], view_mpp * view_px[1])
            view_pos = fview.view_pos.value
            draw_merge_ratio = fview.stream_tree.kwargs.get("merge", 0.5)
            interpolate_data = fview.interpolate_content.value
            exported_data = images_to_export_data(streams,
                                                  view_hfw, view_pos,
                                                  draw_merge_ratio, raw,
                                                  interpolate_data=interpolate_data,
                                                  logo=self._main_frame.legend_logo)

        return exported_data

    def get_viewport_by_view(self, view):
        """ Return the ViewPort associated with the given view """

        for vp in self._viewports:
            if vp.microscope_view == view:
                return vp
        raise IndexError("No ViewPort found for view %s" % view)

    def ShowExportFileDialog(self, filename, default_exporter):
        """
        filename (string): full filename to propose by default
        default_exporter (module): default exporter to be used
        return (string or None): the new filename (or the None if the user cancelled)
                (string): the format name
                (string): spatial, AR, spectrum or spectrum-line
        """
        # Find the available formats (and corresponding extensions) according
        # to the export type
        export_type = self.get_export_type(self._data_model.focussedView.value)
        formats_to_ext = self.get_export_formats(export_type)

        # current filename
        path, base = os.path.split(filename)
        wildcards, formats = formats_to_wildcards(formats_to_ext, suffix="")
        dialog = wx.FileDialog(self._main_frame,
                               message="Choose a filename and destination",
                               defaultDir=path,
                               defaultFile="",
                               style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                               wildcard=wildcards)

        # just default to the first format in EXPORTS[export_type]
        default_fmt = default_exporter.FORMAT
        try:
            idx = formats.index(default_fmt)
        except ValueError:
            idx = 0
        dialog.SetFilterIndex(idx)

        # Strip the extension, so that if the user changes the file format,
        # it will not have 2 extensions in a row.
        if base.endswith(default_exporter.EXTENSIONS[0]):
            base = base[:-len(default_exporter.EXTENSIONS[0])]
        dialog.SetFilename(base)

        # Show the dialog and check whether is was accepted or cancelled
        if dialog.ShowModal() != wx.ID_OK:
            return None, default_fmt, export_type

        # New location and name have been selected...
        # Store the path
        path = dialog.GetDirectory()

        # Store the format
        fmt = formats[dialog.GetFilterIndex()]

        # Check the filename has a good extension, or add the default one
        fn = dialog.GetFilename()
        ext = None
        for extension in formats_to_ext[fmt]:
            if fn.endswith(extension) and len(extension) > len(ext or ""):
                ext = extension

        if ext is None:
            if fmt == default_fmt and default_exporter.EXTENSIONS[0] in formats_to_ext[fmt]:
                # if the format is the same (and extension is compatible): keep
                # the extension. This avoid changing the extension if it's not
                # the default one.
                ext = default_exporter.EXTENSIONS[0]
            else:
                ext = formats_to_ext[fmt][0]  # default extension
            fn += ext

        return os.path.join(path, fn), fmt, export_type

    def get_export_formats(self, export_type):
        """
        Find the available file formats for the given export_type
        export_type (string): spatial, AR, spectrum or spectrum-line
        return (dict string -> list of strings): name of each format -> list of
            extensions.
        """
        pr_formats, pp_formats = EXPORTERS[export_type]

        export_formats = {}
        # Look dynamically which format is available
        # First the print-ready formats
        for format_data in pr_formats:
            exporter = get_converter(format_data)
            export_formats[PR_PREFIX + " " + exporter.FORMAT] = exporter.EXTENSIONS
        # Now for post-processing formats
        for format_data in pp_formats:
            exporter = get_converter(format_data)
            export_formats[PP_PREFIX + " " + exporter.FORMAT] = exporter.EXTENSIONS

        if not export_formats:
            logging.error("No file converter found!")

        return export_formats
