#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json, socket
import codecs
import distutils.spawn
import os.path
import platform
import re
import sys
import subprocess

import requests
import numpy as np
import pandas as pd

from functools import partial
from collections import defaultdict

try:
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    # needed for py3+qt4
    # Ref:
    # http://pyqt.sourceforge.net/Docs/PyQt4/incompatible_apis.html
    # http://stackoverflow.com/questions/21217399/pyqt4-qtcore-qvariant-object-instead-of-a-string
    if sys.version_info.major >= 3:
        import sip
        sip.setapi('QVariant', 2)
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *

import resources
# Add internal libs
from libs.constants import *
from libs.utils import *
from libs.settings import Settings
from libs.shape import Shape, DEFAULT_LINE_COLOR, DEFAULT_FILL_COLOR
from libs.stringBundle import StringBundle
from libs.canvas import Canvas
from libs.zoomWidget import ZoomWidget
from libs.labelDialog import LabelDialog
from libs.colorDialog import ColorDialog
from libs.labelFile import LabelFile, LabelFileError
from libs.toolBar import ToolBar
from libs.pascal_voc_io import PascalVocReader
from libs.pascal_voc_io import XML_EXT
from libs.yolo_io import YoloReader
from libs.yolo_io import TXT_EXT
from libs.ustr import ustr
from libs.version import __version__
from libs.hashableQListWidgetItem import HashableQListWidgetItem

__appname__ = 'Label National ID'


import numpy as np
import cv2
def order_points(pts):
    # initialzie a list of coordinates that will be ordered
    # such that the first entry in the list is the top-left,
    # the second entry is the top-right, the third is the
    # bottom-right, and the fourth is the bottom-left
    rect = np.zeros((4, 2), dtype="float32")

    # the top-left point will have the smallest sum, whereas
    # the bottom-right point will have the largest sum
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    # now, compute the difference between the points, the
    # top-right point will have the smallest difference,
    # whereas the bottom-left will have the largest difference
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]

    # return the ordered coordinates
    return rect


def four_point_transform(image, pts):
    # obtain a consistent order of the points and unpack them
    # individually
    rect = order_points(pts)
    (tl, tr, br, bl) = rect

    # compute the width of the new image, which will be the
    # maximum distance between bottom-right and bottom-left
    # x-coordinates or the top-right and top-left x-coordinates
    width_a = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    width_b = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    max_width = max(int(width_a), int(width_b))

    # compute the height of the new image, which will be the
    # maximum distance between the top-right and bottom-right
    # y-coordinates or the top-left and bottom-left y-coordinates
    height_a = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    height_b = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    max_height = max(int(height_a), int(height_b))

    # now that we have the dimensions of the new image, construct
    # the set of destination points to obtain a "birds eye view",
    # (i.e. top-down view) of the image, again specifying points
    # in the top-left, top-right, bottom-right, and bottom-left
    # order
    dst = np.array([
        [0, 0],
        [max_width - 1, 0],
        [max_width - 1, max_height - 1],
        [0, max_height - 1]], dtype="float32")

    # compute the perspective transform matrix and then apply it
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (max_width, max_height))

    # return the warped image
    return warped


