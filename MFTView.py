#!/bin/python

#    This file is part of INDXParse.
#
#   Copyright 2011 Will Ballenthin <william.ballenthin@mandiant.com>
#                    while at Mandiant <http://www.mandiant.com>
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
#   Version v.1.2.0
from MFT import *
import wx
import wx.lib.mvctree as tree
import argparse

verbose = False

def nop(*args, **kwargs):
    pass

def _expand_into(dest, src):
    vbox = wx.BoxSizer(wx.VERTICAL)
    vbox.Add(src, 1, wx.EXPAND | wx.ALL)
    dest.SetSizer(vbox)

class Node():
    def __init__(self, number, name, parent, is_directory):
        self._number = number
        self._name   = name
        self._parent = parent
        self.children = [] # public
        self.is_directory = is_directory

    def add_child(self, child):
        self.children.append(child)

    def get_name(self):
        return self._name

class MFTModel():
    def __init__(self, filename):
        self._filename = filename
        self._nodes = {}
        self._orphans = []

    def get_root(self):
        return self.get_node(5)

    def get_node(self, rec_num):
        if len(self._nodes) == 0:
            self.fetch()
        return self._nodes[rec_num]

    def fetch(self, progress_fn=nop):
        """
        @param progress_fn - A function(count, total) called periodically
        """
        if len(self._nodes) > 0:
            return

        total_count = 0
        with open(self._filename, "rb") as f:
            f.seek(0, 2) # end
            total_count = f.tell() / 1024
            f.seek(0)

        f = NTFSFile({
            "filename": self._filename,
            "filetype": "mft",
            "offset": 0,
            "clustersize": 4096,
            "prefix": "C:",
            "progress": False
        })

        class RecordConflict(Exception):
            def __init__(self, count):
                self.value = count

        def add_node(mftfile, record):
            """
            Depends on the closure of `nodes` and `orphans`
            @raises RecordConflict if the record already exists in nodes
            """
            rec_num = record.mft_record_number() & 0xFFFFFFFFFFFF
            
            # node already exists by rec_num
            if rec_num in self._nodes:
                raise RecordConflict(rec_num)

            # no filename info --> orphan with name "???"
            fn = record.filename_information()
            if not fn:
                node = Node(rec_num, "???", None, record.is_directory())
                self._orphans.append(node)
                self._nodes[rec_num] = node
                return node

            # one level cycle
            parent_record_num = fn.mft_parent_reference() & 0xFFFFFFFFFFFF
            if parent_record_num == rec_num:
                node = Node(rec_num, fn.filename(), None, record.is_directory())
                self._orphans.append(node)
                self._nodes[rec_num] = node
                return node

            if parent_record_num not in self._nodes:
                # no parent --> orphan with correct filename
                parent_buf = mftfile.mft_get_record_buf(parent_record_num)
                if parent_buf == array.array("B", ""):
                    node = Node(rec_num, fn.filename(), None, record.is_directory())
                    self._orphans.append(node)
                    self._nodes[rec_num] = node
                    return node

                # parent sequence num incorrect --> orphan with correct filename
                parent = MFTRecord(parent_buf, 0, False)
                if parent.sequence_number() != fn.mft_parent_reference() >> 48:
                    node = Node(rec_num, fn.filename(), None, record.is_directory())
                    self._orphans.append(node)
                    self._nodes[rec_num] = node
                    return node

                add_node(mftfile, parent)

            parent_node = self._nodes[parent_record_num]
            node = Node(rec_num, fn.filename(), parent_node, record.is_directory())
            self._nodes[rec_num] = node
            parent_node.add_child(node)
            return node

        count = 0
        for record in f.record_generator():
            count += 1
            try:
                add_node(f, record)
            except RecordConflict as e:
                print "Record conflict with record number %s" % (e.value)
            if count % 100 == 0:  progress_fn(count, total_count)    
            
class MFTTreeCtrl(wx.TreeCtrl):
    def __init__(self, *args, **kwargs):
        self._model = kwargs.get("model", None)
        del kwargs["model"]
        super(MFTTreeCtrl, self).__init__(*args, **kwargs)
        self.Bind(wx.EVT_TREE_ITEM_EXPANDING, self.OnExpandKey)

        self.il = wx.ImageList(16,16)
        self._folder_icon = self.il.Add(wx.ArtProvider.GetBitmap(wx.ART_FOLDER, wx.ART_OTHER, (16,16)))
        self._file_icon = self.il.Add(wx.ArtProvider.GetBitmap(wx.ART_NORMAL_FILE, wx.ART_OTHER, (16,16)))
        self.SetImageList(self.il)

        root = self._model.get_root()
        root_item = self.AddRoot(root.get_name(), self._folder_icon)
        self.SetPyData(root_item, {
            "rec_num": root._number,
            "has_expanded": False
        })
        if len(root.children) > 0:
            self.SetItemHasChildren(root_item)


    def _extend(self, item):
        if self.GetPyData(item)["has_expanded"]:
            return

        rec_num = self.GetPyData(item)["rec_num"]
        node = self._model.get_node(rec_num)
        for child_node in node.children:
            child_item = self.AppendItem(item, child_node.get_name())
            if child_node.is_directory:
                self.SetItemImage(child_item, self._folder_icon)
            else:
                self.SetItemImage(child_item, self._file_icon)
            self.SetPyData(child_item, {
                "rec_num": child_node._number,
                "has_expanded": False,
            })
            if len(child_node.children) > 0:
                self.SetItemHasChildren(child_item)
        self.GetPyData(item)["has_expanded"] = True

    def OnExpandKey(self, event):
        item = event.GetItem()
        if not item.IsOk():
            item = self.GetSelection()
        if not self.GetPyData(item)["has_expanded"]:
            self._extend(item)

