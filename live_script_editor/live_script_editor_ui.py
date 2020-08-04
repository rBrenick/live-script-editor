import builtins
import code
import datetime
import logging
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout, redirect_stderr
from typing import Callable

from Qt import QtCore, QtWidgets, QtGui

from live_script_editor import python_syntax_highlight

logging.basicConfig(level=logging.INFO)
log = logging.Logger(__name__)

key_list = QtCore.Qt


class LocalConstants:
    obj_name_re = re.compile(r"[\w]+\.", re.IGNORECASE)

    tool_qt_stylesheet = ""
    stylesheet_path = os.path.join(os.path.dirname(__file__), "stylesheets", "darkblue.stylesheet")
    if os.path.exists(stylesheet_path):
        with open(stylesheet_path, "r") as fh:
            tool_qt_stylesheet = fh.read()

            # setting keys


lk = LocalConstants


# orig_displayhook = sys.displayhook
# sys.displayhook = lambda x: exec(['_=x; pprint(x)','pass'][x is None])

class ScriptEditorSettings(QtCore.QSettings):
    k_window_layout = "window/layout"
    k_folder_path = "script_tree/folder_path"

    def __init__(self):
        super(ScriptEditorSettings, self).__init__(
            QtCore.QSettings.IniFormat,
            QtCore.QSettings.UserScope,
            'LiveScriptEditor',
            'live_script_editor'  # saves in C:\Users\Richa\AppData\Roaming\LiveScriptEditor
        )