class WindowMixin(object):

    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            addActions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName(u'%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            addActions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar


class OpenLabelDialog(QDialog):
    def __init__(self, images_dir, bbox_filename, parent=None):
        super(OpenLabelDialog, self).__init__(parent)

        layout = QVBoxLayout(self)

        self.images_dir_lbl = QLabel("Choose your images folder: ")
        self.images_dir = QLineEdit(images_dir)
        self.bbox_filename_lbl = QLabel("Choose your bbox filename:")
        self.bbox_filename = QLineEdit(bbox_filename)
        self.username_lbl = QLabel("Your username:")
        self.username = QLineEdit("")
        self.username.setFocusPolicy(Qt.StrongFocus)
        self.password_lbl = QLabel("Your password:")
        self.password = QLineEdit("")
        self.password.setEchoMode(QLineEdit.Password)
        self.label_filename_lbl = QLabel("Label filename:")
        self.label_filename = QLineEdit(os.path.join(os.path.expanduser('~'), '$user_results.json'))
        self.setFixedSize(600, 700)

        layout.addWidget(self.images_dir_lbl)
        layout.addWidget(self.images_dir)
        layout.addWidget(self.bbox_filename_lbl)
        layout.addWidget(self.bbox_filename)
        layout.addWidget(self.username_lbl)
        layout.addWidget(self.username)
        layout.addWidget(self.password_lbl)
        layout.addWidget(self.password)
        layout.addWidget(self.label_filename_lbl)
        layout.addWidget(self.label_filename)

        # OK and Cancel buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # static method to create the dialog and return (date, time, accepted)
    @staticmethod
    def get_result(images_dir, bbox_filename, parent=None):
        dialog = OpenLabelDialog(images_dir, bbox_filename, parent)
        dialog.username.setFocus()
        result = dialog.exec_()
        images_dir = dialog.images_dir.text()
        bbox_filename = dialog.bbox_filename.text()
        username = dialog.username.text()
        password = dialog.password.text()
        label_filename = dialog.label_filename.text()
        if username.strip() != '':
            images_dir = images_dir.replace('$user', username)
            bbox_filename = bbox_filename.replace('$user', username)
            label_filename = label_filename.replace('$user', username)
        return images_dir, bbox_filename, username, password, label_filename, result == QDialog.Accepted


class MainWindow(QMainWindow, WindowMixin):
    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = list(range(3))

    def __init__(self, default_filename=None, default_prefdef_classfile=None, default_save_dir=None,
                 phase=1, start_idx=-1, end_idx=-1, user_scale=1.0):
        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)
        self.start_idx = start_idx
        self.end_idx = end_idx

        self.suggest_label_info = None

        # Load setting in the main thread
        self.settings = Settings()
        self.settings.load()
        settings = self.settings

        # Load string bundle for i18n
        self.stringBundle = StringBundle.getBundle()
        getStr = lambda strId: self.stringBundle.getString(strId)

        # Save as Pascal voc xml
        self.defaultSaveDir = default_save_dir
        self.usingPascalVocFormat = True
        self.usingYoloFormat = False

        # For loading all image under a directory
        self.mImgList = []
        self.dirname = None
        self.labelHist = []
        self.lastOpenDir = None

        # Whether we need to save or not.
        self.dirty = False

        # Edit frame mode
        self.save_status = False
        self.image_data = None

        # Label info
        self.label_info = dict()

        self._noSelectionSlot = False
        self._beginner = True
        self.screencastViewer = self.getAvailableScreencastViewer()
        self.screencast = "https://youtu.be/p0nR2YsCY_U"

        # Load predefined classes to the list
        self.loadPredefinedClasses(default_prefdef_classfile)

        # Main widgets and related state.
        self.labelDialog = LabelDialog(parent=self, listItem=self.labelHist)

        self.itemsToShapes = {}
        self.shapesToItems = {}
        self.prevLabelText = ''

        list_layout = QVBoxLayout()
        list_layout.setContentsMargins(0, 0, 0, 0)

        # Create a widget for using default label
        self.useDefaultLabelCheckbox = QCheckBox(getStr('useDefaultLabel'))
        self.useDefaultLabelCheckbox.setChecked(False)
        self.defaultLabelTextLine = QLineEdit()
        use_default_label_qh_box_layout = QHBoxLayout()
        use_default_label_qh_box_layout.addWidget(self.useDefaultLabelCheckbox)
        use_default_label_qh_box_layout.addWidget(self.defaultLabelTextLine)
        use_default_label_container = QWidget()
        use_default_label_container.setLayout(use_default_label_qh_box_layout)

        # Create a widget for edit and difficult button
        self.diffcButton = QCheckBox(getStr('useDifficult'))
        self.diffcButton.setChecked(False)
        self.diffcButton.stateChanged.connect(self.btnstate)
        self.editButton = QToolButton()
        self.editButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        # Add some of widgets to list_layout
        # list_layout.addWidget(self.editButton)
        # list_layout.addWidget(self.diffcButton)
        # list_layout.addWidget(use_default_label_container)

        # Create and add a widget for showing current label items
        self.labelList = QListWidget()
        label_list_container = QWidget()
        label_list_container.setLayout(list_layout)
        self.labelList.itemActivated.connect(self.labelSelectionChanged)
        self.labelList.itemSelectionChanged.connect(self.labelSelectionChanged)
        self.labelList.itemDoubleClicked.connect(self.editLabel)
        # Connect to itemChanged to detect checkbox changes.
        self.labelList.itemChanged.connect(self.labelItemChanged)
        list_layout.addWidget(self.labelList)

        self.dock = QDockWidget(getStr('boxLabelText'), self)
        self.dock.setObjectName(getStr('labels'))
        self.dock.setWidget(label_list_container)

        self.fileListWidget = QListWidget()
        self.fileListWidget.itemDoubleClicked.connect(self.fileitemDoubleClicked)
        filelist_layout = QVBoxLayout()
        filelist_layout.setContentsMargins(0, 0, 0, 0)
        filelist_layout.addWidget(self.fileListWidget)
        file_list_container = QWidget()
        file_list_container.setLayout(filelist_layout)
        self.filedock = QDockWidget(getStr('fileList'), self)
        self.filedock.setObjectName(getStr('files'))
        self.filedock.setWidget(file_list_container)

        self.zoomWidget = ZoomWidget()
        self.colorDialog = ColorDialog(parent=self)

        self.canvas = Canvas(parent=self)
        self.canvas.zoomRequest.connect(self.zoomRequest)
        self.canvas.setDrawingShapeToSquare(settings.get(SETTING_DRAW_SQUARE, False))

        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        self.scrollBars = {
            Qt.Vertical: scroll.verticalScrollBar(),
            Qt.Horizontal: scroll.horizontalScrollBar()
        }
        self.scrollArea = scroll
        self.canvas.scrollRequest.connect(self.scrollRequest)

        self.canvas.newShape.connect(self.newShape)
        self.canvas.shapeMoved.connect(self.setDirty)
        self.canvas.selectionChanged.connect(self.shapeSelectionChanged)
        self.canvas.drawingPolygon.connect(self.toggleDrawingSensitive)

        self.setCentralWidget(scroll)
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.filedock)
        self.filedock.setFeatures(QDockWidget.DockWidgetFloatable)

        self.dockFeatures = QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
        self.dock.setFeatures(self.dock.features() ^ self.dockFeatures)
        # self.dock.close()
        self.phase = phase
        self.canvas.scale_factor = user_scale
        # Actions
        action = partial(newAction, self)
        quit = action(getStr('quit'), self.close,
                      'Ctrl+Q', 'quit', getStr('quitApp'))

        open = action(getStr('openFile'), self.openFile,
                      'Ctrl+O', 'open', getStr('openFileDetail'))

        opendir = action(getStr('openDir'), self.open_dir_dialog,
                         'Ctrl+u', 'open', getStr('openDir'))

        changeSavedir = action(getStr('changeSaveDir'), self.changeSavedirDialog,
                               'Ctrl+Shift+r', 'open', getStr('changeSavedAnnotationDir'), enabled=False)

        openAnnotation = action(getStr('openAnnotation'), self.openAnnotationDialog,
                                'Ctrl+Shift+O', 'open', getStr('openAnnotationDetail'))

        openNextImg = action(getStr('nextImg'), self.open_next_img,
                             'd', 'next', getStr('nextImgDetail'))

        openPrevImg = action(getStr('prevImg'), self.open_previous_img,
                             'a', 'prev', getStr('prevImgDetail'))

        verify = action(getStr('verifyImg'), self.verifyImg,
                        'space', 'verify', getStr('verifyImgDetail'))

        save = action(getStr('save'), self.saveFile,
                      'Ctrl+S', 'save', getStr('saveDetail'), enabled=False)

        save_format = action('&PascalVOC', self.change_format,
                      'Ctrl+', 'format_voc', getStr('changeSaveFormat'), enabled=False)

        save_as = action(getStr('saveAs'), self.saveFileAs,
                        'Ctrl+Shift+S', 'save-as', getStr('saveAsDetail'), enabled=False)

        close = action(getStr('closeCur'), self.closeFile, 'Ctrl+W', 'close', getStr('closeCurDetail'))

        reset_all = action(getStr('resetAll'), self.resetAll, None, 'resetall', getStr('resetAllDetail'))

        color1 = action(getStr('boxLineColor'), self.chooseColor1,
                        'Ctrl+L', 'color_line', getStr('boxLineColorDetail'))

        create_mode = action(getStr('crtBox'), self.setCreateMode,
                            'w', 'new', getStr('crtBoxDetail'), enabled=False)
        edit_mode = action('&Edit\nRectBox', self.setEditMode,
                          'Ctrl+J', 'edit', u'Move and edit Boxs', enabled=False)

        create = action(getStr('crtBox'), self.createShape,
                        'w', 'new', getStr('crtBoxDetail'), enabled=False)
        delete = action(getStr('delBox'), self.deleteSelectedShape,
                        'Delete', 'delete', getStr('delBoxDetail'), enabled=False)
        copy = action(getStr('dupBox'), self.copySelectedShape,
                      'Ctrl+D', 'copy', getStr('dupBoxDetail'),
                      enabled=False)

        advanced_mode = action(getStr('advancedMode'), self.toggleAdvancedMode,
                              'Ctrl+Shift+A', 'expert', getStr('advancedModeDetail'),
                              checkable=True)

        hide_all = action('&Hide\nRectBox', partial(self.togglePolygons, False),
                         'Ctrl+H', 'hide', getStr('hideAllBoxDetail'),
                         enabled=False)
        show_all = action('&Show\nRectBox', partial(self.togglePolygons, True),
                         'Ctrl+A', 'hide', getStr('showAllBoxDetail'),
                         enabled=False)

        align_crop = action('&Align && Crop', partial(self.align_crop, False),
                            'Ctrl+Shift+A', 'hide', getStr('showAllBoxDetail'),
                            enabled=False)

        rotate_left = action('&Rotate -90', partial(self.rotate, -90),
                         'Ctrl+r', 'hide', getStr('showAllBoxDetail'),
                         enabled=True)

        rotate_right = action('&Rotate +90', partial(self.rotate, 90),
                            'Ctrl+t', 'hide', getStr('showAllBoxDetail'),
                            enabled=True)

        help = action(getStr('tutorial'), self.showTutorialDialog, None, 'help', getStr('tutorialDetail'))
        show_info = action(getStr('info'), self.showInfoDialog, None, 'help', getStr('info'))

        zoom = QWidgetAction(self)
        zoom.setDefaultWidget(self.zoomWidget)
        self.zoomWidget.setWhatsThis(
            u"Zoom in or out of the image. Also accessible with"
            " %s and %s from the canvas." % (fmtShortcut("Ctrl+[-+]"),
                                             fmtShortcut("Ctrl+Wheel")))
        self.zoomWidget.setEnabled(False)

        zoom_in = action(getStr('zoomin'), partial(self.addZoom, 10),
                        'Ctrl++', 'zoom-in', getStr('zoominDetail'), enabled=False)
        zoom_out = action(getStr('zoomout'), partial(self.addZoom, -10),
                         'Ctrl+-', 'zoom-out', getStr('zoomoutDetail'), enabled=False)
        zoom_org = action(getStr('originalsize'), partial(self.setZoom, 100),
                         'Ctrl+=', 'zoom', getStr('originalsizeDetail'), enabled=False)
        fit_window = action(getStr('fitWin'), self.setFitWindow,
                           'Ctrl+F', 'fit-window', getStr('fitWinDetail'),
                           checkable=True, enabled=False)
        fit_width = action(getStr('fitWidth'), self.setFitWidth,
                          'Ctrl+Shift+F', 'fit-width', getStr('fitWidthDetail'),
                          checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoom_actions = (self.zoomWidget, zoom_in, zoom_out,
                       zoom_org, fit_window, fit_width)
        self.zoomMode = self.FIT_WINDOW
        self.scalers = {
            self.FIT_WINDOW: self.scaleFitWindow,
            self.FIT_WIDTH: self.scaleFitWidth,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = action(getStr('editLabel'), self.editLabel,
                      'E', 'edit', getStr('editLabelDetail'),
                      enabled=False)
        self.editButton.setDefaultAction(edit)

        shape_line_color = action(getStr('shapeLineColor'), self.chshapeLineColor,
                                icon='color_line', tip=getStr('shapeLineColorDetail'),
                                enabled=False)
        shape_fill_color = action(getStr('shapeFillColor'), self.chshapeFillColor,
                                icon='color', tip=getStr('shapeFillColorDetail'),
                                enabled=False)

        labels = self.dock.toggleViewAction()
        labels.setText(getStr('showHide'))
        labels.setShortcut('Ctrl+Shift+L')
        if self.phase == 0:
            self.dock.close()

        # Label list context menu.
        label_menu = QMenu()
        addActions(label_menu, (edit, delete))
        self.labelList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.labelList.customContextMenuRequested.connect(
            self.popLabelListMenu)

        # Draw squares/rectangles
        self.drawSquaresOption = QAction('Draw Squares', self)
        self.drawSquaresOption.setShortcut('Ctrl+Shift+R')
        self.drawSquaresOption.setCheckable(True)
        self.drawSquaresOption.setChecked(settings.get(SETTING_DRAW_SQUARE, False))
        self.drawSquaresOption.triggered.connect(self.toogleDrawSquare)

        # Store actions for further handling.
        self.actions = struct(save=save, save_format=save_format, saveAs=save_as, open=open, close=close, resetAll = reset_all,
                              lineColor=color1, create=create, delete=delete, edit=edit, copy=copy,
                              createMode=create_mode, editMode=edit_mode, advancedMode=advanced_mode,
                              shapeLineColor=shape_line_color, shapeFillColor=shape_fill_color,
                              zoom=zoom, zoomIn=zoom_in, zoomOut=zoom_out, zoomOrg=zoom_org,
                              fitWindow=fit_window, fitWidth=fit_width,
                              zoomActions=zoom_actions,
                              fileMenuActions=(
                                  open, opendir, save, save_as, close, reset_all, quit),
                              beginner=(), advanced=(),
                              editMenu=(edit, copy, delete,
                                        None, color1, self.drawSquaresOption),
                              beginnerContext=(create, edit, copy, delete),
                              advancedContext=(create_mode, edit_mode, edit, copy,
                                               delete, shape_line_color, shape_fill_color),
                              onLoadActive=(
                                  close, create, create_mode, edit_mode),
                              onShapesPresent=(save_as, hide_all, show_all))

        self.menus = struct(
            file=self.menu('&File'),
            edit=self.menu('&Edit'),
            view=self.menu('&View'),
            help=self.menu('&Help'),
            recentFiles=QMenu('Open &Recent'),
            labelList=label_menu)

        # Auto saving : Enable auto saving if pressing next
        self.autoSaving = QAction(getStr('autoSaveMode'), self)
        self.autoSaving.setCheckable(True)
        self.autoSaving.setChecked(settings.get(SETTING_AUTO_SAVE, False))
        # Sync single class mode from PR#106
        self.singleClassMode = QAction(getStr('singleClsMode'), self)
        self.singleClassMode.setShortcut("Ctrl+Shift+S")
        self.singleClassMode.setCheckable(True)
        self.singleClassMode.setChecked(settings.get(SETTING_SINGLE_CLASS, False))
        self.lastLabel = None
        # Add option to enable/disable labels being displayed at the top of bounding boxes
        self.displayLabelOption = QAction(getStr('displayLabel'), self)
        self.displayLabelOption.setShortcut("Ctrl+Shift+P")
        self.displayLabelOption.setCheckable(True)
        self.displayLabelOption.setChecked(settings.get(SETTING_PAINT_LABEL, False))
        self.displayLabelOption.triggered.connect(self.togglePaintLabelsOption)

        addActions(self.menus.file,
                   (open, opendir, changeSavedir, openAnnotation, self.menus.recentFiles, save, save_format, save_as, close, reset_all, quit))
        addActions(self.menus.help, (help, show_info))
        addActions(self.menus.view, (
            self.autoSaving,
            self.singleClassMode,
            self.displayLabelOption,
            labels, advanced_mode, None,
            hide_all, show_all, None,
            zoom_in, zoom_out, zoom_org, None,
            fit_window, fit_width))

        self.menus.file.aboutToShow.connect(self.updateFileMenu)

        # Custom context menu for the canvas widget:
        addActions(self.canvas.menus[0], self.actions.beginnerContext)
        addActions(self.canvas.menus[1], (
            action('&Copy here', self.copyShape),
            action('&Move here', self.moveShape)))

        self.tools = self.toolbar('Tools')
        self.actions.beginner = (
            # open, opendir, changeSavedir, openNextImg, openPrevImg, verify, save, save_format, None, create, copy, delete, None,
            opendir, changeSavedir, openNextImg, openPrevImg, None, create, copy, delete, None,
            zoom_in, zoom, zoom_out, fit_window, fit_width, None, align_crop, None, rotate_right, rotate_left)

        self.actions.advanced = (
            open, opendir, changeSavedir, openNextImg, openPrevImg, save, save_format, None,
            create_mode, edit_mode, None,
            hide_all, show_all)

        self.statusBar().showMessage('%s started.' % __appname__)
        self.statusBar().show()

        # Application state.
        self.image = QImage()
        self.filepath = ustr(default_filename)
        self.recentFiles = []
        self.maxRecent = 7
        self.lineColor = None
        self.fillColor = None
        self.zoom_level = 100
        self.fit_window = False
        # Add Chris
        self.difficult = False

        # Fix the compatible issue for qt4 and qt5. Convert the QStringList to python list
        if settings.get(SETTING_RECENT_FILES):
            if have_qstring():
                recent_file_q_string_list = settings.get(SETTING_RECENT_FILES)
                self.recentFiles = [ustr(i) for i in recent_file_q_string_list]
            else:
                self.recentFiles = recent_file_q_string_list = settings.get(SETTING_RECENT_FILES)

        size = settings.get(SETTING_WIN_SIZE, QSize(600, 500))
        position = QPoint(0, 0)
        saved_position = settings.get(SETTING_WIN_POSE, position)
        # Fix the multiple monitors issue
        for i in range(QApplication.desktop().screenCount()):
            if QApplication.desktop().availableGeometry(i).contains(saved_position):
                position = saved_position
                break
        self.resize(size)
        self.move(position)
        save_dir = ustr(settings.get(SETTING_SAVE_DIR, None))
        self.lastOpenDir = ustr(settings.get(SETTING_LAST_OPEN_DIR, None))
        if self.defaultSaveDir is None and save_dir is not None and os.path.exists(save_dir):
            self.defaultSaveDir = save_dir
            self.statusBar().showMessage('%s started. Annotation will be saved to %s' %
                                         (__appname__, self.defaultSaveDir))
            self.statusBar().show()

        self.restoreState(settings.get(SETTING_WIN_STATE, QByteArray()))
        Shape.line_color = self.lineColor = QColor(settings.get(SETTING_LINE_COLOR, DEFAULT_LINE_COLOR))
        Shape.fill_color = self.fillColor = QColor(settings.get(SETTING_FILL_COLOR, DEFAULT_FILL_COLOR))
        self.canvas.setDrawingColor(self.lineColor)
        # Add chris
        Shape.difficult = self.difficult

        def xbool(x):
            if isinstance(x, QVariant):
                return x.toBool()
            return bool(x)

        if xbool(settings.get(SETTING_ADVANCE_MODE, False)):
            self.actions.advancedMode.setChecked(True)
            self.toggleAdvancedMode()

        # Populate the File menu dynamically.
        self.updateFileMenu()

        # Since loading the file may take some time, make sure it runs in the background.
        if self.filepath and os.path.isdir(self.filepath):
            self.queueEvent(partial(self.import_dir_images, self.filepath or ""))
        elif self.filepath:
            self.queueEvent(partial(self.load_file, self.filepath or ""))

        # Callbacks:
        self.zoomWidget.valueChanged.connect(self.paintCanvas)

        self.populateModeActions()

        # Display cursor coordinates at the right of status bar
        self.labelCoordinates = QLabel('')
        self.statusBar().addPermanentWidget(self.labelCoordinates)

        # Open Dir if default file
        if self.filepath and os.path.isdir(self.filepath):
            self.open_dir_dialog(dirpath=self.filepath)

        self.setFocusPolicy(Qt.ClickFocus)

        self.aligned_points = None
        self.curr_index = -1

    @property
    def phase(self):
        return self.__phase

    @phase.setter
    def phase(self, value):
        self.__phase = value
        if self.canvas is not None:
            self.canvas.phase = value

    def keyReleaseEvent(self, event):
        modifiers = event.modifiers()

        if event.key() == Qt.Key_PageDown or event.key() == Qt.Key_N:
            self.open_next_img()
        elif event.key() == Qt.Key_PageUp or event.key() == Qt.Key_B:
            self.open_previous_img()
        elif event.key() == Qt.Key_S and modifiers == Qt.ControlModifier:
            self.save_status = True
        elif event.key() == Qt.Key_R:
            self.rotate(-90)
        elif event.key() == Qt.Key_T:
            self.rotate(90)
        if event.key() == Qt.Key_Control:
            self.canvas.setDrawingShapeToSquare(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Control:
            # Draw rectangle if Ctrl is pressed
            self.canvas.setDrawingShapeToSquare(True)

    # Support Functions ##
    def set_format(self, save_format):
        if save_format == FORMAT_PASCALVOC:
            self.actions.save_format.setText(FORMAT_PASCALVOC)
            self.actions.save_format.setIcon(newIcon("format_voc"))
            self.usingPascalVocFormat = True
            self.usingYoloFormat = False
            LabelFile.suffix = XML_EXT

        elif save_format == FORMAT_YOLO:
            self.actions.save_format.setText(FORMAT_YOLO)
            self.actions.save_format.setIcon(newIcon("format_yolo"))
            self.usingPascalVocFormat = False
            self.usingYoloFormat = True
            LabelFile.suffix = TXT_EXT

    def change_format(self):
        if self.usingPascalVocFormat: self.set_format(FORMAT_YOLO)
        elif self.usingYoloFormat: self.set_format(FORMAT_PASCALVOC)

    def no_shapes(self):
        return not self.itemsToShapes

    def align_crop(self, value):
        if self.phase == 0:
            self.phase = 1
            self.canvas.phase = 1
            self.aligned_points = np.array([(p.x(), p.y()) for p in self.canvas.shapes[0].points])
            filename = os.path.relpath(self.filepath, self.dirname)
            if filename not in self.label_info.keys():
                self.label_info[filename] = dict()
                self.label_info[filename]['rotation'] = 0
                self.label_info[filename]['photo_type'] = self.canvas.photo_type

            self.label_info[filename]['aligned'] = self.aligned_points.tolist()

            self.load_file(self.filepath, 0)
        else:
            self.phase = 0
            self.canvas.phase = 0
            self.load_file(self.filepath)

    def rotate(self, value):
        filename = self.filename  # os.path.relpath(self.filepath, self.dirname)
        if filename not in self.label_info.keys():
            self.label_info[filename] = dict()
            self.label_info[filename]['rotation'] = 0
            self.label_info[filename]['photo_type'] = self.canvas.photo_type

        self.aligned_points = np.array([(p.x(), p.y()) for p in self.canvas.shapes[0].points])
        self.label_info[filename]['aligned'] = self.aligned_points.tolist()
        self.label_info[filename]['rotation'] = (self.label_info[filename]['rotation'] + value) % 360
        total_angle = self.label_info[filename]['rotation']
        print(total_angle)
        if total_angle == 90 or total_angle == 270:
            image_width = self.image.width()
            image_height = self.image.height()
        elif total_angle == 0 or total_angle == 180:
            image_width = self.image.height()
            image_height = self.image.width()
        else:
            raise NotImplementedError()
        print(self.label_info[filename]['aligned'])
        points = []
        for x in self.label_info[filename]['aligned']:
            if value == 90:
                x[1] = image_height - x[1]
            elif value == -90:
                x[0] = image_width - x[0]
            points.append((x[1], x[0]))
        self.label_info[filename]['aligned'] = points
        print(self.label_info[filename]['aligned'])
        self.load_file(self.filename, total_angle)

    def toggleAdvancedMode(self, value=True):
        self._beginner = not value
        self.canvas.setEditing(True)
        self.populateModeActions()
        self.editButton.setVisible(not value)
        if value:
            self.actions.createMode.setEnabled(True)
            self.actions.editMode.setEnabled(False)
            self.dock.setFeatures(self.dock.features() | self.dockFeatures)
        else:
            self.dock.setFeatures(self.dock.features() ^ self.dockFeatures)

    def populateModeActions(self):
        if self.beginner():
            tool, menu = self.actions.beginner, self.actions.beginnerContext
        else:
            tool, menu = self.actions.advanced, self.actions.advancedContext
        self.tools.clear()
        addActions(self.tools, tool)
        self.canvas.menus[0].clear()
        addActions(self.canvas.menus[0], menu)
        self.menus.edit.clear()
        actions = (self.actions.create,) if self.beginner()\
            else (self.actions.createMode, self.actions.editMode)
        addActions(self.menus.edit, actions + self.actions.editMenu)

    def setBeginner(self):
        self.tools.clear()
        addActions(self.tools, self.actions.beginner)

    def setAdvanced(self):
        self.tools.clear()
        addActions(self.tools, self.actions.advanced)

    def setDirty(self):
        self.dirty = True
        self.actions.save.setEnabled(True)

    def setClean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create.setEnabled(True)

    def toggleActions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)

    def queueEvent(self, function):
        QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def resetState(self):
        self.itemsToShapes.clear()
        self.shapesToItems.clear()
        self.labelList.clear()
        self.filepath = None
        self.image_data = None
        self.labelFile = None
        self.canvas.resetState()
        self.labelCoordinates.clear()

    def currentItem(self):
        items = self.labelList.selectedItems()
        if items:
            return items[0]
        return None

    def addRecentFile(self, filePath):
        if filePath in self.recentFiles:
            self.recentFiles.remove(filePath)
        elif len(self.recentFiles) >= self.maxRecent:
            self.recentFiles.pop()
        self.recentFiles.insert(0, filePath)

    def beginner(self):
        return self._beginner

    def advanced(self):
        return not self.beginner()

    def getAvailableScreencastViewer(self):
        os_name = platform.system()

        if os_name == 'Windows':
            return ['C:\\Program Files\\Internet Explorer\\iexplore.exe']
        elif os_name == 'Linux':
            return ['xdg-open']
        elif os_name == 'Darwin':
            return ['open', '-a', 'Safari']

    ## Callbacks ##
    def showTutorialDialog(self):
        subprocess.Popen(self.screencastViewer + [self.screencast])

    def showInfoDialog(self):
        msg = u'Name:{0} \nApp Version:{1} \n{2} '.format(__appname__, __version__, sys.version_info)
        QMessageBox.information(self, u'Information', msg)

    def createShape(self):
        assert self.beginner()
        self.canvas.setEditing(False)
        self.actions.create.setEnabled(False)

    def toggleDrawingSensitive(self, drawing=True):
        """In the middle of drawing, toggling between modes should be disabled."""
        self.actions.editMode.setEnabled(not drawing)
        if not drawing and self.beginner():
            # Cancel creation.
            print('Cancel creation.')
            self.canvas.setEditing(True)
            self.canvas.restoreCursor()
            self.actions.create.setEnabled(True)

    def toggleDrawMode(self, edit=True):
        self.canvas.setEditing(edit)
        self.actions.createMode.setEnabled(edit)
        self.actions.editMode.setEnabled(not edit)

    def setCreateMode(self):
        assert self.advanced()
        self.toggleDrawMode(False)

    def setEditMode(self):
        assert self.advanced()
        self.toggleDrawMode(True)
        self.labelSelectionChanged()

    def updateFileMenu(self):
        currFilePath = self.filepath

        def exists(filename):
            return os.path.exists(filename)
        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recentFiles if f !=
                 currFilePath and exists(f)]
        for i, f in enumerate(files):
            icon = newIcon('labels')
            action = QAction(
                icon, '&%d %s' % (i + 1, QFileInfo(f).fileName()), self)
            action.triggered.connect(partial(self.loadRecent, f))
            menu.addAction(action)

    def popLabelListMenu(self, point):
        self.menus.labelList.exec_(self.labelList.mapToGlobal(point))

    def editLabel(self):
        if not self.canvas.editing():
            return
        item = self.currentItem()
        shape = self.canvas.selectedShape
        if not item:
            return
        pos = shape.points[2]
        text = self.labelDialog.popUp(item.text(), pos=QPoint(pos.x() * shape.scale, pos.y() * shape.scale))
        if text is not None:
            item.setText(text)
            # item.setBackground(generateColorByText(text))
            self.setDirty()

    def save_label(self):
        filename = self.filename  # os.path.relpath(self.filepath, self.dirname)
        if filename not in self.label_info.keys():
            self.label_info[filename] = dict()
            self.label_info[filename]['rotation'] = 0
            self.label_info[filename]['photo_type'] = self.canvas.photo_type
        if self.phase == 0:
            self.aligned_points = np.array([(p.x(), p.y()) for p in self.canvas.shapes[0].points])
            self.label_info[filename]['aligned'] = self.aligned_points.tolist()
            self.label_info[filename]['photo_type'] = self.canvas.photo_type
        else:
            self.label_info[filename]['bb'] = [(s.label, [(p.x(), p.y()) for p in s.points]) for s in
                                               self.canvas.shapes]

    # Tzutalin 20160906 : Add file list and dock to move faster
    def fileitemDoubleClicked(self, item=None):
        self.save_label()
        self.canvas.set_loading(True)
        self.curr_index = self.mImgList.index(ustr(item.text()))
        if self.curr_index < len(self.mImgList):
            filename = self.mImgList[self.curr_index]
            if filename:
                if filename in self.label_info:
                    self.aligned_points = np.array(self.label_info[filename]['aligned'])
                    rotation = self.label_info[filename]['rotation']
                else:
                    rotation = None
                self.load_file(filename, rotation)

    # Add chris
    def btnstate(self, item= None):
        """ Function to handle difficult examples
        Update on each object """
        if not self.canvas.editing():
            return

        item = self.currentItem()
        if not item:  # If not selected Item, take the first one
            item = self.labelList.item(self.labelList.count()-1)

        difficult = self.diffcButton.isChecked()

        try:
            shape = self.itemsToShapes[item]
        except:
            pass
        # Checked and Update
        try:
            if difficult != shape.difficult:
                shape.difficult = difficult
                self.setDirty()
            else:  # User probably changed item visibility
                self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)
        except:
            pass

    # React to canvas signals.
    def shapeSelectionChanged(self, selected=False):
        if self._noSelectionSlot:
            self._noSelectionSlot = False
        else:
            shape = self.canvas.selectedShape
            if shape:
                self.shapesToItems[shape].setSelected(True)
            else:
                self.labelList.clearSelection()
        self.actions.delete.setEnabled(selected)
        self.actions.copy.setEnabled(selected)
        self.actions.edit.setEnabled(selected)
        self.actions.shapeLineColor.setEnabled(selected)
        self.actions.shapeFillColor.setEnabled(selected)

    def addLabel(self, shape):
        shape.paintLabel = self.displayLabelOption.isChecked()
        item = HashableQListWidgetItem(shape.label)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        # item.setBackground(generateColorByText(shape.label))
        self.itemsToShapes[item] = shape
        self.shapesToItems[shape] = item
        self.labelList.addItem(item)
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)

    def remLabel(self, shape):
        if shape is None:
            # print('rm empty label')
            return
        item = self.shapesToItems[shape]
        self.labelList.takeItem(self.labelList.row(item))
        del self.shapesToItems[shape]
        del self.itemsToShapes[item]

    def loadLabels(self, shapes):
        s = []
        for label, points, line_color, fill_color, difficult in shapes:
            shape = Shape(label=label)
            for x, y in points:
                # Ensure the labels are within the bounds of the image. If not, fix them.
                x, y, snapped = self.canvas.snapPointToCanvas(x, y)
                if snapped:
                    self.setDirty()
                shape.addPoint(QPointF(x, y))
            shape.difficult = difficult
            shape.close()
            s.append(shape)

            if line_color:
                shape.line_color = QColor(*line_color)
            else:
                shape.line_color = generateColorByText(label)

            if fill_color:
                shape.fill_color = QColor(*fill_color)
            else:
                shape.fill_color = generateColorByText(label)

            self.addLabel(shape)

        self.canvas.loadShapes(s)

    def showEvent(self, a0: QShowEvent):
        self.open_dir_dialog()
        self.showMaximized()

    @staticmethod
    def format_shape(s):
        return dict(label=s.label,
                    line_color=s.line_color.getRgb(),
                    fill_color=s.fill_color.getRgb(),
                    points=[(p.x(), p.y()) for p in s.points],
                    # add chris
                    difficult=s.difficult)

    def saveLabels(self, annotationFilePath):
        annotationFilePath = ustr(annotationFilePath)
        if self.labelFile is None:
            self.labelFile = LabelFile()
            self.labelFile.verified = self.canvas.verified

        shapes = [self.format_shape(shape) for shape in self.canvas.shapes]
        # Can add different annotation formats here
        try:
            if self.usingPascalVocFormat is True:
                if annotationFilePath[-4:].lower() != ".xml":
                    annotationFilePath += XML_EXT
                self.labelFile.savePascalVocFormat(annotationFilePath, shapes, self.filepath, self.image_data,
                                                   self.lineColor.getRgb(), self.fillColor.getRgb())
            elif self.usingYoloFormat is True:
                if annotationFilePath[-4:].lower() != ".txt":
                    annotationFilePath += TXT_EXT
                self.labelFile.saveYoloFormat(annotationFilePath, shapes, self.filepath, self.image_data, self.labelHist,
                                              self.lineColor.getRgb(), self.fillColor.getRgb())
            else:
                self.labelFile.save(annotationFilePath, shapes, self.filepath, self.image_data,
                                    self.lineColor.getRgb(), self.fillColor.getRgb())
            print('Image:{0} -> Annotation:{1}'.format(self.filepath, annotationFilePath))
            return True
        except LabelFileError as e:
            self.errorMessage(u'Error saving label data', u'<b>%s</b>' % e)
            return False

    def copySelectedShape(self):
        self.addLabel(self.canvas.copySelectedShape())
        # fix copy and delete
        self.shapeSelectionChanged(True)

    def labelSelectionChanged(self):
        item = self.currentItem()
        if item and self.canvas.editing():
            self._noSelectionSlot = True
            self.canvas.selectShape(self.itemsToShapes[item])
            shape = self.itemsToShapes[item]
            # Add Chris
            # self.diffcButton.setChecked(shape.difficult)

    def labelItemChanged(self, item):
        shape = self.itemsToShapes[item]
        label = item.text()
        if label != shape.label:
            shape.label = item.text()
            shape.line_color = generateColorByText(shape.label)
            self.setDirty()
        else:  # User probably changed item visibility
            self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)

    # Callback functions:
    def newShape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        # if not self.useDefaultLabelCheckbox.isChecked() or not self.defaultLabelTextLine.text():
        if len(self.labelHist) > 0:
            self.labelDialog = LabelDialog(
                parent=self, listItem=self.labelHist)

        # Sync single class mode from PR#106
        if self.singleClassMode.isChecked() and self.lastLabel:
            text = self.lastLabel
        else:
            text = self.labelDialog.popUp(text=self.prevLabelText)
            self.lastLabel = text
        # else:
        #     text = self.defaultLabelTextLine.text()

        # Add Chris
        # self.diffcButton.setChecked(False)
        if text is not None:
            self.prevLabelText = text
            generate_color = generateColorByText(text)
            shape = self.canvas.setLastLabel(text, generate_color, generate_color)
            self.addLabel(shape)
            if self.beginner():  # Switch to edit mode.
                self.canvas.setEditing(True)
                self.actions.create.setEnabled(True)
            else:
                self.actions.editMode.setEnabled(True)
            self.setDirty()

            if text not in self.labelHist:
                self.labelHist.append(text)
        else:
            # self.canvas.undoLastLine()
            self.canvas.resetAllLines()

    def scrollRequest(self, delta, orientation):
        units = - delta / (8 * 15)
        bar = self.scrollBars[orientation]
        bar.setValue(bar.value() + bar.singleStep() * units)

    def setZoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.MANUAL_ZOOM
        self.zoomWidget.setValue(value)

    def addZoom(self, increment=10):
        self.setZoom(self.zoomWidget.value() + increment)

    def zoomRequest(self, delta):
        # get the current scrollbar positions
        # calculate the percentages ~ coordinates
        h_bar = self.scrollBars[Qt.Horizontal]
        v_bar = self.scrollBars[Qt.Vertical]

        # get the current maximum, to know the difference after zooming
        h_bar_max = h_bar.maximum()
        v_bar_max = v_bar.maximum()

        # get the cursor position and canvas size
        # calculate the desired movement from 0 to 1
        # where 0 = move left
        #       1 = move right
        # up and down analogous
        cursor = QCursor()
        pos = cursor.pos()
        relative_pos = QWidget.mapFromGlobal(self, pos)

        cursor_x = relative_pos.x()
        cursor_y = relative_pos.y()

        w = self.scrollArea.width()
        h = self.scrollArea.height()

        # the scaling from 0 to 1 has some padding
        # you don't have to hit the very leftmost pixel for a maximum-left movement
        margin = 0.1
        move_x = (cursor_x - margin * w) / (w - 2 * margin * w)
        move_y = (cursor_y - margin * h) / (h - 2 * margin * h)

        # clamp the values from 0 to 1
        move_x = min(max(move_x, 0), 1)
        move_y = min(max(move_y, 0), 1)

        # zoom in
        units = delta / (8 * 15)
        scale = 10
        self.addZoom(scale * units)

        # get the difference in scrollbar values
        # this is how far we can move
        d_h_bar_max = h_bar.maximum() - h_bar_max
        d_v_bar_max = v_bar.maximum() - v_bar_max

        # get the new scrollbar values
        new_h_bar_value = h_bar.value() + move_x * d_h_bar_max
        new_v_bar_value = v_bar.value() + move_y * d_v_bar_max

        h_bar.setValue(new_h_bar_value)
        v_bar.setValue(new_v_bar_value)

    def setFitWindow(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoomMode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjustScale()

    def setFitWidth(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjustScale()

    def togglePolygons(self, value):
        for item, shape in self.itemsToShapes.items():
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def load_file(self, filename=None, rotation=None):
        self.canvas.set_loading(True)
        self.settings['curr_index'] = self.curr_index
        self.save_settings()
        print('Current index:', self.curr_index)
        self.fileListWidget.setCurrentRow(self.curr_index)

        file_path = self.dirname + filename
        """Load the specified file, or the last opened file if None."""
        if len(self.label_info.keys()) > 0:
            # label_info_filename = os.path.basename(self.dirname) + '.' + socket.gethostname() + '.' + os.getlogin()
            label_info_filepath = self.label_info_filepath
            # os.path.join(os.path.dirname(self.dirname), label_info_filename + '.json')
            with open(label_info_filepath, 'w') as fp:
                json.dump(self.label_info, fp)

        self.resetState()

        self.canvas.setEnabled(False)
        if file_path is None:
            file_path = self.settings.get(SETTING_FILENAME)

        # Make sure that file_path is a regular python string, rather than QString
        file_path = ustr(file_path)
        print(file_path)

        unicode_file_path = ustr(file_path)
        print(unicode_file_path)
        # Tzutalin 20160906 : Add file list and dock to move faster
        # Highlight the file item
        if unicode_file_path and self.fileListWidget.count() > 0:
            index = self.mImgList.index(filename)
            file_widget_item = self.fileListWidget.item(index)
            file_widget_item.setSelected(True)

        if unicode_file_path:  # and os.path.exists(unicode_file_path):
            # Load image:
            # read data first and store for saving into label file.
            if unicode_file_path[:4] == 'http':
                print("get response")
                response = self.session.get(unicode_file_path)
                print("done response")
                self.image_data = response.content
            else:
                self.image_data = read(unicode_file_path, None)

            if filename in self.label_info.keys():
                self.aligned_points = np.array(self.label_info[filename]['aligned'])
            elif (self.suggest_label_info is not None) and (filename in self.suggest_label_info.keys()):
                self.aligned_points = np.array(self.suggest_label_info[filename]['aligned'])

                self.label_info[filename] = self.suggest_label_info[filename]


            self.labelFile = None
            self.canvas.verified = False

            from PIL import Image
            import io
            image = Image.open(io.BytesIO(self.image_data))
            rotate_angle = 0
            if rotation is not None:
                rotate_angle = rotation
            elif filename in self.label_info.keys():
                rotate_angle = self.label_info[filename]['rotation']
            elif (self.suggest_label_info is not None) and (filename in self.suggest_label_info.keys()):
                rotate_angle = self.suggest_label_info[filename]['rotation']
            if rotate_angle != 0:
                rotate_angle = (rotate_angle + 360) % 360
                if rotate_angle == 90:
                    image = image.transpose(Image.ROTATE_270)
                elif rotate_angle == 270:
                    image = image.transpose(Image.ROTATE_90)
                elif rotate_angle == 180:
                    image = image.transpose(Image.ROTATE_180)
                else:
                    raise ValueError('Not support this angle')
                
            image = np.array(image)
            image_shape = image.shape
            image = np.require(image, np.uint8, 'C')
            self.original_image = image
            if self.phase == 1:
                image = four_point_transform(image, self.aligned_points)

            image = QImage(image.data, image.shape[1], image.shape[0], image.strides[0], QImage.Format_RGB888)
            # image = QImage.fromData(self.imageData)

            if image.isNull():
                self.errorMessage(u'Error opening file',
                                  u"<p>Make sure <i>%s</i> is a valid image file." % unicode_file_path)
                self.status("Error reading %s" % unicode_file_path)
                return False
            self.status("Loaded %s" % os.path.basename(unicode_file_path))
            self.image = image
            self.filepath = unicode_file_path
            self.filename = filename

            self.canvas.loadPixmap(QPixmap.fromImage(image))
            if self.labelFile:
                self.loadLabels(self.labelFile.shapes)
            elif self.phase == 0:
                if filename in self.label_info.keys():
                    shapes = [('', [(x[0], x[1]) for x in self.label_info[filename]['aligned']], None, None, False)]
                    self.canvas.photo_type = self.label_info[filename]['photo_type']
                else:
                    self.canvas.photo_type = 0
                    if self.suggest_corners[self.curr_index][1][0] == -1:
                        shapes = [('', [(10, 10), (image.width() - 20, 10), (image.width() - 20, image.height() - 20),
                                    (10, image.height() - 20)], None, None, False)]
                    else:
                        shapes = [('', [(x[0], x[1]) for x in self.suggest_corners[self.curr_index][1:]], None, None, False)]
                    print(self.curr_index)
                    print(self.suggest_corners[self.curr_index])
                    print(shapes)
                self.loadLabels(shapes)
            else:
                print('phase == 1')
                # filename = os.path.relpath(file_path, self.dirname)
                if (filename in self.label_info.keys()) and ('bb' in self.label_info[filename].keys()):
                    shapes = [(item[0], item[1], None, None, None) for item in self.label_info[filename]['bb']]
                    self.loadLabels(shapes)

            self.setClean()
            self.canvas.setEnabled(True)
            self.canvas.set_loading(False)
            self.adjustScale(initial=True)
            self.paintCanvas()
            self.addRecentFile(self.filepath)
            self.toggleActions(True)

            # Label xml file and show bound box according to its filename
            # if self.usingPascalVocFormat is True:
            if self.defaultSaveDir is not None:
                basename = os.path.basename(
                    os.path.splitext(self.filepath)[0])
                xmlPath = os.path.join(self.defaultSaveDir, basename + XML_EXT)
                txtPath = os.path.join(self.defaultSaveDir, basename + TXT_EXT)

                """Annotation file priority:
                PascalXML > YOLO
                """
                if os.path.isfile(xmlPath):
                    self.loadPascalXMLByFilename(xmlPath)
                elif os.path.isfile(txtPath):
                    self.loadYOLOTXTByFilename(txtPath)
            else:
                xmlPath = os.path.splitext(file_path)[0] + XML_EXT
                txtPath = os.path.splitext(file_path)[0] + TXT_EXT
                if os.path.isfile(xmlPath):
                    self.loadPascalXMLByFilename(xmlPath)
                elif os.path.isfile(txtPath):
                    self.loadYOLOTXTByFilename(txtPath)

            self.setWindowTitle(__appname__ + ' ' + file_path)

            # Default : select last item if there is at least one item
            if self.labelList.count():
                self.labelList.setCurrentItem(self.labelList.item(self.labelList.count()-1))
                self.labelList.item(self.labelList.count()-1).setSelected(True)

            self.canvas.setFocus(True)
            return True
        return False

    def resizeEvent(self, event):
        if self.canvas and not self.image.isNull()\
           and self.zoomMode != self.MANUAL_ZOOM:
            self.adjustScale()
        super(MainWindow, self).resizeEvent(event)

    def paintCanvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoomWidget.value()
        self.canvas.adjustSize()
        self.canvas.update()

    def adjustScale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoomMode]()
        self.zoomWidget.setValue(int(100 * value))

    def scaleFitWindow(self):
        """Figure out the size of the pixmap in order to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scaleFitWidth(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def closeEvent(self, event):
        self.save_settings()

    def save_settings(self):
        settings = self.settings
        # If it loads images from dir, don't load it at the begining
        if self.dirname is None:
            settings[SETTING_FILENAME] = self.filepath if self.filepath else ''
        else:
            settings[SETTING_FILENAME] = ''

        settings[SETTING_WIN_SIZE] = self.size()
        settings[SETTING_WIN_POSE] = self.pos()
        settings[SETTING_WIN_STATE] = self.saveState()
        settings[SETTING_LINE_COLOR] = self.lineColor
        settings[SETTING_FILL_COLOR] = self.fillColor
        settings[SETTING_RECENT_FILES] = self.recentFiles
        settings[SETTING_ADVANCE_MODE] = not self._beginner
        if self.defaultSaveDir and os.path.exists(self.defaultSaveDir):
            settings[SETTING_SAVE_DIR] = ustr(self.defaultSaveDir)
        else:
            settings[SETTING_SAVE_DIR] = ''

        if self.lastOpenDir and os.path.exists(self.lastOpenDir):
            settings[SETTING_LAST_OPEN_DIR] = self.lastOpenDir
        else:
            settings[SETTING_LAST_OPEN_DIR] = ''

        settings[SETTING_AUTO_SAVE] = self.autoSaving.isChecked()
        settings[SETTING_SINGLE_CLASS] = self.singleClassMode.isChecked()
        settings[SETTING_PAINT_LABEL] = self.displayLabelOption.isChecked()
        settings[SETTING_DRAW_SQUARE] = self.drawSquaresOption.isChecked()
        settings.save()

    def loadRecent(self, filename):
        if self.mayContinue():
            self.load_file(filename)

    def scanAllImages(self, folderPath):
        extensions = ['.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        images = []

        for root, dirs, files in os.walk(folderPath):
            for file in files:
                if file.lower().endswith(tuple(extensions)):
                    relativePath = os.path.join(root, file)
                    path = ustr(os.path.abspath(relativePath))
                    images.append(path)
        natural_sort(images, key=lambda x: x.lower())
        return images

    def changeSavedirDialog(self, _value=False):
        if self.defaultSaveDir is not None:
            path = ustr(self.defaultSaveDir)
        else:
            path = '.'

        dirpath = ustr(QFileDialog.getExistingDirectory(self,
                                                       '%s - Save annotations to the directory' % __appname__, path,  QFileDialog.ShowDirsOnly
                                                       | QFileDialog.DontResolveSymlinks))

        if dirpath is not None and len(dirpath) > 1:
            self.defaultSaveDir = dirpath

        self.statusBar().showMessage('%s . Annotation will be saved to %s' %
                                     ('Change saved folder', self.defaultSaveDir))
        self.statusBar().show()

    def openAnnotationDialog(self, _value=False):
        if self.filepath is None:
            self.statusBar().showMessage('Please select image first')
            self.statusBar().show()
            return

        path = os.path.dirname(ustr(self.filepath))\
            if self.filepath else '.'
        if self.usingPascalVocFormat:
            filters = "Open Annotation XML file (%s)" % ' '.join(['*.xml'])
            filename = ustr(QFileDialog.getOpenFileName(self,'%s - Choose a xml file' % __appname__, path, filters))
            if filename:
                if isinstance(filename, (tuple, list)):
                    filename = filename[0]
            self.loadPascalXMLByFilename(filename)

    def open_dir_dialog(self):
        continue_condition = True
        while continue_condition:
            images_dir = self.settings.get('images_dir', default= "https://192.168.1.184:48000/user/$user/files/working/common/hotdata/VNIDCards/data01/images/")
            # bbox_filename = self.settings.get('bbox_filename', default="https://202.161.73.78:18008/user/$user/files/working/common/hotdata/VNIDCards/data02/idcorners_splited_csv/xxx.csv")
            bbox_filename = self.settings.get('bbox_filename', default="https://192.168.1.184:48000/user/$user/files/working/common/hotdata/VNIDCards/data02/idcorner_labels/Khanh.json")
            images_dir, bbox_filename, username, password, label_filename, ok = \
                OpenLabelDialog.get_result(images_dir, bbox_filename)
            self.settings['images_dir'] = images_dir
            self.settings['bbox_filename'] = bbox_filename
            print(images_dir, bbox_filename, ok)
            if not ok:
                return False
            self.canvas.set_loading(True)
            self.label_info_filepath = label_filename
            if os.path.exists(self.label_info_filepath):
                self.label_info = json.load(open(self.label_info_filepath))
            else:
                self.label_info = dict()
            print(self.label_info)
            if self.import_dir_images(images_dir, bbox_filename, username, password):
                continue_condition = False
        return True

    def import_dir_images(self, images_dir, bbox_filename, username, password):
        if bbox_filename != "":
            if bbox_filename[:4] == 'http':
                from urllib.parse import urlparse
                # from urlparse import urlparse  # Python 2
                parsed_uri = urlparse(images_dir)
                hostname = '{uri.scheme}://{uri.netloc}/'.format(uri=parsed_uri)
                print(hostname)
                self.session = requests.Session()
                data = {'username': username, 'password': password}
                r = self.session.post(hostname+'hub/login', data=data, verify=False)
                if '<button id="logout" class="btn btn-sm navbar-btn">Logout</button>' not in r.text:
                    msg = QMessageBox()
                    msg.setIcon(QMessageBox.Information)

                    msg.setText("Could not login to server")
                    msg.setWindowTitle("Login error")
                    msg.setDetailedText(r.text)
                    msg.setStandardButtons(QMessageBox.Ok)
                    retval = msg.exec_()
                    return False

                r = self.session.get(bbox_filename, verify=False)
                r.encoding = 'utf-8'
                import io
                if '<h1>404 : Not Found</h1>' in r.text:
                    msg = QMessageBox()
                    msg.setIcon(QMessageBox.Information)

                    msg.setText("File " + bbox_filename + " is not found")
                    msg.setWindowTitle("File error")
                    msg.setDetailedText(r.text)
                    msg.setStandardButtons(QMessageBox.Ok)
                    retval = msg.exec_()
                    return False
                if bbox_filename[-3:] == 'csv':
                    df = pd.read_csv(io.StringIO(r.text), low_memory=False)
                    self.mImgList = [st for st in df['file_path'].values]
                    self.suggest_corners = [
                        [0, (x[0], x[1]), (x[2], x[3]), (x[4], x[5]), (x[6], x[7])]
                        for x in df[
                            ['top-left-y', 'top-left-x', 'top-right-y', 'top-right-x',
                             'bottom-right-y', 'bottom-right-x', 'bottom-left-y', 'bottom-left-x']].values
                    ]
                elif bbox_filename[-4:] == 'json':
                    self.suggest_label_info = json.loads(r.text)
                    self.mImgList = []
                    self.suggest_corners = []
                    for name, info in self.suggest_label_info.items():
                        if info['photo_type'] != 0:
                            continue
                        self.mImgList.append(name)
                        self.suggest_corners.append([
                                info['rotation'], tuple(info["aligned"][0]), tuple(info["aligned"][1]),
                                tuple(info["aligned"][2]), tuple(info["aligned"][3])
                        ])
                else:
                    msg = QMessageBox()
                    msg.setIcon(QMessageBox.Information)

                    msg.setText("Only support .csv or .json files")
                    msg.setWindowTitle("Get bounding box file error")
                    msg.setDetailedText(r.text)
                    msg.setStandardButtons(QMessageBox.Ok)
                    retval = msg.exec_()
                    return False
                if self.start_idx != -1 and self.end_idx != -1:
                    self.mImgList = self.mImgList[self.start_idx:self.end_idx]
                    self.suggest_corners = self.suggest_corners[self.start_idx:self.end_idx]
                # elif self.phase == 0:
                #     try:
                #         annotated_files = list(self.label_info.keys())
                #         annotated_ids = [self.mImgList.index(f) for f in annotated_files]
                #         unannotated_ids = list(set(range(len(self.mImgList))) - set(annotated_ids))
                #         ids = annotated_ids + unannotated_ids
                #         self.mImgList = list(np.array(self.mImgList)[ids])
                #         self.suggest_corners = list(np.array(self.suggest_corners)[ids])
                #         self.settings['curr_index'] = len(annotated_ids)
                #     except Exception as error:
                #         print(error)
                print(self.suggest_corners[:5])
                print(self.mImgList[:5])
        else:
            self.lastOpenDir = images_dir

            self.filepath = None
            self.filename = None
            self.fileListWidget.clear()
            self.mImgList = self.scanAllImages(images_dir)

        self.dirname = images_dir
        self.open_next_img()
        for imgPath in self.mImgList:
            item = QListWidgetItem(imgPath)
            self.fileListWidget.addItem(item)
        self.fileListWidget.setCurrentRow(self.curr_index)
        return True

    def verifyImg(self, _value=False):
        # Proceding next image without dialog if having any label
        if self.filepath is not None:
            try:
                self.labelFile.toggleVerify()
            except AttributeError:
                # If the labelling file does not exist yet, create if and
                # re-save it with the verified attribute.
                self.saveFile()
                if self.labelFile != None:
                    self.labelFile.toggleVerify()
                else:
                    return

            self.canvas.verified = self.labelFile.verified
            self.paintCanvas()
            self.saveFile()

    def open_previous_img(self, _value=False):
        # Proceding prev image without dialog if having any label
        # if self.autoSaving.isChecked():
        #     if self.defaultSaveDir is not None:
        #         if self.dirty is True:
        #             self.saveFile()
        #     else:
        #         self.changeSavedirDialog()
        #         return

        if self.filepath is not None:
            if not self.mayContinue():
                return

        if len(self.mImgList) <= 0:
            return

        if self.filepath is None:
            return
        self.save_label()
        self.curr_index = self.mImgList.index(self.filename)
        if self.curr_index - 1 >= 0:
            self.curr_index -= 1
            filename = self.mImgList[self.curr_index]
            if filename:
                self.save_status = False
                self.load_file(filename)

    def open_next_img(self):
        # Processing prev image without dialog if having any label
        # if self.autoSaving.isChecked():
        #     if self.defaultSaveDir is not None:
        #         if self.dirty is True:
        #             self.saveFile()
        #     else:
        #         self.changeSavedirDialog()
        #         return

        if self.filepath is not None:
            if not self.mayContinue():
                return

        if len(self.mImgList) <= 0:
            return
        filename = None
        if self.filepath is None:
            if self.settings.get('curr_index') is None:
                self.curr_index = 0
            else:
                self.curr_index = self.settings['curr_index']
            filename = self.mImgList[self.curr_index]
        else:
            self.save_label()
            self.curr_index = self.mImgList.index(self.filename)
            if self.curr_index + 1 < len(self.mImgList):
                self.curr_index += 1
                filename = self.mImgList[self.curr_index]
        if filename:
            self.save_status = False
            self.load_file(filename)

    def openFile(self, _value=False):
        if not self.mayContinue():
            return
        self.save_status = False
        path = os.path.dirname(ustr(self.filepath)) if self.filepath else '.'
        formats = ['*.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        filters = "Image & Label files (%s)" % ' '.join(formats + ['*%s' % LabelFile.suffix])
        filename = QFileDialog.getOpenFileName(self, '%s - Choose Image or Label file' % __appname__, path, filters)
        if filename:
            if isinstance(filename, (tuple, list)):
                filename = filename[0]
            self.load_file(filename)

    def saveFile(self, _value=False):
        if self.defaultSaveDir is not None and len(ustr(self.defaultSaveDir)):
            if self.filepath:
                imgFileName = os.path.basename(self.filepath)
                savedFileName = os.path.splitext(imgFileName)[0]
                savedPath = os.path.join(ustr(self.defaultSaveDir), savedFileName)
                self._saveFile(savedPath)
        else:
            imgFileDir = os.path.dirname(self.filepath)
            imgFileName = os.path.basename(self.filepath)
            savedFileName = os.path.splitext(imgFileName)[0]
            savedPath = os.path.join(imgFileDir, savedFileName)
            self._saveFile(savedPath if self.labelFile
                           else self.saveFileDialog(removeExt=False))

    def saveFileAs(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._saveFile(self.saveFileDialog())

    def saveFileDialog(self, removeExt=True):
        caption = '%s - Choose File' % __appname__
        filters = 'File (*%s)' % LabelFile.suffix
        openDialogPath = self.currentPath()
        dlg = QFileDialog(self, caption, openDialogPath, filters)
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        filenameWithoutExtension = os.path.splitext(self.filepath)[0]
        dlg.selectFile(filenameWithoutExtension)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)
        if dlg.exec_():
            fullFilePath = ustr(dlg.selectedFiles()[0])
            if removeExt:
                return os.path.splitext(fullFilePath)[0] # Return file path without the extension.
            else:
                return fullFilePath
        return ''

    def _saveFile(self, annotationFilePath):
        if annotationFilePath and self.saveLabels(annotationFilePath):
            self.setClean()
            self.statusBar().showMessage('Saved to  %s' % annotationFilePath)
            self.statusBar().show()

    def closeFile(self, _value=False):
        if not self.mayContinue():
            return
        self.resetState()
        self.setClean()
        self.toggleActions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    def resetAll(self):
        self.settings.reset()
        self.close()
        proc = QProcess()
        proc.startDetached(os.path.abspath(__file__))

    def mayContinue(self):
        return not (not self.save_status and not self.discardChangesDialog())

    def discardChangesDialog(self):
        yes, no = QMessageBox.Yes, QMessageBox.No
        msg = u'Do you finish labelling this image?'
        return yes == QMessageBox.warning(self, u'Attention', msg, yes | no, defaultButton=yes)

    def errorMessage(self, title, message):
        return QMessageBox.critical(self, title,
                                    '<p><b>%s</b></p>%s' % (title, message))

    def currentPath(self):
        return os.path.dirname(self.filepath) if self.filepath else '.'

    def chooseColor1(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.lineColor = color
            Shape.line_color = color
            self.canvas.setDrawingColor(color)
            self.canvas.update()
            self.setDirty()

    def deleteSelectedShape(self):
        self.remLabel(self.canvas.deleteSelected())
        self.setDirty()
        if self.no_shapes():
            for action in self.actions.onShapesPresent:
                action.setEnabled(False)

    def chshapeLineColor(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.canvas.selectedShape.line_color = color
            self.canvas.update()
            self.setDirty()

    def chshapeFillColor(self):
        color = self.colorDialog.getColor(self.fillColor, u'Choose fill color',
                                          default=DEFAULT_FILL_COLOR)
        if color:
            self.canvas.selectedShape.fill_color = color
            self.canvas.update()
            self.setDirty()

    def copyShape(self):
        self.canvas.endMove(copy=True)
        self.addLabel(self.canvas.selectedShape)
        self.setDirty()

    def moveShape(self):
        self.canvas.endMove(copy=False)
        self.setDirty()

    def loadPredefinedClasses(self, predefClassesFile):
        if os.path.exists(predefClassesFile) is True:
            with codecs.open(predefClassesFile, 'r', 'utf8') as f:
                for line in f:
                    line = line.strip()
                    if self.labelHist is None:
                        self.labelHist = [line]
                    else:
                        self.labelHist.append(line)

    def loadPascalXMLByFilename(self, xmlPath):
        if self.filepath is None:
            return
        if os.path.isfile(xmlPath) is False:
            return

        self.set_format(FORMAT_PASCALVOC)

        tVocParseReader = PascalVocReader(xmlPath)
        shapes = tVocParseReader.getShapes()
        self.loadLabels(shapes)
        self.canvas.verified = tVocParseReader.verified

    def loadYOLOTXTByFilename(self, txtPath):
        if self.filepath is None:
            return
        if os.path.isfile(txtPath) is False:
            return

        self.set_format(FORMAT_YOLO)
        tYoloParseReader = YoloReader(txtPath, self.image)
        shapes = tYoloParseReader.getShapes()
        print (shapes)
        self.loadLabels(shapes)
        self.canvas.verified = tYoloParseReader.verified

    def togglePaintLabelsOption(self):
        for shape in self.canvas.shapes:
            shape.paintLabel = self.displayLabelOption.isChecked()

    def toogleDrawSquare(self):
        self.canvas.setDrawingShapeToSquare(self.drawSquaresOption.isChecked())

def inverted(color):
    return QColor(*[255 - v for v in color.getRgb()])


def read(filename, default=None):
    try:
        with open(filename, 'rb') as f:
            return f.read()
    except:
        return default


def get_main_app(argv=[]):
    """
    Standard boilerplate Qt application code.
    Do everything but app.exec_() -- so that we can test the application in one thread
    """
    app = QApplication(argv)
    app.setApplicationName(__appname__)
    app.setWindowIcon(newIcon("app"))
    # Tzutalin 201705+: Accept extra agruments to change predefined class file
    # Usage : annotate.py image predefClassFile saveDir
    # win = MainWindow(argv[1] if len(argv) >= 2 else None,
    #                  argv[2] if len(argv) >= 3 else os.path.join(
    #                      os.path.dirname(sys.argv[0]),
    #                      'data', 'predefined_classes.txt'),
    #                  argv[3] if len(argv) >= 4 else None)
    win = MainWindow(
        default_prefdef_classfile=os.path.join(os.path.dirname(sys.argv[0]), 'data', 'predefined_classes.txt'),
        phase=int(argv[1]) if len(argv) >= 2 else 1,
        user_scale=float(argv[2]) if len(argv) >= 3 else 0,
        start_idx=int(argv[3]) if len(argv) >= 4 else -1,
        end_idx=int(argv[4]) if len(argv) >= 5 else -1
    )
    win.show()
    return app, win


def main():
    """
    construct main app and run it
    :return:
    """
    app, _win = get_main_app(sys.argv)
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