class MFTRecordView(wx.Panel):
    def __init__(self, *args, **kwargs):
        self._model = kwargs.get("model", None)
        del kwargs["model"]
        super(MFTRecordView, self).__init__(*args, **kwargs)

        self._sizer = wx.BoxSizer(wx.VERTICAL)
        self.SetSizer(self._sizer)

    def _format_hex(self, data):
        """
        see http://code.activestate.com/recipes/142812/
        """
        FILTER=''.join([(len(repr(chr(x)))==3) and chr(x) or '.' for x in range(256)])

        def dump(src, length=16):
            N=0; result=''
            while src:
                s,src = src[:length],src[length:]
                hexa = ' '.join(["%02X"%ord(x) for x in s])
                s = s.translate(FILTER)
                result += "%04X   %-*s   %s\n" % (N, length*3, hexa, s)
                N+=length
            return result
        return dump(data)

    def display_value(self, value):
        self._sizer.Clear()
        view = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        view.SetValue(unicode(value))
        self._sizer.Add(view, 1, wx.EXPAND)
        self._sizer.Layout()

    def display_record(self, record):
        self._sizer.Clear()
        view = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        font = wx.Font(8, wx.SWISS, wx.NORMAL, wx.NORMAL, False, u'Courier')
        view.SetFont(font)
        view.SetValue(unicode(self._format_hex(record._buf.tostring())))
        self._sizer.Add(view, 1, wx.EXPAND)
        self._sizer.Layout()

    def clear_value(self):
        self._sizer.Clear()
        self._sizer.Add(wx.Panel(self, -1), 1, wx.EXPAND)
        self._sizer.Layout()

class MFTFileView(wx.Panel):
    def __init__(self, parent, filename):
        super(MFTFileView, self).__init__(parent, -1, size=(800, 600))
        self._filename = filename
        self._model = MFTModel(filename)

        vsplitter = wx.SplitterWindow(self, -1)

        panel_left = wx.Panel(vsplitter, -1)
        self._tree = MFTTreeCtrl(panel_left, -1, model=self._model)
        _expand_into(panel_left, self._tree)

        panel_right = wx.Panel(vsplitter, -1)
        self._recordview = MFTRecordView(panel_right, -1, model=self._model)
        _expand_into(panel_right, self._recordview)

        vsplitter.SplitVertically(panel_left, panel_right)
        vsplitter.SetSashPosition(325, True)
        _expand_into(self, vsplitter)
        self.Centre()

        self._tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.OnFileSelected)

    def OnFileSelected(self, event):
        item = event.GetItem()
        if not item.IsOk():
            item = self._tree.GetSelection()
        rec_num = self._tree.GetPyData(item)["rec_num"]

        f = NTFSFile({
            "filename": self._filename,
            "filetype": "mft",
            "offset": 0,
            "clustersize": 4096,
            "prefix": "C:",
            "progress": False
        })

        try:
            record = f.mft_get_record(rec_num)
        except InvalidMFTRecordNumber as e:
            sys.stderr.write("Unable to open MFT record %d\n" % (e.value))
            return

        self._recordview.display_record(record)

class MFTFileViewer(wx.Frame):
    def __init__(self, parent, filename):
        super(MFTFileViewer, self).__init__(parent, -1, "MFT File Viewer", size=(800, 600))
        self.CreateStatusBar()

        menu_bar = wx.MenuBar()
        file_menu = wx.Menu()
        menu_bar.Append(file_menu, "&File")

        p = wx.Panel(self)
        self._nb = wx.Notebook(p)
        
        view = MFTFileView(self._nb, filename)
        self._nb.AddPage(view, filename)

        _expand_into(p, self._nb)
        self.Layout()

import threading
def start(func, *args):
    thread = threading.Thread(target=func, args=args)
    thread.setDaemon(True)
    thread.start()

def test(dialog):
    def foo(count, total):
        update_str = "%d / %d\n%0.2f%% Complete\n" % \
                     ( count, total, 100 * count / float(total))
        wx.CallAfter(dialog.Update, 
                     100 * count / float(total), update_str)
    m = MFTModel(sys.argv[1])
    m.fetch(progress_fn=foo)
    wx.CallAfter(dialog.Destroy)

if __name__ == "__main__":
    app = wx.App(False)
    filename = sys.argv[1]
    frame = MFTFileViewer(None, filename)
    frame.Show()
    app.MainLoop()
#    app = wx.PySimpleApp()
#    dialog = wx.ProgressDialog('a', 'b', maximum=100.0)
#    start(test, dialog)
#    dialog.ShowModal()
#    app.MainLoop()

