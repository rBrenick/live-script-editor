import code
import os
import sys
from contextlib import redirect_stdout, redirect_stderr
from typing import Callable

from Qt import QtCore, QtWidgets, QtGui

key_list = QtCore.Qt

from live_script_editor import python_syntax_highlight

tool_qt_stylesheet = ""
stylesheet_path = os.path.join(os.path.dirname(__file__), "stylesheets", "darkblue.stylesheet")
if os.path.exists(stylesheet_path):
    with open(stylesheet_path, "r") as fh:
        tool_qt_stylesheet = fh.read()


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

        if len(line) != 1 or ord(line[0]) != 10:
            self.appendPlainText(line.rstrip())
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class PythonObjectCompleter(QtWidgets.QCompleter):
    insert_text = QtCore.Signal(str)

    def __init__(self, parent=None):
        super(PythonObjectCompleter, self).__init__(["yeahh", "boiiii"], parent)  # if this shows up, that means trouble

        self.popup().setStyleSheet(tool_qt_stylesheet)

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
        selected_obj_full_name = text.split(" ")[-1]
        dot_split = selected_obj_full_name.split(".")

        selected_obj_base = dot_split[0]  # globals only has root imports
        selected_obj = globals().get(selected_obj_base)

        if selected_obj:
            if selected_obj_full_name.endswith("."):
                selected_obj_full_name = selected_obj_full_name.rstrip(".")

            completion_list = dir(eval(selected_obj_full_name))  # this doesn't feel very safe

            self.item_model.setStringList(completion_list)

    def set_filter(self, filter_text):
        self.filter_model.setFilterFixedString(filter_text)
        self.filter_text = filter_text


class PythonScriptTextEdit(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super(PythonScriptTextEdit, self).__init__(parent)

        self.completer = PythonObjectCompleter()
        self.completer.setWidget(self)
        self.completer.setMaxVisibleItems(20)
        self.completer.insert_text.connect(self.insert_completion)

        self.filter_is_active = False

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

        if key_event == key_list.Key_Period:
            popup = self.completer.popup()
            tc.select(QtGui.QTextCursor.LineUnderCursor)

            self.completer.complete_text(tc.selectedText())
            self.completer.set_filter("")

            popup.setCurrentIndex(self.completer.completionModel().index(0, 0))

            cr = self.cursorRect()
            cr.setWidth(self.completer.popup().sizeHintForColumn(
                0) + self.completer.popup().verticalScrollBar().sizeHint().width())
            self.completer.complete(cr)


class LiveScriptEditorWindowUI(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(LiveScriptEditorWindowUI, self).__init__(parent)

        # Class properties
        self.main_layout = QtWidgets.QVBoxLayout()

        self.script_output = ScriptConsoleOutputUI(self)
        self.script_output.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.main_layout.addWidget(self.script_output)

        self.script_tab_widget = QtWidgets.QTabWidget()
        # self.script_tab_widget.setTabsClosable(True)
        self.main_layout.addWidget(self.script_tab_widget)

        self.add_script_tab()

        self.setLayout(self.main_layout)
        self.main_layout.setStretch(0, 30)
        self.main_layout.setStretch(1, 50)

    def add_script_tab(self, tab_name="Python"):
        script_text_edit = PythonScriptTextEdit()
        script_text_edit.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        script_text_edit.setWordWrapMode(QtGui.QTextOption.NoWrap)
        test_str = """import sys
print(sys.executable)

"heyo, some text"
'''other sub text'''
# comment
123 + 123.34
class Testing():
    def __init__(self):
        self.something = 123

def test_func(arg):
    print(arg)
"""

        script_text_edit.setPlainText(test_str)

        python_syntax_highlight.PythonHighlighter(script_text_edit.document())

        self.script_tab_widget.addTab(script_text_edit, tab_name)
        self.script_tab_widget.setCurrentIndex(self.script_tab_widget.count() - 1)


class LiveScriptEditorWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super(LiveScriptEditorWindow, self).__init__(parent)
        self.setWindowTitle("Live Script Editor")
        self.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), "icons", "argument_dialog_icon.png")))

        self.setStyleSheet(tool_qt_stylesheet)

        self.ui = LiveScriptEditorWindowUI(self)
        self.setCentralWidget(self.ui)
        self.resize(700, 500)

        file_menu = self.menuBar().addMenu("File")
        file_menu.setTearOffEnabled(True)
        file_menu.addAction("New Tab", self.ui.add_script_tab, QtGui.QKeySequence("CTRL+N"))
        file_menu.addAction("Close Tab", self.close_current_tab, QtGui.QKeySequence("CTRL+W"))
        file_menu.addSeparator()
        file_menu.addAction("Run Script", self.run_script, QtGui.QKeySequence("CTRL+RETURN"))

        edit_menu = self.menuBar().addMenu("Edit")
        edit_menu.setTearOffEnabled(True)
        edit_menu.addAction("Clear History", self.ui.script_output.clear, QtGui.QKeySequence("CTRL+SHIFT+D"))

        # class properties
        self.interp = code.InteractiveInterpreter(globals())

    def get_active_script_text_edit(self):
        return self.ui.script_tab_widget.currentWidget()

    def close_current_tab(self):
        self.ui.script_tab_widget.removeTab(self.ui.script_tab_widget.currentIndex())

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
