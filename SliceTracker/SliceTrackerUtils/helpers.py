import logging
import os
import datetime
import qt
import vtk
import re
import slicer

from .constants import SliceTrackerConstants as constants

from SlicerDevelopmentToolboxUtils.decorators import logmethod
from SlicerDevelopmentToolboxUtils.widgets import ExtendedQMessageBox
from SlicerDevelopmentToolboxUtils.module.logic import LogicBase
from SlicerDevelopmentToolboxUtils.module.base import ModuleBase
from SlicerDevelopmentToolboxUtils.mixins import ModuleWidgetMixin
from SlicerDevelopmentToolboxUtils.metaclasses import Singleton
from SlicerDevelopmentToolboxUtils.icons import Icons


class NewCaseSelectionNameWidget(qt.QMessageBox, ModuleWidgetMixin):

  PREFIX = "Case"
  SUFFIX = "-" + datetime.date.today().strftime("%Y%m%d")
  SUFFIX_PATTERN = "-[0-9]{8}"

  def __init__(self, destination, parent=None):
    super(NewCaseSelectionNameWidget, self).__init__(parent)
    self.CASE_NUMBER_OF_DIGITS = int(self.getSetting("CASE_NUMBER_OF_DIGITS", moduleName="SliceTracker"))
    self.PATTERN = self.PREFIX +"[0-9]{" + str(self.CASE_NUMBER_OF_DIGITS - 1) + "}[0-9]{1}" + self.SUFFIX_PATTERN

    if not os.path.exists(destination):
      raise OSError("Directory %s doesn't exist" % destination)
    self.destinationRoot = destination
    self.newCaseDirectory = None
    self.minimum = self.getNextCaseNumber()
    self.setupUI()
    self.setupConnections()
    self.onCaseNumberChanged(self.minimum)

  def getNextCaseNumber(self):
    caseNumber = 0
    for dirName in [dirName for dirName in os.listdir(self.destinationRoot)
                     if os.path.isdir(os.path.join(self.destinationRoot, dirName)) and re.match(self.PATTERN, dirName)]:
      number = int(re.split(self.SUFFIX_PATTERN, dirName)[0].split(self.PREFIX)[1])
      caseNumber = caseNumber if caseNumber > number else number
    return caseNumber+1

  def setupUI(self):
    self.setWindowTitle("Case Number Selection")
    self.spinbox = qt.QSpinBox()
    self.spinbox.setRange(self.minimum, int("9" * self.CASE_NUMBER_OF_DIGITS))

    self.hideInvisibleUnneededComponents()

    self.textLabel = qt.QLabel("Please select a case number for the new case.")
    self.textLabel.setStyleSheet("font-weight: bold;")
    self.previewLabel = qt.QLabel("New case directory:")
    self.preview = qt.QLabel()
    self.notice = qt.QLabel()
    self.notice.setStyleSheet("color:red;")

    self.okButton = self.addButton(self.Ok)
    self.okButton.enabled = False
    self.cancelButton = self.addButton(self.Cancel)
    self.setDefaultButton(self.okButton)

    self.groupBox = qt.QGroupBox()
    self.groupBox.setLayout(qt.QGridLayout())
    self.groupBox.layout().addWidget(self.textLabel, 0, 0, 1, 2)
    self.groupBox.layout().addWidget(qt.QLabel("Proposed Case Number"), 1, 0)
    self.groupBox.layout().addWidget(self.spinbox, 1, 1)
    self.groupBox.layout().addWidget(self.previewLabel, 2, 0, 1, 2)
    self.groupBox.layout().addWidget(self.preview, 3, 0, 1, 2)
    self.groupBox.layout().addWidget(self.notice, 4, 0, 1, 2)

    self.groupBox.layout().addWidget(self.okButton, 5, 0)
    self.groupBox.layout().addWidget(self.cancelButton, 5, 1)

    self.layout().addWidget(self.groupBox, 1, 1)

  def hideInvisibleUnneededComponents(self):
    for oName in ["qt_msgbox_label", "qt_msgboxex_icon_label"]:
      try:
        slicer.util.findChild(self, oName).hide()
      except RuntimeError:
        pass

  def setupConnections(self):
    self.spinbox.valueChanged.connect(self.onCaseNumberChanged)

  def onCaseNumberChanged(self, caseNumber):
    formatString = '%0' + str(self.CASE_NUMBER_OF_DIGITS) + 'd'
    caseNumber = formatString % caseNumber
    directory = self.PREFIX+caseNumber+self.SUFFIX
    self.newCaseDirectory = os.path.join(self.destinationRoot, directory)
    self.preview.setText( self.newCaseDirectory)
    self.okButton.enabled = not os.path.exists(self.newCaseDirectory)
    self.notice.text = "" if not os.path.exists(self.newCaseDirectory) else "Note: Directory already exists."