class ScriptConsoleOutputUI(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super(ScriptConsoleOutputUI, self).__init__(parent=parent)
        self.setReadOnly(True)
        self.setWordWrapMode(QtGui.QTextOption.NoWrap)
        self.setStyleSheet("background-color: #242424;")

        self.input_format = self.currentCharFormat()

        self.output_format = QtGui.QTextCharFormat(self.input_format)
        self.output_format.setForeground(QtGui.QBrush(QtGui.QColor(160, 180, 255)))

        self.error_format = QtGui.QTextCharFormat(self.input_format)
        self.error_format.setForeground(QtGui.QBrush(QtGui.QColor(255, 100, 100)))

    def write(self, line):
        # overloaded stdout function
        self.write_line_to_output(line, self.output_format)

    def write_input(self, line):
        self.write_line_to_output(line, self.input_format)

    def write_error(self, line):
        self.write_line_to_output(line, self.error_format)

    def write_line_to_output(self, line, fmt=None):
        if fmt is not None:
            self.setCurrentCharFormat(fmt)

        if len(line) != 1 or ord(line[0]) != 10:  # ordinal 10 is Line feed or '\n'
            self.appendPlainText(line.rstrip())
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class PythonObjectCompleter(QtWidgets.QCompleter):
    insert_text = QtCore.Signal(str)

    def __init__(self, parent=None):
        super(PythonObjectCompleter, self).__init__(["yeahh", "boiiii"], parent)  # if this shows up, that means trouble

        self.popup().setStyleSheet(lk.tool_qt_stylesheet)

        self.last_selected = None
        self.setCompletionMode(QtWidgets.QCompleter.UnfilteredPopupCompletion)
        self.highlighted.connect(self.set_highlighted)

        self.item_model = QtCore.QStringListModel()

        self.filter_text = ""

        # This was useful https://stackoverflow.com/a/4829759
        self.filter_model = QtCore.QSortFilterProxyModel(self)
        self.filter_model.setSourceModel(self.item_model)
        self.filter_model.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.filter_model.setFilterFixedString(self.filter_text)
        self.setModel(self.filter_model)

    def set_highlighted(self, text):
        self.last_selected = text

    def get_selected(self):
        return self.last_selected

    def complete_text(self, text):
        """
        Analyse a string and get the dir() of the python object (so we can fill autocomplete list)

        :param text:
        :return:
        """
        found_obj_names = lk.obj_name_re.findall(text)  # strip ( and find everything after special characters
        if not found_obj_names:
            log.warning("regex failed on: {}".format(text))
            return

        selected_obj_full_name = "".join(found_obj_names)

        dot_split = selected_obj_full_name.split(".")

        selected_obj_base = dot_split[0]  # get first part before the .

        selected_obj = globals().get(selected_obj_base)  # get python obj from globals()

        if selected_obj:
            if selected_obj_full_name.endswith("."):
                selected_obj_full_name = selected_obj_full_name.rstrip(".")

            completion_list = dir(eval(selected_obj_full_name))  # this doesn't feel very safe

            self.item_model.setStringList(completion_list)

    def set_filter(self, filter_text):
        self.filter_model.setFilterFixedString(filter_text)
        self.filter_text = filter_text

    def reset_completion_list(self):
        completion_list = []
        completion_list.extend(globals())
        completion_list.extend(dir(builtins))
        self.item_model.setStringList(completion_list)


class LineNumberArea(QtWidgets.QWidget):
    """
    line number implementation modified from
    https://stackoverflow.com/a/40389314
    """

    def __init__(self, editor):
        super().__init__(editor)
        self.my_editor = editor

    def sizeHint(self):
        return QtCore.QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self.my_editor.line_number_area_paint_event(event)


class PythonScriptTextEdit(QtWidgets.QPlainTextEdit):
    def __init__(self, file_path="", parent=None):
        super(PythonScriptTextEdit, self).__init__(parent)

        self.dock_widget = None  # type: QtWidgets.QDockWidget
        self.script_file_path = file_path
        self.script_name = os.path.basename(file_path) if file_path else "UNDEFINED"

        self.completer = PythonObjectCompleter()
        self.completer.setWidget(self)
        self.completer.setMaxVisibleItems(20)
        self.completer.insert_text.connect(self.insert_completion)

        self.line_number_area = LineNumberArea(self)

        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self.highlight_current_line)
        self.textChanged.connect(self.mark_unsaved_changes)

        self.update_line_number_area_width(0)

    # -------------------------------------------
    # Functionality
    def save_script(self, save_as=False, start_dir=None):

        file_path = self.script_file_path
        if save_as or not file_path:
            file_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, dir=start_dir, filter="*.py")
            if not file_path:
                return

        with open(file_path, "w") as fp:
            fp.write(self.toPlainText())

        self.set_active_script_path(file_path)
        return True

    def load_script(self, file_path=None, open_dialog=False, start_dir=None):
        if file_path is None or open_dialog:
            file_path = self.script_file_path
            if file_path is None or open_dialog:
                file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, dir=start_dir, filter="*.py")
                if not file_path:
                    return

        with open(file_path, "r") as fp:
            self.setPlainText(fp.read())

        self.set_active_script_path(file_path)
        return True

    def set_active_script_path(self, file_path):
        script_name = os.path.basename(file_path)
        self.set_dock_tab_name(script_name)
        self.script_file_path = file_path
        self.script_name = script_name

    def mark_unsaved_changes(self):
        tab_name = self.dock_widget.windowTitle()
        if not tab_name.endswith("*"):
            tab_name = "{}*".format(tab_name)
        self.set_dock_tab_name(tab_name)

    def set_dock_tab_name(self, name):
        self.dock_widget.setWindowTitle(name)

    # -------------------------------------------------------------------
    # UI things
    def line_number_area_width(self):
        digits = 1
        count = max(1, self.blockCount())
        while count >= 10:
            count /= 10
            digits += 1
        space = 3 + self.fontMetrics().width('9') * digits
        return space

    def update_line_number_area_width(self, _):
        self.setViewportMargins(self.line_number_area_width() + 10, 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())

        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event):
        super(PythonScriptTextEdit, self).resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(
            QtCore.QRect(cr.left(), cr.top(), self.line_number_area_width() + 10, cr.height()))

    def line_number_area_paint_event(self, event):
        my_painter = QtGui.QPainter(self.line_number_area)
        my_painter.fillRect(event.rect(), QtGui.QColor("#262626"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()

        # Just to make sure I use the right font
        height = self.fontMetrics().height()
        while block.isValid() and (top <= event.rect().bottom()):
            if block.isVisible() and (bottom >= event.rect().top()):
                number = str(block_number + 1)
                my_painter.setPen(QtCore.Qt.lightGray)
                my_painter.drawText(-7, top, self.line_number_area.width(), height, QtCore.Qt.AlignRight, number)

            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            block_number += 1

    def highlight_current_line(self):
        extra_selections = []

        if not self.isReadOnly():
            selection = QtWidgets.QTextEdit.ExtraSelection()

            line_color = QtGui.QColor()
            line_color.setAlpha(30)

            selection.format.setBackground(line_color)
            selection.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            selection.cursor = self.textCursor()
            selection.cursor.clearSelection()
            extra_selections.append(selection)
        self.setExtraSelections(extra_selections)

    def insert_completion(self, completion):
        tc = self.textCursor()

        if len(self.completer.filter_text):
            tc.movePosition(QtGui.QTextCursor.PreviousWord)
            tc.movePosition(QtGui.QTextCursor.EndOfWord, QtGui.QTextCursor.KeepAnchor)  # replace characters after .

        tc.insertText(completion)
        self.setTextCursor(tc)
        self.completer.popup().hide()

    def focusInEvent(self, event):
        if self.completer:
            self.completer.setWidget(self)
        super(PythonScriptTextEdit, self).focusInEvent(event)

    def keyPressEvent(self, event):
        key_event = event.key()
        tc = self.textCursor()

        popup_visible = self.completer.popup().isVisible()

        if popup_visible:
            # insert selected completion
            if key_event == key_list.Key_Return or key_event == key_list.Key_Tab:
                self.completer.insert_text.emit(self.completer.get_selected())
                self.completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
                return

            # hide popup on these key presses
            if key_event in (
                    key_list.Key_Left,
                    key_list.Key_Right,
            ):
                self.completer.popup().hide()

        if key_event == key_list.Key_Tab:
            # Indent
            pos = tc.selectionStart()

            selected_text = tc.selection().toPlainText()

            text_was_selected = len(selected_text)
            if not text_was_selected:
                pos = tc.position()
                tc.select(tc.LineUnderCursor)
                selected_text = tc.selectedText()

            if len(selected_text):
                new_lines = []
                for line in selected_text.splitlines():
                    new_lines.append(" " * 4 + line)
                text_to_insert = "\n".join(new_lines)
            else:
                text_to_insert = " " * 4
            tc.insertText(text_to_insert)

            # select indented text
            if text_was_selected:
                tc.setPosition(pos)
                tc.movePosition(tc.Right, tc.KeepAnchor, n=len(text_to_insert))
                self.setTextCursor(tc)
            return

        if key_event == key_list.Key_Backtab:
            # Un-indent
            pos = tc.selectionStart()
            selected_text = tc.selection().toPlainText()

            text_was_selected = len(selected_text)
            if not text_was_selected:
                tc.select(tc.LineUnderCursor)
                selected_text = tc.selectedText()

            new_lines = []
            for line in selected_text.splitlines():
                line_indent = (len(line) - len(line.lstrip(' ')))
                if line_indent <= 3:
                    line = line.lstrip(' ')

                if line_indent > 3:
                    line = line[4:]

                new_lines.append(line)
            inserted_text = "\n".join(new_lines)
            tc.insertText(inserted_text)

            if text_was_selected:
                # select un-indented tabs
                tc.setPosition(pos)
                tc.movePosition(tc.Right, tc.KeepAnchor, n=len(inserted_text))
                self.setTextCursor(tc)
            return

        if key_event == key_list.Key_Return:
            """
            Handle indent on Enter
            """
            current_pos = tc.position()

            # find current character before enter key was pressed
            tc.movePosition(QtGui.QTextCursor.Left)
            tc.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor)
            character_on_enter = tc.selectedText()

            # count spaces on current line
            tc.select(QtGui.QTextCursor.LineUnderCursor)
            line_text = tc.selectedText()
            indent_level = len(line_text) - len(line_text.lstrip(' '))

            # reset to org cursor position
            tc.setPosition(current_pos)

            super(PythonScriptTextEdit, self).keyPressEvent(event)

            leading_spaces = " " * indent_level
            if character_on_enter == ":":  # if function was declared, add some more spaces
                leading_spaces += "    "

            tc.insertText(leading_spaces)

            return

        super(PythonScriptTextEdit, self).keyPressEvent(event)

        if popup_visible:
            tc.select(QtGui.QTextCursor.WordUnderCursor)
            filter_text = tc.selectedText()
            if len(filter_text) == 0:
                self.completer.popup().hide()
                return

            self.completer.set_filter(filter_text)

            if self.completer.filter_model.rowCount() == 0:
                self.completer.popup().hide()

            return

        modifiers = QtWidgets.QApplication.keyboardModifiers()
        ctrl_space_pressed = key_event == key_list.Key_Space and modifiers == QtCore.Qt.ControlModifier

        if key_event == key_list.Key_Period or ctrl_space_pressed:
            popup = self.completer.popup()

            tc.movePosition(QtGui.QTextCursor.Left)
            tc.movePosition(QtGui.QTextCursor.Right, QtGui.QTextCursor.KeepAnchor)
            last_character = tc.selectedText()

            tc.select(QtGui.QTextCursor.LineUnderCursor)
            current_line_text = tc.selectedText()

            self.completer.set_filter("")
            if last_character == "." and current_line_text:
                self.completer.complete_text(current_line_text)
            else:
                self.completer.reset_completion_list()
                if current_line_text:
                    self.completer.set_filter(current_line_text)

            popup.setCurrentIndex(self.completer.completionModel().index(0, 0))

            cr = self.cursorRect()
            cr.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width())
            self.completer.complete(cr)


class ScriptTree(QtWidgets.QWidget):
    file_path_double_clicked = QtCore.Signal(str)

    def __init__(self, parent=None):
        super(ScriptTree, self).__init__(parent)

        self._settings = ScriptEditorSettings()

        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(2)

        self.file_model = QtWidgets.QFileSystemModel()

        folder_path_layout = QtWidgets.QHBoxLayout()
        self.folder_path_line_edit = QtWidgets.QLineEdit()
        folder_path_layout.addWidget(self.folder_path_line_edit)

        self.folder_path_browse_button = QtWidgets.QPushButton("...")
        self.folder_path_browse_button.clicked.connect(self.browse_folder_path)
        folder_path_layout.addWidget(self.folder_path_browse_button)
        main_layout.addLayout(folder_path_layout)

        self.tree_view = QtWidgets.QTreeView()
        self.tree_view.setModel(self.file_model)
        self.tree_view.setHeaderHidden(True)
        for i in range(1, self.tree_view.model().columnCount()):
            self.tree_view.header().hideSection(i)
        main_layout.addWidget(self.tree_view)

        folder_path = self._settings.value(ScriptEditorSettings.k_folder_path)
        if folder_path is None:
            folder_path = os.path.dirname(__file__)
        self.set_folder_path(folder_path)

        self.tree_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self.context_menu)
        self.tree_view.doubleClicked.connect(self._path_double_clicked)

        self.setLayout(main_layout)

    def context_menu(self, position):
        actions = list()
        # actions.append({"Run Script": self.run_script})
        actions.append({"Show in Explorer": self.open_path_in_explorer})
        # actions.append({"Open Backup Folder": self.open_backup_folder})

        menu = self.build_context_menu(actions)
        menu.exec_(self.tree_view.viewport().mapToGlobal(position))

    def build_context_menu(self, actions):
        menu = QtWidgets.QMenu(self)
        for action in actions:
            if action == "-":
                menu.addSeparator()
            else:
                for text, method in action.items():
                    action = QtWidgets.QAction(self)
                    action.setText(text)
                    action.triggered.connect(method)
                    menu.addAction(action)

        return menu

    def set_folder_path(self, folder_path):
        self.folder_path_line_edit.setText(folder_path)
        self.file_model.setRootPath(folder_path)
        self.tree_view.setRootIndex(self.file_model.index(folder_path))
        self._settings.setValue(ScriptEditorSettings.k_folder_path, folder_path)

    def get_folder_path(self):
        return self.folder_path_line_edit.text()

    def browse_folder_path(self):
        p = QtWidgets.QFileDialog.getExistingDirectory(self, "Get Script Folder")
        if p:
            self.set_folder_path(p)

    def _path_double_clicked(self, index):
        path = self.get_file_path_from_index(index)
        if os.path.isfile(path):
            self.file_path_double_clicked.emit(path)

    def get_file_path_from_index(self, index):
        return self.file_model.filePath(index).replace("\\", "/")

    def get_current_selected_file_path(self):
        index = self.tree_view.currentIndex()
        return self.get_file_path_from_index(index)

    def open_path_in_explorer(self, file_path=None):
        if not file_path:
            file_path = self.get_current_selected_file_path()

        if os.path.isdir(file_path):
            file_path += "/"

        file_path = file_path.replace("/", "\\")  # wow, I don't think I've done this intentionally before

        try:
            if os.path.isdir(file_path):
                os.startfile(file_path)  # this felt faster than subprocess on my machine
            else:
                subprocess.Popen(r'explorer /select, "{}"'.format(file_path))
                log.info("you did it")
                log.warning("Attempt to open path in explorer failed")
        except Exception as e:
            log.warning("Attempt to open path in explorer failed")