class SeriesTypeManager(LogicBase):

  SeriesTypeManuallyAssignedEvent = vtk.vtkCommand.UserEvent + 2334

  MODULE_NAME = constants.MODULE_NAME

  __metaclass__ = Singleton

  assignedSeries = {}

  def __init__(self):
    LogicBase.__init__(self)
    self.seriesTypes = self.getSetting("SERIES_TYPES")

  def clear(self):
    self.assignedSeries = {}

  def getSeriesType(self, series):
    try:
      return self.assignedSeries[series]
    except KeyError:
      self.assignedSeries[series] = self.computeSeriesType(series)
    return self.assignedSeries[series]

  def computeSeriesType(self, series):
    if self.getSetting("COVER_PROSTATE_PATTERN") in series:
      seriesType = constants.COVER_PROSTATE
    elif self.getSetting("COVER_TEMPLATE_PATTERN") in series:
      seriesType = constants.COVER_TEMPLATE
    elif self.getSetting("NEEDLE_IMAGE_PATTERN") in series:
      seriesType = constants.GUIDANCE_IMAGE
    elif self.getSetting("VIBE_IMAGE_PATTERN") in series:
      seriesType = constants.VIBE_IMAGE
    else:
      seriesType = constants.OTHER_IMAGE
    return seriesType

  def autoAssign(self, series):
    self.assignedSeries[series] = self.getSeriesType(series)

  def assign(self, series, seriesType=None):
    if series in self.assignedSeries.keys() and self.assignedSeries[series] == seriesType:
      return
    if seriesType:
      assert seriesType in self.seriesTypes
      self.assignedSeries[series] = seriesType
      self.invokeEvent(self.SeriesTypeManuallyAssignedEvent)
    else:
      self.autoAssign(series)

  def isCoverProstate(self, series):
    return self._hasSeriesType(series, constants.COVER_PROSTATE)

  def isCoverTemplate(self, series):
    return self._hasSeriesType(series, constants.COVER_TEMPLATE)

  def isGuidance(self, series):
    return self._hasSeriesType(series, constants.GUIDANCE_IMAGE)

  def isVibe(self, series):
    return self._hasSeriesType(series, constants.VIBE_IMAGE)

  def isOther(self, series):
    return self._hasSeriesType(series, constants.OTHER_IMAGE) or not (self.isCoverProstate(series) or
                                                                      self.isCoverTemplate(series) or
                                                                      self.isGuidance(series) or
                                                                      self.isVibe(series))

  def _hasSeriesType(self, series, seriesType):
    return self.getSeriesType(series) == seriesType


class IncomingDataMessageBox(ExtendedQMessageBox):

  def __init__(self, parent=None):
    super(IncomingDataMessageBox, self).__init__(parent)
    self.setWindowTitle("Incoming image data")
    self.setText("New data has been received. What do you want do?")
    self.setIcon(qt.QMessageBox.Question)
    trackButton = self.addButton(qt.QPushButton('Track targets'), qt.QMessageBox.AcceptRole)
    self.addButton(qt.QPushButton('Postpone'), qt.QMessageBox.NoRole)
    self.setDefaultButton(trackButton)


class SeriesTypeToolButton(qt.QToolButton, ModuleBase, ModuleWidgetMixin):

  MODULE_NAME = constants.MODULE_NAME

  class SeriesTypeListWidget(qt.QListWidget, ModuleWidgetMixin):

    @property
    def series(self):
      return self._series

    @series.setter
    def series(self, value):
      self._series = value
      self._preselectSeriesType()

    def __init__(self, series=None):
      qt.QListWidget.__init__(self)
      self.seriesTypeManager = SeriesTypeManager()
      self._series = series
      self.setup()
      self.setupConnections()

    def setup(self):
      self.clear()
      for index, seriesType in enumerate(self.seriesTypeManager.seriesTypes):
        self.addItem(seriesType)
      self.setFixedSize(self.sizeHintForColumn(0) + 2 * self.frameWidth,
                        self.sizeHintForRow(0) * self.count + 2 * self.frameWidth)
      self._preselectSeriesType()

    def _preselectSeriesType(self):
      if not self._series:
        self.setCurrentItem(None)
      seriesType = self.seriesTypeManager.getSeriesType(self._series)
      for index in range(self.count):
        if seriesType == self.item(index).text():
          self.setCurrentItem(self.item(index))
          return

    def setupConnections(self):
      self.itemSelectionChanged.connect(self.onSelectionChanged)

    def onSelectionChanged(self):
      currentSeriesType = self.seriesTypeManager.getSeriesType(self._series)
      selectedSeriesType = self.selectedItems()[0].text()
      if selectedSeriesType != currentSeriesType:
        if slicer.util.confirmYesNoDisplay("You are about to change the series type for series:\n\n"
                                           "Name: {0} \n\n"
                                           "Current series type: {1} \n"
                                           "New series type: {2} \n\n"
                                           "This could have significant impact on the workflow.\n\n"
                                           "Do you want to proceed?".format(self._series, currentSeriesType,
                                                                            selectedSeriesType)):
          self.seriesTypeManager.assign(self._series, self.selectedItems()[0].text())
        else:
          self._preselectSeriesType()

  def __init__(self):
    qt.QToolButton.__init__(self)
    ModuleBase.__init__(self)
    self.setPopupMode(self.InstantPopup)
    self.setMenu(qt.QMenu(self))
    self.action = qt.QWidgetAction(self)
    self.listWidget = None
    self.setIcon(Icons.edit)
    self.enabled = False

  @logmethod(logging.DEBUG)
  def setSeries(self, series):
    if not self.listWidget:
      self.listWidget = self.SeriesTypeListWidget(series)
      self.action.setDefaultWidget(self.listWidget)
      self.menu().addAction(self.action)
      seriesTypeManager = SeriesTypeManager()
      seriesTypeManager.addEventObserver(seriesTypeManager.SeriesTypeManuallyAssignedEvent,
                                         lambda caller, event: self.menu().close())
    else:
      self.listWidget.series = series
    self.updateTooltipAndIcon(series)

  def updateTooltipAndIcon(self, series):
    seriesTypeManager = SeriesTypeManager()
    currentType = seriesTypeManager.getSeriesType(series)
    autoType = seriesTypeManager.computeSeriesType(series)
    if currentType != autoType:
      icon = Icons.info
      tooltip = "Series type assignment changed!\n\n" \
                "Auto-calculated series type: {}\n\n" \
                "Assigned series type: {}".format(autoType, currentType)
    else:
      icon = Icons.edit
      tooltip = self.listWidget.currentItem().text() if self.listWidget.currentItem() else ""
    self.setIcon(icon)
    self.setToolTip(tooltip)