class LiveScriptEditorWindowUI(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(LiveScriptEditorWindowUI, self).__init__(parent)

        # Class properties
        self.main_layout = QtWidgets.QVBoxLayout()

        self.script_output = ScriptConsoleOutputUI(self)
        self.script_output.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))

        self.script_output_dock = QtWidgets.QDockWidget()
        self.script_output_dock.setWindowTitle("Output")
        self.script_output_dock.setWidget(self.script_output)
        # self.script_output_dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)

        self.script_tree = ScriptTree()
        self.script_tree_dock = QtWidgets.QDockWidget()
        self.script_tree_dock.setWindowTitle("Script Tree")
        self.script_tree_dock.setWidget(self.script_tree)
        # self.script_tree_dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)


class LiveScriptEditorWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super(LiveScriptEditorWindow, self).__init__(parent)
        self.setWindowTitle("Live Script Editor")
        self.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), "icons", "live_script_editor_icon.png")))
        self.setStyleSheet(lk.tool_qt_stylesheet)

        self._settings = ScriptEditorSettings()

        self.script_docks = list()
        self.recently_closed_scripts = list()

        self.ui = LiveScriptEditorWindowUI(self)
        # self.add_script_tab(file_path=__file__)
        self.add_script_tab()

        self.reset_layout()

        self.setDockOptions(self.AnimatedDocks | self.AllowNestedDocks)
        self.setTabPosition(QtCore.Qt.AllDockWidgetAreas, QtWidgets.QTabWidget.TabPosition.North)

        self.resize(900, 700)

        """
        try:
            self.restoreGeometry(self._settings.value("geometry"))
            self.restoreState(self._settings.value("windowState"))
        except AttributeError as e:
            print(e)
        """

        file_menu = self.menuBar().addMenu("File")
        file_menu.setTearOffEnabled(True)
        file_menu.addAction("New Tab", self.add_script_tab, QtGui.QKeySequence("CTRL+N"))
        file_menu.addAction("Open Script...", self.open_script, QtGui.QKeySequence("CTRL+O"))
        file_menu.addAction("Save Script", self.save_current_tab, QtGui.QKeySequence("CTRL+S"))
        file_menu.addAction("Save Script As...", self.save_current_tab_as, QtGui.QKeySequence("CTRL+SHIFT+S"))
        file_menu.addSeparator()
        file_menu.addAction("Re-Open Recent Script", self.reopen_recently_closed, QtGui.QKeySequence("CTRL+SHIFT+T"))
        file_menu.addAction("Reload Script", self.reload_script, QtGui.QKeySequence("CTRL+R"))
        file_menu.addAction("Close Script", self.close_current_tab, QtGui.QKeySequence("CTRL+W"))
        file_menu.addSeparator()
        file_menu.addAction("Run Script", self.run_script, QtGui.QKeySequence("CTRL+RETURN"))

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.setTearOffEnabled(True)
        edit_menu.addAction("Clear History", self.ui.script_output.clear, QtGui.QKeySequence("CTRL+SHIFT+D"))
        edit_menu.addAction("Reset Layout", self.reset_layout, QtGui.QKeySequence("F5"))

        self.ui.script_tree.file_path_double_clicked.connect(self.open_script_path)

        # class properties
        self.interp = code.InteractiveInterpreter(globals())
        self.show_message("Ready")

    """
    def closeEvent(self, event):
        # from https://stackoverflow.com/a/39092887
        # doesn't seem to restore properly for some reason
        self._settings.setValue('geometry', self.saveGeometry())
        self._settings.setValue('windowState', self.saveState())
        super(LiveScriptEditorWindow, self).closeEvent(event)
    """

    def reset_layout(self):
        self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.ui.script_tree_dock)
        self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.ui.script_output_dock)
        self.resizeDocks((self.ui.script_tree_dock, self.ui.script_output_dock), (30, 50), QtCore.Qt.Horizontal)

    def open_script_path(self, path):
        self.add_script_tab(path)

    def add_script_tab(self, file_path=None):

        # custom QT widget for ScriptEditing
        script_text_edit = PythonScriptTextEdit(file_path=file_path)
        script_text_edit.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        script_text_edit.setWordWrapMode(QtGui.QTextOption.NoWrap)

        # syntax highlight
        python_syntax_highlight.PythonHighlighter(script_text_edit.document())

        # Dock Widget for ScriptTab
        script_tabs_dock = QtWidgets.QDockWidget()
        script_tabs_dock.setWidget(script_text_edit)
        script_text_edit.dock_widget = script_tabs_dock  # not very safe

        if file_path:
            script_text_edit.load_script(file_path)
            self.show_message("Opened: {}".format(file_path))
        else:
            script_text_edit.script_name = "Python"
            script_text_edit.set_dock_tab_name("Python")

        # dock to existing script tab, or make new at the bottom
        if self.script_docks:
            self.tabifyDockWidget(self.script_docks[-1], script_tabs_dock)
        else:
            self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, script_tabs_dock)

        # show new tab and set focus to ti
        script_tabs_dock.show()
        script_tabs_dock.raise_()
        script_text_edit.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
        self.script_docks.append(script_tabs_dock)

    def get_active_script_text_edit(self):
        for dock in self.script_docks:  # type: QtWidgets.QDockWidget
            if dock.widget().hasFocus():
                if dock.visibleRegion().isEmpty():  # if it's not visible, don't return it
                    continue
                return dock.widget()
        return self.script_docks[-1].widget()  # nothing has focus, return last created

    def close_current_tab(self):
        docks_in_focus = []
        for dock in self.script_docks:  # type: QtWidgets.QDockWidget
            dock_widget = dock.widget()  # type: PythonScriptTextEdit
            if dock_widget.hasFocus():
                if dock.visibleRegion().isEmpty():  # if it's not visible, don't close it
                    continue
                docks_in_focus.append(dock)
                self.script_docks.remove(dock)
                self.recently_closed_scripts.append(dock_widget.script_file_path)
                self.show_message("Closed: {}".format(dock_widget.script_name))

        [d.close() for d in docks_in_focus]
        [d.deleteLater() for d in docks_in_focus]

        if len(self.script_docks):
            self.script_docks[-1].widget().setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)

    def save_current_tab(self):
        active_script = self.get_active_script_text_edit()  # type: PythonScriptTextEdit
        if active_script.save_script(start_dir=self.ui.script_tree.get_folder_path()):
            self.show_message("Saved: {}".format(active_script.script_name))

    def save_current_tab_as(self):
        active_script = self.get_active_script_text_edit()  # type: PythonScriptTextEdit
        if active_script.save_script(save_as=True, start_dir=self.ui.script_tree.get_folder_path()):
            self.show_message("Saved: {}".format(active_script.script_name))

    def open_script(self):
        active_script = self.get_active_script_text_edit()  # type: PythonScriptTextEdit
        if active_script.load_script(open_dialog=True, start_dir=self.ui.script_tree.get_folder_path()):
            self.show_message("Opened: {}".format(active_script.script_name))

    def reload_script(self):
        active_script = self.get_active_script_text_edit()  # type: PythonScriptTextEdit
        if active_script.load_script(start_dir=self.ui.script_tree.get_folder_path()):
            self.show_message("Reloaded: {}".format(active_script.script_name))

    def reopen_recently_closed(self):
        if not len(self.recently_closed_scripts):
            self.show_message("No recent scripts found")
            return
        recent_script_path = self.recently_closed_scripts.pop(-1)
        self.add_script_tab(recent_script_path)

    def show_message(self, text):
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        self.statusBar().showMessage("{} - {}".format(current_time, text))

    def run_script(self):
        active_script = self.get_active_script_text_edit()  # type: PythonScriptTextEdit

        # Get selected text
        cursor = active_script.textCursor()  # type: QtGui.QTextCursor
        python_script_text = cursor.selection().toPlainText()

        # If no text selected, get the entire script
        if not python_script_text:
            python_script_text = active_script.toPlainText()

        self.ui.script_output.write_input(python_script_text)

        # execute script
        if python_script_text.count("\n"):
            self.interp.runcode(python_script_text)  # runsource fails on multi-line
        else:
            self.interp.runsource(python_script_text)  # shows results of single line commands
        self.show_message("Executed: {}".format(active_script.script_name))


class Redirect(object):
    """
    Map self.write to a function
    from: https://python-forum.io/Thread-Embed-Python-console-in-GUI-application
    """

    def __init__(self, func: Callable) -> 'Redirect':
        self.func = func

    def write(self, line: str) -> None:
        self.func(line)


def main():
    arg_dialog = LiveScriptEditorWindow()
    return arg_dialog


if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)

    dialog = main()
    redirect = Redirect(dialog.ui.script_output.write_error)

    # This might be tricky to replicate in DCC
    # It looks like the app.exec_() needs to be part of the 'with'
    with redirect_stdout(dialog.ui.script_output), redirect_stderr(redirect):
        dialog.show()
        sys.exit(app.exec_())
