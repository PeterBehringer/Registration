import csv, re, numpy, json, ast, re
import shutil, datetime, logging
import ctk, vtk, qt
from collections import OrderedDict

import SimpleITK as sitk
import sitkUtils

from slicer.ScriptedLoadableModule import *

import EditorLib
from Editor import EditorWidget

from SlicerProstateUtils.buttons import *
from SlicerProstateUtils.constants import DICOMTAGS, COLOR, STYLE, FileExtension
from SlicerProstateUtils.helpers import SampleDataDownloader, SmartDICOMReceiver, SliceAnnotation, TargetCreationWidget
from SlicerProstateUtils.helpers import RatingWindow, IncomingDataMessageBox, IncomingDataWindow
from SlicerProstateUtils.helpers import WatchBoxAttribute, BasicInformationWatchBox, DICOMBasedInformationWatchBox
from SlicerProstateUtils.mixins import ModuleWidgetMixin, ModuleLogicMixin, ParameterNodeObservationMixin
from SlicerProstateUtils.events import SlicerProstateEvents
from SlicerProstateUtils.decorators import onReturnProcessEvents, beforeRunProcessEvents

from SliceTrackerUtils.constants import SliceTrackerConstants
from SliceTrackerUtils.exceptions import DICOMValueError
from SliceTrackerUtils.RegistrationData import RegistrationResults, RegistrationResult
from SliceTrackerUtils.ZFrameRegistration import *
from SliceTrackerUtils.configuration import SliceTrackerConfiguration

from SliceTrackerRegistration import SliceTrackerRegistrationLogic

from VolumeClipToLabel import VolumeClipToLabelWidget


class SliceTracker(ScriptedLoadableModule):

  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "SliceTracker"
    self.parent.categories = ["Radiology"]
    self.parent.dependencies = ["SlicerProstate", "mpReview", "mpReviewPreprocessor"]
    self.parent.contributors = ["Christian Herz (SPL), Peter Behringer (SPL), Andriy Fedorov (SPL)"]
    self.parent.helpText = """ SliceTracker facilitates support of MRI-guided targeted prostate biopsy. See <a href=\"https://www.gitbook.com/read/book/fedorov/slicetracker\">the documentation</a> for details."""
    self.parent.acknowledgementText = """Surgical Planning Laboratory, Brigham and Women's Hospital, Harvard
                                          Medical School, Boston, USA This work was supported in part by the National
                                          Institutes of Health through grants U24 CA180918,
                                          R01 CA111288 and P41 EB015898."""


class SliceTrackerWidget(ModuleWidgetMixin, SliceTrackerConstants, ScriptedLoadableModuleWidget):

  @property
  def registrationResults(self):
    return self.logic.registrationResults

  @property
  def currentResult(self):
    return self.registrationResults.activeResult

  @currentResult.setter
  def currentResult(self, series):
    self.registrationResults.activeResult = series

  @property
  def caseRootDir(self):
    return self.casesRootDirectoryButton.directory

  @caseRootDir.setter
  def caseRootDir(self, path):
    try:
      exists = os.path.exists(path)
    except TypeError:
      exists = False
    self.setSetting('CasesRootLocation', path if exists else None)
    self.casesRootDirectoryButton.text = self.truncatePath(path) if exists else "Choose output directory"
    self.casesRootDirectoryButton.toolTip = path
    self.openCaseButton.enabled = exists
    self.createNewCaseButton.enabled = exists

  @property
  def preopDataDir(self):
    return self._preopDataDir

  @preopDataDir.setter
  def preopDataDir(self, path):
    self._preopDataDir = path
    if path is None:
      return
    if os.path.exists(path):
      self.loadPreopData()

  @property
  def intraopDataDir(self):
    return self.logic.intraopDataDir

  @intraopDataDir.setter
  def intraopDataDir(self, path):
    if os.path.exists(path):
      self.intraopWatchBox.sourceFile = None
      self.logic.removeEventObservers()
      self.logic.addEventObserver(SlicerProstateEvents.StatusChangedEvent, self.onDICOMReceiverStatusChanged)
      self.logic.addEventObserver(SlicerProstateEvents.DICOMReceiverStoppedEvent, self.onIntraopDICOMReceiverStopped)
      self.logic.addEventObserver(SlicerProstateEvents.NewImageDataReceivedEvent, self.onNewImageDataReceived)
      self.logic.addEventObserver(SlicerProstateEvents.NewFileIndexedEvent, self.onNewFileIndexed)
      self.logic.intraopDataDir = path
      self.closeCaseButton.enabled = True

  @property
  def currentStep(self):
    return self.SLICETRACKER_STEPS[self._currentStep]

  @currentStep.setter
  def currentStep(self, name):
    assert name in self.SLICETRACKER_STEPS
    self._currentStep = self.SLICETRACKER_STEPS.index(name)

    self.targetTable.disconnect('doubleClicked(QModelIndex)', self.onMoveTargetRequest)
    self.disableTargetMovingMode()
    self.resetViewSettingButtons()

    if name == self.STEP_OVERVIEW:
      self.registrationEvaluationButtonsGroupBox.hide()
      self.registrationEvaluationGroupBox.hide()
      self.registrationResultsGroupBox.hide()
      self.zFrameRegistrationGroupBox.hide()
      self.segmentationGroupBox.hide()
      self.targetingGroupBox.hide()
      self.overviewGroupBox.show()
      self.overviewGroupBoxLayout.addWidget(self.targetTable, 3, 0, 1, 2)
    elif name == self.STEP_ZFRAME_REGISTRATION:
      self.overviewGroupBox.hide()
      self.zFrameRegistrationGroupBox.show()

      self.zFrameRegistrationManualIndexesGroupBox.visible = self.zFrameRegistrationClass is OpenSourceZFrameRegistration
      self.zFrameRegistrationManualIndexesGroupBox.checked = False
      self.applyZFrameRegistrationButton.enabled = self.zFrameRegistrationClass is LineMarkerRegistration
      self.retryZFrameRegistrationButton.visible = self.zFrameRegistrationClass is OpenSourceZFrameRegistration

      self.showZFrameModelButton.checked = True
      self.showTemplateButton.checked = True
      self.showTemplatePathButton.checked = True
    elif name == self.STEP_SEGMENTATION:
      self.redOnlyLayoutButton.enabled = False
      self.sideBySideLayoutButton.enabled = True
      self.registrationEvaluationButtonsGroupBox.hide()
      self.registrationEvaluationGroupBox.hide()
      self.registrationResultsGroupBox.hide()
      self.overviewGroupBox.hide()
      self.segmentationGroupBox.show()
      self.editorWidgetButton.enabled = False
      self.applyRegistrationButton.enabled = False
    elif name == self.STEP_TARGETING:
      self.segmentationGroupBox.hide()
      self.targetingGroupBox.show()
    elif name == self.STEP_SEGMENTATION_COMPARISON:
      # TODO: Apply registration Button should be visible here
      pass
    elif name == self.STEP_EVALUATION:
      self.redOnlyLayoutButton.enabled = False
      self.sideBySideLayoutButton.enabled = True
      self.overviewGroupBox.hide()
      self.segmentationGroupBox.hide()
      self.registrationEvaluationButtonsGroupBox.show()
      self.registrationResultsGroupBox.show()
      self.registrationEvaluationGroupBoxLayout.addWidget(self.targetTable, 4, 0)
      self.registrationEvaluationGroupBox.show()

      self.useRevealCursorButton.enabled = True
      self.visualEffectsGroupBox.enabled = True
      self.registrationEvaluationButtonsGroupBox.enabled = True
      self.rejectRegistrationResultButton.enabled = not self.getSetting("COVER_PROSTATE") in self.currentResult.name

  @property
  def mpReviewPreprocessedOutput(self):
    return os.path.join(self.currentCaseDirectory, "mpReviewPreprocessed") if self.currentCaseDirectory else None

  @property
  def preopDICOMDataDirectory(self):
    return os.path.join(self.currentCaseDirectory, "DICOM", "Preop") if self.currentCaseDirectory else None

  @property
  def intraopDICOMDataDirectory(self):
    return os.path.join(self.currentCaseDirectory, "DICOM", "Intraop") if self.currentCaseDirectory else None

  @property
  def outputDir(self):
    return os.path.join(self.currentCaseDirectory, "SliceTrackerOutputs")

  @property
  def currentCaseDirectory(self):
    return self._currentCaseDirectory

  @property
  def currentTargets(self):
    return self._currentTargets

  @currentTargets.setter
  def currentTargets(self, targets):
    self._currentTargets = targets
    self.targetTableModel.targetList = targets
    if not targets:
      self.targetTableModel.coverProstateTargetList = None
    else:
      coverProstate = self.registrationResults.getMostRecentApprovedCoverProstateRegistration()
      if coverProstate:
        self.targetTableModel.coverProstateTargetList = coverProstate.approvedTargets
    self.targetTable.enabled = targets is not None
    self.updateDisplacementChartTargetSelectorTable()

  @currentCaseDirectory.setter
  def currentCaseDirectory(self, path):
    self._currentCaseDirectory = path
    valid = path is not None
    self.closeCaseButton.enabled = valid
    if valid:
      self.layoutManager.layoutChanged.connect(self.onLayoutChanged)
      self.updateCaseWatchBox()
    else:
      self.layoutManager.layoutChanged.disconnect(self.onLayoutChanged)
      self.caseWatchBox.reset()

  @property
  def generatedOutputDirectory(self):
    return self._generatedOutputDirectory

  @generatedOutputDirectory.setter
  def generatedOutputDirectory(self, path):
    if not os.path.exists(path):
      self.logic.createDirectory(path)
    exists = os.path.exists(path)
    self._generatedOutputDirectory = path if exists else ""
    self.completeCaseButton.enabled = exists and not self.logic.caseCompleted

  def __init__(self, parent=None):
    ScriptedLoadableModuleWidget.__init__(self, parent)
    self.modulePath = os.path.dirname(slicer.util.modulePath(self.moduleName))
    SliceTrackerConfiguration(self.moduleName, os.path.join(self.modulePath, 'Resources', "default.cfg"))
    self.zFrameRegistrationClass = getattr(sys.modules[__name__], self.getSetting("ZFrame_Registration_Class_Name"))
    self.logic = SliceTrackerLogic()
    self.sampleDownloader = SampleDataDownloader(True)
    self.markupsLogic = slicer.modules.markups.logic()
    self.volumesLogic = slicer.modules.volumes.logic()
    self.annotationLogic = slicer.modules.annotations.logic()
    self.iconPath = os.path.join(self.modulePath, 'Resources/Icons')
    self.setupIcons()
    slicer.app.connect('aboutToQuit()', self.onSlicerQuits)

  def onSlicerQuits(self):
    self.clearData()

  def onReload(self):
    try:
      self.clearData()
      if self.customStatusProgressBar:
        slicer.util.mainWindow().statusBar().removeWidget(self.customStatusProgressBar)
    except:
      pass
    ScriptedLoadableModuleWidget.onReload(self)

  def enter(self):
    if not slicer.dicomDatabase:
      slicer.util.errorDisplay("Slicer DICOMDatabase was not found. In order to be able to use SliceTracker, you will "
                               "need to set a proper location for the Slicer DICOMDatabase.")
    self.layout.parent().enabled = slicer.dicomDatabase is not None

  def clearData(self):
    self.simulatePreopPhaseButton.enabled = False
    self.simulateIntraopPhaseButton.enabled = False
    self.cleanupPreopDICOMReceiver()
    self.sampleDownloader.resetAndInitialize()
    if self.currentCaseDirectory:
      self.logic.closeCase(self.currentCaseDirectory)
      self.currentCaseDirectory = None
    slicer.mrmlScene.Clear(0)
    self.logic.resetAndInitializeData()
    self.updateIntraopSeriesSelectorTable()
    self.updateIntraopSeriesSelectorColor(None)
    self.removeSliceAnnotations()
    self.seriesModel.clear()
    self.trackTargetsButton.setEnabled(False)
    self.currentTargets = None
    self.updateDisplacementChartTargetSelectorTable()
    self.targetDisplacementChart.resetAndInitializeChart()
    self.targetDisplacementChart.lastSelectedTarget = None
    self.resetViewSettingButtons()
    self.resetVisualEffects()
    self.disconnectKeyEventObservers()
    self.patientWatchBox.sourceFile = None
    self.intraopWatchBox.sourceFile = None
    self.continueOldCase = False
    if self.customStatusProgressBar:
      self.customStatusProgressBar.reset()
      self.customStatusProgressBar.hide()

  def cleanup(self):
    ScriptedLoadableModuleWidget.cleanup(self)
    self.clearData()

  def updateOutputFolder(self):
    if os.path.exists(self.generatedOutputDirectory):
      return
    if self.patientWatchBox.getInformation("PatientID") != '' \
            and self.intraopWatchBox.getInformation("StudyDate") != '':
      if self.outputDir and not os.path.exists(self.outputDir):
        self.logic.createDirectory(self.outputDir)
      finalDirectory = self.patientWatchBox.getInformation("PatientID") + "-biopsy-" + \
                       str(qt.QDate().currentDate()) + "-" + qt.QTime().currentTime().toString().replace(":", "")
      self.generatedOutputDirectory = os.path.join(self.outputDir, finalDirectory, "MRgBiopsy")
    else:
      self.generatedOutputDirectory = ""

  def createPatientWatchBox(self):
    self.patientWatchBoxInformation = [WatchBoxAttribute('PatientID', 'Patient ID: ', DICOMTAGS.PATIENT_ID, masked=self.demoMode),
                                       WatchBoxAttribute('PatientName', 'Patient Name: ', DICOMTAGS.PATIENT_NAME, masked=self.demoMode),
                                       WatchBoxAttribute('DOB', 'Date of Birth: ', DICOMTAGS.PATIENT_BIRTH_DATE, masked=self.demoMode),
                                       WatchBoxAttribute('StudyDate', 'Preop Study Date: ', DICOMTAGS.STUDY_DATE)]
    self.patientWatchBox = DICOMBasedInformationWatchBox(self.patientWatchBoxInformation)
    self.layout.addWidget(self.patientWatchBox)

    intraopWatchBoxInformation = [WatchBoxAttribute('StudyDate', 'Intraop Study Date: ', DICOMTAGS.STUDY_DATE),
                                  WatchBoxAttribute('CurrentSeries', 'Current Series: ', [DICOMTAGS.SERIES_NUMBER,
                                                                                          DICOMTAGS.SERIES_DESCRIPTION])]
    self.intraopWatchBox = DICOMBasedInformationWatchBox(intraopWatchBoxInformation)
    self.registrationDetailsButton = self.createButton("", icon=self.settingsIcon, styleSheet="border:none;",
                                                       maximumWidth=16)
    self.layout.addWidget(self.intraopWatchBox)

  def createCaseInformationArea(self):
    self.casesRootDirectoryButton = self.createDirectoryButton(text="Choose cases root location",
                                                               caption="Choose cases root location",
                                                               directory=self.getSetting('CasesRootLocation'))
    self.createCaseWatchBox()
    self.collapsibleDirectoryConfigurationArea = ctk.ctkCollapsibleButton()
    self.collapsibleDirectoryConfigurationArea.collapsed = True
    self.collapsibleDirectoryConfigurationArea.text = "Case Directory Settings"
    self.directoryConfigurationLayout = qt.QGridLayout(self.collapsibleDirectoryConfigurationArea)
    self.directoryConfigurationLayout.addWidget(qt.QLabel("Cases Root Directory"), 1, 0, 1, 1)
    self.directoryConfigurationLayout.addWidget(self.casesRootDirectoryButton, 1, 1, 1, 1)
    self.directoryConfigurationLayout.addWidget(self.caseWatchBox, 2, 0, 1, qt.QSizePolicy.ExpandFlag)
    self.layout.addWidget(self.collapsibleDirectoryConfigurationArea)

  def createCaseWatchBox(self):
    watchBoxInformation = [WatchBoxAttribute('CurrentCaseDirectory', 'Directory'),
                           WatchBoxAttribute('CurrentPreopDICOMDirectory', 'Preop DICOM Directory: '),
                           WatchBoxAttribute('CurrentIntraopDICOMDirectory', 'Intraop DICOM Directory: '),
                           WatchBoxAttribute('mpReviewDirectory', 'mpReview Directory: ')]
    self.caseWatchBox = BasicInformationWatchBox(watchBoxInformation, title="Current Case")

  def setupIcons(self):
    self.greenCheckIcon = self.createIcon('icon-greenCheck.png')
    self.newImageDataIcon = self.createIcon('icon-newImageData.png')
    self.settingsIcon = self.createIcon('icon-settings.png')
    self.zFrameIcon = self.createIcon('icon-zframe.png')
    self.needleIcon = self.createIcon('icon-needle.png')
    self.templateIcon = self.createIcon('icon-template.png')
    self.textInfoIcon = self.createIcon('icon-text-info.png')
    self.revealCursorIcon = self.createIcon('icon-revealCursor.png')
    self.skipIcon = self.createIcon('icon-skip.png')

  def setup(self):
    ScriptedLoadableModuleWidget.setup(self)

    try:
      import VolumeClipWithModel
    except ImportError:
      return slicer.util.warningDisplay("Error: Could not find extension VolumeClip. Open Slicer Extension Manager and install "
                                "VolumeClip.", "Missing Extension")

    self.ratingWindow = RatingWindow(int(self.getSetting("Maximum_Rating_Score")))
    self.ratingWindow.disableWidgetCheckbox.checked = not self.getSetting("Rating_Enabled") == "true"
    self.demoMode = self.getSetting("Demo_Mode") == "true"
    self.sliceAnnotations = []
    self.mouseReleaseEventObservers = {}
    self.revealCursor = None
    self._currentTargets = None
    self.moveTargetMode = False
    self.currentlyMovedTargetModelIndex = None

    self.sliceWidgets = []

    self.keyPressEventObservers = {}
    self.keyReleaseEventObservers = {}

    self.logic.retryMode = False
    self.logic.zFrameRegistrationSuccessful = False

    self.lastSelectedModelIndex = None

    self.notifyUserAboutNewData = True

    self.createPatientWatchBox()
    self.customStatusProgressBar = self.getOrCreateCustomProgressBar()
    self.setupViewSettingGroupBox()
    self.createCaseInformationArea()
    self.setupRegistrationWatchBox()

    self.setupSliceWidgets()
    self.setupZFrameRegistrationUIElements()
    self.setupOverviewStepUIElements()
    self.setupTargetingStepUIElements()
    self.setupSegmentationUIElements()
    self.setupEvaluationStepUIElements()
    self.setupConnections()

    self.generatedOutputDirectory = ""
    self.caseRootDir = self.getSetting('CasesRootLocation')
    self._currentCaseDirectory = None

    self.layoutManager.setLayout(self.LAYOUT_RED_SLICE_ONLY)
    self.setAxialOrientation()

    self.showAcceptRegistrationWarning = False

    self.roiObserverTag = None
    self.coverTemplateROI = None
    self.zFrameCroppedVolume = None
    self.zFrameLabelVolume = None
    self.zFrameMaskedVolume = None

    self.zFrameClickObserver = None
    self.zFrameInstructionAnnotation = None

    self.segmentationNoPreopAnnotation = None

    self.preopTransferWindow = None
    self._generatedOutputDirectory = ""
    self.continueOldCase = False
    self.preopDataDir = None

    self.registrationResultOldImageAnnotation = None
    self.registrationResultNewImageAnnotation = None

    self.currentStep = self.STEP_OVERVIEW
    self.layoutManager.setLayout(self.layoutManager.layout)

  def setupViewSettingGroupBox(self):
    iconSize = qt.QSize(24, 24)
    self.redOnlyLayoutButton = RedSliceLayoutButton()
    self.sideBySideLayoutButton = SideBySideLayoutButton()
    self.fourUpLayoutButton = FourUpLayoutButton()
    self.crosshairButton = CrosshairButton()
    self.wlEffectsToolButton = WindowLevelEffectsButton()
    self.settingsButton = ModuleSettingsButton(self.moduleName)

    self.showZFrameModelButton = self.createButton("", icon=self.zFrameIcon, iconSize=iconSize, checkable=True, toolTip="Display zFrame model")
    self.showTemplateButton = self.createButton("", icon=self.templateIcon, iconSize=iconSize, checkable=True, toolTip="Display template")
    self.showNeedlePathButton = self.createButton("", icon=self.needleIcon, iconSize=iconSize, checkable=True, toolTip="Display needle path")
    self.showTemplatePathButton = self.createButton("", icon=self.templateIcon, iconSize=iconSize, checkable=True, toolTip="Display template paths")
    self.showAnnotationsButton = self.createButton("", icon=self.textInfoIcon, iconSize=iconSize, checkable=True, toolTip="Display annotations", checked=True)

    self.resetViewSettingButtons()
    self.layout.addWidget(self.createHLayout([self.redOnlyLayoutButton, self.sideBySideLayoutButton,
                                              self.fourUpLayoutButton, self.showAnnotationsButton,
                                              self.crosshairButton, self.showZFrameModelButton,
                                              self.showTemplatePathButton, self.showNeedlePathButton,
                                              self.wlEffectsToolButton, self.settingsButton]))

  def resetViewSettingButtons(self):
    self.showTemplateButton.enabled = self.logic.templateSuccessfulLoaded
    self.showTemplatePathButton.enabled = self.logic.templateSuccessfulLoaded
    self.showZFrameModelButton.enabled = self.logic.zFrameSuccessfulLoaded
    self.showTemplateButton.checked = False
    self.showTemplatePathButton.checked = False
    self.showZFrameModelButton.checked = False
    self.showNeedlePathButton.checked = False

    self.wlEffectsToolButton.checked = False
    self.crosshairButton.checked = False

  def setupSliceWidgets(self):
    self.createSliceWidgetClassMembers("Red")
    self.createSliceWidgetClassMembers("Yellow")
    self.createSliceWidgetClassMembers("Green")
    self.layoutManager.setLayout(self.LAYOUT_RED_SLICE_ONLY)

  def setDefaultOrientation(self):
    self.redSliceNode.SetOrientationToAxial()
    self.yellowSliceNode.SetOrientationToSagittal()
    self.greenSliceNode.SetOrientationToCoronal()
    self.updateFOV() # TODO: shall not be called here

  def setAxialOrientation(self):
    self.redSliceNode.SetOrientationToAxial()
    self.yellowSliceNode.SetOrientationToAxial()
    self.greenSliceNode.SetOrientationToAxial()
    self.updateFOV() # TODO: shall not be called here

  def updateFOV(self):
    if self.getSetting("COVER_TEMPLATE") in self.intraopSeriesSelector.currentText:
      self.setDefaultFOV(self.redSliceLogic, 1.0)
      self.setDefaultFOV(self.yellowSliceLogic, 1.0)
      self.setDefaultFOV(self.greenSliceLogic, 1.0)
    elif self.layoutManager.layout == self.LAYOUT_RED_SLICE_ONLY:
      self.setDefaultFOV(self.redSliceLogic)
    elif self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
      self.setDefaultFOV(self.redSliceLogic)
      self.setDefaultFOV(self.yellowSliceLogic)
    elif self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      self.setDefaultFOV(self.redSliceLogic)
      self.yellowSliceLogic.FitSliceToAll()
      self.greenSliceLogic.FitSliceToAll()

  def setupZFrameRegistrationUIElements(self):
    self.zFrameRegistrationGroupBox = qt.QGroupBox()
    self.zFrameRegistrationGroupBoxGroupBoxLayout = qt.QGridLayout()
    self.zFrameRegistrationGroupBox.setLayout(self.zFrameRegistrationGroupBoxGroupBoxLayout)

    self.applyZFrameRegistrationButton = self.createButton("Run ZFrame Registration", enabled=False)

    self.zFrameRegistrationManualIndexesGroupBox = qt.QGroupBox("Use manual start/end indexes")
    self.zFrameRegistrationManualIndexesGroupBox.setCheckable(True)
    self.zFrameRegistrationManualIndexesGroupBoxLayout = qt.QGridLayout()
    self.zFrameRegistrationManualIndexesGroupBox.setLayout(self.zFrameRegistrationManualIndexesGroupBoxLayout)

    self.zFrameRegistrationStartIndex = qt.QSpinBox()
    self.zFrameRegistrationEndIndex = qt.QSpinBox()

    self.zFrameRegistrationManualIndexesGroupBoxLayout.addWidget(self.createHLayout([qt.QLabel("start"),
                                                                                  self.zFrameRegistrationStartIndex,
                                                                                  qt.QLabel("end"),
                                                                                  self.zFrameRegistrationEndIndex]),
                                                                 1, 1, qt.Qt.AlignRight)

    self.approveZFrameRegistrationButton = self.createButton("Confirm registration accuracy", enabled=False)
    self.retryZFrameRegistrationButton = self.createButton("Reset", enabled=False)

    buttons = self.createVLayout([self.applyZFrameRegistrationButton, self.approveZFrameRegistrationButton,
                                  self.retryZFrameRegistrationButton])
    self.zFrameRegistrationGroupBoxGroupBoxLayout.addWidget(self.createHLayout([buttons,
                                                                                self.zFrameRegistrationManualIndexesGroupBox]))

    self.zFrameRegistrationGroupBoxGroupBoxLayout.setRowStretch(1, 1)
    self.layout.addWidget(self.zFrameRegistrationGroupBox)

  def setupTrainingSectionUIElements(self):
    self.collapsibleTrainingArea = ctk.ctkCollapsibleButton()
    self.collapsibleTrainingArea.collapsed = True
    self.collapsibleTrainingArea.text = "Training"

    self.simulatePreopPhaseButton = self.createButton("Simulate preop phase", enabled=False)
    self.simulateIntraopPhaseButton = self.createButton("Simulate intraop phase", enabled=False)

    self.trainingsAreaLayout = qt.QGridLayout(self.collapsibleTrainingArea)
    self.trainingsAreaLayout.addWidget(self.createHLayout([self.simulatePreopPhaseButton,
                                                           self.simulateIntraopPhaseButton]))

  def setupOverviewStepUIElements(self):
    self.overviewGroupBox = qt.QGroupBox()
    self.overviewGroupBoxLayout = qt.QGridLayout()
    self.overviewGroupBox.setLayout(self.overviewGroupBoxLayout)

    self.setupTrainingSectionUIElements()

    self.trackTargetsButton = self.createButton("Track targets", toolTip="Track targets", enabled=False)
    self.skipIntraopSeriesButton = self.createButton("Skip", toolTip="Skip the currently selected series", enabled=False)
    self.closeCaseButton = self.createButton("Close case", toolTip="Close case without completing it", enabled=False)
    self.completeCaseButton = self.createButton('Case completed', enabled=False)
    self.setupTargetsTable()
    self.setupIntraopSeriesSelector()

    self.createNewCaseButton = self.createButton("New case")
    self.openCaseButton = self.createButton("Open case")

    self.overviewGroupBoxLayout.addWidget(self.createNewCaseButton, 1, 0)
    self.overviewGroupBoxLayout.addWidget(self.openCaseButton, 1, 1)
    self.overviewGroupBoxLayout.addWidget(self.collapsibleTrainingArea, 2, 0, 1, 2)
    self.overviewGroupBoxLayout.addWidget(self.targetTable, 3, 0, 1, 2)
    self.overviewGroupBoxLayout.addWidget(self.intraopSeriesSelector, 4, 0)
    self.overviewGroupBoxLayout.addWidget(self.skipIntraopSeriesButton, 4, 1)
    self.overviewGroupBoxLayout.addWidget(self.trackTargetsButton, 5, 0, 1, 2)
    self.overviewGroupBoxLayout.addWidget(self.closeCaseButton, 6, 0, 1, 2)
    self.overviewGroupBoxLayout.addWidget(self.completeCaseButton, 7, 0, 1, 2)

    self.targetDisplacementChart = TargetDisplacementChartWidget()
    self.overviewGroupBoxLayout.addWidget(self.targetDisplacementChart.plotFrame, 8, 0, 1, 2)

    self.overviewGroupBoxLayout.setRowStretch(6, 1)
    self.layout.addWidget(self.overviewGroupBox)

  def setupTargetsTable(self):
    self.targetTable = qt.QTableView()
    self.targetTableModel = CustomTargetTableModel(self.logic)
    self.targetTableModel.addEventObserver(vtk.vtkCommand.ModifiedEvent, self.updateNeedleModel)
    self.targetTable.setModel(self.targetTableModel)
    self.targetTable.setSelectionBehavior(qt.QTableView.SelectItems)
    self.setTargetTableSizeConstraints()
    self.targetTable.verticalHeader().hide()
    self.targetTable.minimumHeight = 150
    self.targetTable.setStyleSheet("QTableView::item:selected{background-color: #ff7f7f; color: black};")

  def setTargetTableSizeConstraints(self):
    self.targetTable.horizontalHeader().setResizeMode(qt.QHeaderView.Stretch)
    self.targetTable.horizontalHeader().setResizeMode(0, qt.QHeaderView.Fixed)
    self.targetTable.horizontalHeader().setResizeMode(1, qt.QHeaderView.Stretch)
    self.targetTable.horizontalHeader().setResizeMode(2, qt.QHeaderView.ResizeToContents)
    self.targetTable.horizontalHeader().setResizeMode(3, qt.QHeaderView.ResizeToContents)

  def setupIntraopSeriesSelector(self):
    self.intraopSeriesSelector = qt.QComboBox()
    self.seriesModel = qt.QStandardItemModel()
    self.intraopSeriesSelector.setModel(self.seriesModel)

  def setupSegmentationUIElements(self):
    iconSize = qt.QSize(24, 24)

    self.volumeClipGroupBox = qt.QWidget()
    self.volumeClipGroupBoxLayout = qt.QVBoxLayout()
    self.volumeClipGroupBox.setLayout(self.volumeClipGroupBoxLayout)

    self.volumeClipToLabelWidget = VolumeClipToLabelWidget(self.volumeClipGroupBox)
    self.volumeClipToLabelWidget.setup()
    if self.developerMode:
      self.volumeClipToLabelWidget.reloadCollapsibleButton.hide()
    self.volumeClipToLabelWidget.selectorsGroupBox.hide()
    self.volumeClipToLabelWidget.colorGroupBox.hide()
    self.volumeClipToLabelWidget.logic.colorNode = self.logic.mpReviewColorNode
    self.volumeClipToLabelWidget.onColorSelected(self.logic.segmentedLabelValue)

    self.applyRegistrationButton = self.createButton("Apply Registration", icon=self.greenCheckIcon, iconSize=iconSize,
                                                     toolTip="Run Registration.")
    self.applyRegistrationButton.setFixedHeight(45)

    self.editorWidgetButton = self.createButton("", icon=self.settingsIcon, toolTip="Show Label Editor",
                                                enabled=False, iconSize=iconSize)

    self.setupEditorWidget()
    self.segmentationGroupBox = qt.QGroupBox()
    self.segmentationGroupBoxLayout = qt.QGridLayout()
    self.segmentationGroupBox.setLayout(self.segmentationGroupBoxLayout)
    self.volumeClipToLabelWidget.segmentationButtons.layout().addWidget(self.editorWidgetButton)
    self.segmentationGroupBoxLayout.addWidget(self.volumeClipGroupBox, 0, 0)
    self.segmentationGroupBoxLayout.addWidget(self.editorWidgetParent, 1, 0)
    self.segmentationGroupBoxLayout.addWidget(self.applyRegistrationButton, 2, 0)
    self.segmentationGroupBoxLayout.setRowStretch(3, 1)
    self.layout.addWidget(self.segmentationGroupBox)
    self.editorWidgetParent.hide()

  def setupEditorWidget(self):
    self.editorWidgetParent = slicer.qMRMLWidget()
    self.editorWidgetParent.setLayout(qt.QVBoxLayout())
    self.editorWidgetParent.setMRMLScene(slicer.mrmlScene)
    self.editUtil = EditorLib.EditUtil.EditUtil()
    self.editorWidget = EditorWidget(parent=self.editorWidgetParent, showVolumesFrame=False)
    self.editorWidget.setup()
    self.editorParameterNode = self.editUtil.getParameterNode()

  def setupTargetingStepUIElements(self):
    self.targetingGroupBox = qt.QGroupBox()
    self.targetingGroupBoxLayout = qt.QFormLayout()
    self.targetingGroupBox.setLayout(self.targetingGroupBoxLayout)

    self.fiducialsWidget = TargetCreationWidget(self.targetingGroupBoxLayout)
    self.fiducialsWidget.addEventObserver(vtk.vtkCommand.ModifiedEvent, self.onTargetListModified)
    self.finishTargetingStepButton = self.createButton("Done setting targets", enabled=True,
                                                       toolTip="Click this button to continue after setting targets")

    self.targetingGroupBoxLayout.addRow(self.finishTargetingStepButton)
    self.layout.addWidget(self.targetingGroupBox)

  def onTargetListModified(self, caller, event):
    self.finishTargetingStepButton.enabled = self.fiducialsWidget.currentNode is not None and \
                                             self.fiducialsWidget.currentNode.GetNumberOfFiducials()

  def setupRegistrationWatchBox(self):
    self.registrationGroupBox = qt.QGroupBox()
    self.registrationGroupBoxLayout = qt.QFormLayout()
    self.registrationGroupBox.setLayout(self.registrationGroupBoxLayout)
    self.movingVolumeSelector = self.createComboBox(nodeTypes=["vtkMRMLScalarVolumeNode", ""], showChildNodeTypes=False,
                                                    selectNodeUponCreation=True, toolTip="Pick algorithm input.")
    self.movingLabelSelector = self.createComboBox(nodeTypes=["vtkMRMLLabelMapVolumeNode", ""], showChildNodeTypes=False,
                                                   selectNodeUponCreation=False, toolTip="Pick algorithm input.")
    self.fixedVolumeSelector = self.createComboBox(nodeTypes=["vtkMRMLScalarVolumeNode", ""], noneEnabled=True,
                                                   showChildNodeTypes=False, selectNodeUponCreation=True,
                                                   toolTip="Pick algorithm input.")
    self.fixedLabelSelector = self.createComboBox(nodeTypes=["vtkMRMLLabelMapVolumeNode", ""],
                                                  showChildNodeTypes=False,
                                                  selectNodeUponCreation=True, toolTip="Pick algorithm input.")
    self.fiducialSelector = self.createComboBox(nodeTypes=["vtkMRMLMarkupsFiducialNode", ""], noneEnabled=True,
                                                showChildNodeTypes=False, selectNodeUponCreation=False,
                                                toolTip="Select the Targets")
    self.registrationGroupBoxLayout.addRow("Moving Image Volume: ", self.movingVolumeSelector)
    self.registrationGroupBoxLayout.addRow("Moving Label Volume: ", self.movingLabelSelector)
    self.registrationGroupBoxLayout.addRow("Fixed Image Volume: ", self.fixedVolumeSelector)
    self.registrationGroupBoxLayout.addRow("Fixed Label Volume: ", self.fixedLabelSelector)
    self.registrationGroupBoxLayout.addRow("Targets: ", self.fiducialSelector)
    self.registrationGroupBox.hide()
    self.layout.addWidget(self.registrationGroupBox)

  def setupEvaluationStepUIElements(self):
    self.registrationEvaluationGroupBox = qt.QGroupBox()
    self.registrationEvaluationGroupBoxLayout = qt.QGridLayout()
    self.registrationEvaluationGroupBox.setLayout(self.registrationEvaluationGroupBoxLayout)

    self.setupRegistrationResultsGroupBox()
    self.setupRegistrationValidationButtons()
    self.registrationEvaluationGroupBoxLayout.addWidget(self.registrationResultsGroupBox, 3, 0)
    self.registrationEvaluationGroupBoxLayout.addWidget(self.registrationEvaluationButtonsGroupBox, 5, 0)
    self.registrationEvaluationGroupBoxLayout.setRowStretch(6, 1)
    self.layout.addWidget(self.registrationEvaluationGroupBox)

  def setupRegistrationValidationButtons(self):
    self.approveRegistrationResultButton = self.createButton("Approve", toolTip="Approve")
    self.retryRegistrationButton = self.createButton("Retry", toolTip="Retry")
    self.rejectRegistrationResultButton = self.createButton("Reject", toolTip="Reject")
    self.registrationEvaluationButtonsGroupBox = self.createHLayout([self.retryRegistrationButton,
                                                                     self.approveRegistrationResultButton,
                                                                     self.rejectRegistrationResultButton])
    self.registrationEvaluationButtonsGroupBox.enabled = False

  def setupRegistrationResultsGroupBox(self):

    self.registrationResultsGroupBox = qt.QGroupBox("Registration Results")
    self.registrationResultsGroupBoxLayout = qt.QFormLayout()
    self.registrationResultsGroupBox.setLayout(self.registrationResultsGroupBoxLayout)

    self.resultSelector = ctk.ctkComboBox()
    self.registrationResultsGroupBoxLayout.addWidget(self.resultSelector)

    self.showRigidResultButton = self.createButton('Rigid', checkable=True, name='rigid')
    self.showAffineResultButton = self.createButton('Affine', checkable=True, name='affine')
    self.showBSplineResultButton = self.createButton('BSpline', checkable=True, name='bSpline')

    self.registrationButtonGroup = qt.QButtonGroup()
    self.registrationButtonGroup.addButton(self.showRigidResultButton, 1)
    self.registrationButtonGroup.addButton(self.showAffineResultButton, 2)
    self.registrationButtonGroup.addButton(self.showBSplineResultButton, 3)

    self.registrationTypesGroupBox = qt.QGroupBox("Type")
    self.registrationTypesGroupBoxLayout = qt.QFormLayout(self.registrationTypesGroupBox)
    self.registrationTypesGroupBoxLayout.addWidget(self.createVLayout([self.showRigidResultButton,
                                                                       self.showAffineResultButton,
                                                                       self.showBSplineResultButton]))
    self.setupVisualEffectsUIElements()

    self.registrationResultsGroupBoxLayout.addWidget(self.createHLayout([self.registrationTypesGroupBox,
                                                                         self.visualEffectsGroupBox]))

  def setupVisualEffectsUIElements(self):
    self.opacitySpinBox = qt.QDoubleSpinBox()
    self.opacitySpinBox.minimum = 0
    self.opacitySpinBox.maximum = 1.0
    self.opacitySpinBox.value = 0
    self.opacitySpinBox.singleStep = 0.05

    self.opacitySliderPopup = ctk.ctkPopupWidget(self.opacitySpinBox)
    popupLayout = qt.QHBoxLayout(self.opacitySliderPopup)
    self.opacitySlider = ctk.ctkDoubleSlider(self.opacitySliderPopup)
    self.opacitySlider.orientation = qt.Qt.Horizontal
    self.opacitySlider.minimum = 0
    self.opacitySlider.maximum = 1.0
    self.opacitySlider.value = 0
    self.opacitySlider.singleStep = 0.05

    popupLayout.addWidget(self.opacitySlider)
    self.opacitySliderPopup.verticalDirection = ctk.ctkBasePopupWidget.TopToBottom
    self.opacitySliderPopup.animationEffect = ctk.ctkBasePopupWidget.FadeEffect
    self.opacitySliderPopup.orientation = qt.Qt.Horizontal
    self.opacitySliderPopup.easingCurve = qt.QEasingCurve.OutQuart
    self.opacitySliderPopup.effectDuration = 100

    self.rockCount = 0
    self.rockTimer = qt.QTimer()
    self.rockTimer.setInterval(50)
    self.rockCheckBox = qt.QCheckBox("Rock")
    self.rockCheckBox.checked = False

    self.flickerTimer = qt.QTimer()
    self.flickerTimer.setInterval(400)
    self.flickerCheckBox = qt.QCheckBox("Flicker")
    self.flickerCheckBox.checked = False

    self.animaHolderLayout = self.createHLayout([self.rockCheckBox, self.flickerCheckBox])
    self.visualEffectsGroupBox = qt.QGroupBox("Visual Effects")
    self.visualEffectsGroupBoxLayout = qt.QFormLayout(self.visualEffectsGroupBox)
    self.useRevealCursorButton = self.createButton("", icon=self.revealCursorIcon, checkable=True,
                                                   enabled=False, toolTip="Use reveal cursor")
    slider = self.createHLayout([self.opacitySpinBox, self.animaHolderLayout])
    self.visualEffectsGroupBoxLayout.addWidget(self.createVLayout([slider, self.useRevealCursorButton]))

  def startPreopPhaseSimulation(self):
    if self.PREOP_SAMPLE_DATA_URL and self.preopDICOMDataDirectory and self.preopTransferWindow:
      self.logic.trainingMode = True
      if self.preopTransferWindow.dicomReceiver.isRunning():
        self.preopTransferWindow.dicomReceiver.stopStoreSCP()
      self.simulatePreopPhaseButton.enabled = False
      preopZipFile = self.initiateSampleDataDownload(self.PREOP_SAMPLE_DATA_URL)
      if not self.sampleDownloader.wasCanceled and preopZipFile:
        self.unzipFileAndCopyToDirectory(preopZipFile, self.preopDICOMDataDirectory)

  def startIntraopPhaseSimulation(self):
    if self.INTRAOP_SAMPLE_DATA_URL and self.intraopDataDir:
      if not self.logic.trainingMode:
        self.logic.smartDicomReceiver.stopStoreSCP()
        self.logic.trainingMode = True
      self.simulateIntraopPhaseButton.enabled = False
      intraopZipFile = self.initiateSampleDataDownload(self.INTRAOP_SAMPLE_DATA_URL)
      if not self.sampleDownloader.wasCanceled and intraopZipFile:
        self.unzipFileAndCopyToDirectory(intraopZipFile, self.intraopDICOMDataDirectory)

  def initiateSampleDataDownload(self, url):
    filename = os.path.basename(url)
    self.sampleDownloader.resetAndInitialize()
    self.sampleDownloader.addEventObserver(self.sampleDownloader.EVENTS['status_changed'], self.onDownloadProgressUpdated)
    self.customStatusProgressBar.show()
    downloadedFile = self.sampleDownloader.downloadFileIntoCache(url, filename)
    self.customStatusProgressBar.hide()
    return None if self.sampleDownloader.wasCanceled else downloadedFile

  @onReturnProcessEvents
  @vtk.calldata_type(vtk.VTK_STRING)
  def onDownloadProgressUpdated(self, caller, event, callData):
    message, percent = ast.literal_eval(callData)
    self.customStatusProgressBar.updateStatus(message, percent)

  def unzipFileAndCopyToDirectory(self, filepath, copyToDirectory):
    import zipfile
    try:
      zip_ref = zipfile.ZipFile(filepath, 'r')
      destination = filepath.replace(os.path.basename(filepath), "")
      logging.debug("extracting to %s " % destination)
      zip_ref.extractall(destination)
      zip_ref.close()
      self.copyDirectory(filepath.replace(".zip", ""), copyToDirectory)
    except zipfile.BadZipfile as exc:
      if self.preopTransferWindow:
        self.preopTransferWindow.hide()
      slicer.util.errorDisplay("An error appeared while extracting %s. If the file is corrupt, please delete it and try "
                               "again." % filepath, detailedText=str(exc.message))
      self.clearData()

  def copyDirectory(self, source, destination, recursive=True):
    print source
    assert os.path.isdir(source)
    for listObject in os.listdir(source):
      current = os.path.join(source, listObject)
      if os.path.isdir(current) and recursive:
        self.copyDirectory(current, destination, recursive)
      else:
        shutil.copy(current, destination)

  def setupConnections(self):

    def setupButtonConnections():
      def setupOverviewStepButtonConnections():
        self.createNewCaseButton.clicked.connect(self.onCreateNewCaseButtonClicked)
        self.openCaseButton.clicked.connect(self.onOpenCaseButtonClicked)
        self.casesRootDirectoryButton.directoryChanged.connect(lambda: setattr(self, "caseRootDir",
                                                                                self.casesRootDirectoryButton.directory))
        self.skipIntraopSeriesButton.clicked.connect(self.onSkipIntraopSeriesButtonClicked)
        self.trackTargetsButton.clicked.connect(self.onTrackTargetsButtonClicked)
        self.completeCaseButton.clicked.connect(self.onCompleteCaseButtonClicked)
        self.closeCaseButton.clicked.connect(self.clearData)

        self.simulatePreopPhaseButton.clicked.connect(self.startPreopPhaseSimulation)
        self.simulateIntraopPhaseButton.clicked.connect(self.startIntraopPhaseSimulation)

      def setupSegmentationStepButtonConnections():
        self.editorWidgetButton.clicked.connect(self.onEditorGearIconClicked)
        self.applyRegistrationButton.clicked.connect(lambda: self.onInvokeRegistration(initial=True))

      def setupEvaluationStepButtonConnections():
        self.registrationButtonGroup.connect('buttonClicked(int)', self.onRegistrationButtonChecked)

        self.retryRegistrationButton.clicked.connect(self.onRetryRegistrationButtonClicked)
        self.approveRegistrationResultButton.clicked.connect(self.onApproveRegistrationResultButtonClicked)
        self.rejectRegistrationResultButton.clicked.connect(self.onRejectRegistrationResultButtonClicked)
        self.registrationDetailsButton.clicked.connect(self.onShowRegistrationDetails)

      def setupViewSettingsButtonConnections():
        self.useRevealCursorButton.connect('toggled(bool)', self.onRevealToggled)
        self.showZFrameModelButton.connect('toggled(bool)', self.onShowZFrameModelToggled)
        self.showTemplateButton.connect('toggled(bool)', self.onShowZFrameTemplateToggled)
        self.showTemplatePathButton.connect('toggled(bool)', self.onShowTemplatePathToggled)
        self.showAnnotationsButton.connect('toggled(bool)', self.onShowAnnotationsToggled)
        self.showNeedlePathButton.connect('toggled(bool)', self.onShowNeedlePathToggled)

      def setupZFrameRegistrationStepButtonConnections():
        self.retryZFrameRegistrationButton.clicked.connect(self.onRetryZFrameRegistrationButtonClicked)
        self.approveZFrameRegistrationButton.clicked.connect(self.onApproveZFrameRegistrationButtonClicked)
        self.applyZFrameRegistrationButton.clicked.connect(self.onApplyZFrameRegistrationButtonClicked)

      def setupTargetingStepButtonConnections():
        self.finishTargetingStepButton.clicked.connect(self.onFinishTargetingStepButtonClicked)

      setupViewSettingsButtonConnections()
      setupOverviewStepButtonConnections()
      setupZFrameRegistrationStepButtonConnections()
      setupSegmentationStepButtonConnections()
      setupEvaluationStepButtonConnections()
      setupTargetingStepButtonConnections()

    def setupSelectorConnections():
      self.resultSelector.connect('currentIndexChanged(QString)', self.onRegistrationResultSelected)
      self.intraopSeriesSelector.connect('currentIndexChanged(QString)', self.onIntraopSeriesSelectionChanged)
      self.targetDisplacementChart.DisplacementChartTargetSelector.connect('currentIndexChanged(QString)', self.onDisplacementChartTargetSelectionChanged)

    def setupCheckBoxConnections():
      self.rockCheckBox.connect('toggled(bool)', self.onRockToggled)
      self.flickerCheckBox.connect('toggled(bool)', self.onFlickerToggled)

    def setupOtherConnections():
      self.opacitySpinBox.valueChanged.connect(self.onOpacitySpinBoxChanged)
      self.opacitySlider.valueChanged.connect(self.onOpacitySliderChanged)
      self.rockTimer.connect('timeout()', self.onRockToggled)
      self.flickerTimer.connect('timeout()', self.onFlickerToggled)
      self.targetTable.connect('clicked(QModelIndex)', self.onTargetTableSelectionChanged)
      self.zFrameRegistrationStartIndex.valueChanged.connect(self.onZFrameStartIndexSpinBoxChanged)
      self.zFrameRegistrationEndIndex.valueChanged.connect(self.onZFrameEndIndexSpinBoxChanged)

    def setupEventConnections():
      self.ratingWindow.addEventObserver(SlicerProstateEvents.RatingWindowClosedEvent, self.onRatingDone)

    setupCheckBoxConnections()
    setupButtonConnections()
    setupSelectorConnections()
    setupOtherConnections()
    setupEventConnections()

  def onZFrameStartIndexSpinBoxChanged(self, value):
    if not value <= self.zFrameRegistrationEndIndex.value:
      self.zFrameRegistrationEndIndex.value = value

  def onZFrameEndIndexSpinBoxChanged(self, value):
    if not value >= self.zFrameRegistrationStartIndex.value:
      self.zFrameRegistrationStartIndex.value = value

  def onFinishTargetingStepButtonClicked(self):
    self.fiducialsWidget.stopPlacing()
    if not slicer.util.confirmYesNoDisplay("Are you done setting targets and renaming them?"):
      return
    self.logic.preopTargets = self.fiducialsWidget.currentNode
    self.logic.preopVolume = self.fixedVolumeSelector.currentNode()
    self.createCoverProstateRegistrationResultManually()
    self.setupPreopLoadedTargets()
    self.hideAllTargets()
    self.openOverviewStep()
    self.fiducialsWidget.reset()

  def createCoverProstateRegistrationResultManually(self):
    fixedVolume = self.fixedVolumeSelector.currentNode()
    result = self.logic.generateNameAndCreateRegistrationResult(fixedVolume)
    approvedRegistrationType = "bSpline"
    result.originalTargets = self.logic.preopTargets
    targetName = str(result.seriesNumber) + '-TARGETS-' + approvedRegistrationType + result.suffix
    clone = self.logic.cloneFiducials(self.logic.preopTargets, targetName)
    self.logic.applyDefaultTargetDisplayNode(clone)
    result.setTargets(approvedRegistrationType, clone)
    result.fixedVolume = fixedVolume
    result.fixedLabel = self.fixedLabelSelector.currentNode()
    result.approve(approvedRegistrationType)

  def onCreateNewCaseButtonClicked(self):
    if not self.checkAndWarnUserIfCaseInProgress():
      return
    self.clearData()
    self.caseDialog = NewCaseSelectionNameWidget(self.caseRootDir)
    selectedButton = self.caseDialog.exec_()
    if selectedButton == qt.QMessageBox.Ok:
      self.currentCaseDirectory = self.logic.createNewCase(self.caseDialog.newCaseDirectory)
      self.startPreopDICOMReceiver()
      self.simulatePreopPhaseButton.enabled = True

  def checkAndWarnUserIfCaseInProgress(self):
    proceed = True
    if self.currentCaseDirectory is not None:
      if not slicer.util.confirmYesNoDisplay("Current case will be closed. Do you want to proceed?"):
        proceed = False
    return proceed

  def startPreopDICOMReceiver(self):
    self.cleanupPreopDICOMReceiver()
    self.preopTransferWindow = IncomingDataWindow(incomingDataDirectory=self.preopDICOMDataDirectory,
                                                  skipText="No Preop available")
    self.preopTransferWindow.addEventObserver(SlicerProstateEvents.IncomingDataSkippedEvent,
                                         self.continueWithoutPreopData)
    self.preopTransferWindow.addEventObserver(SlicerProstateEvents.IncomingDataCanceledEvent,
                                         self.onPreopTransferMessageBoxCanceled)
    self.preopTransferWindow.addEventObserver(SlicerProstateEvents.IncomingDataReceiveFinishedEvent,
                                         self.startPreProcessingPreopData)
    self.preopTransferWindow.show()

  def cleanupPreopDICOMReceiver(self):
    if self.preopTransferWindow:
      self.preopTransferWindow.hide()
      self.preopTransferWindow.removeEventObservers()
      self.preopTransferWindow = None

  def onPreopTransferMessageBoxCanceled(self, caller, event):
    self.clearData()

  def continueWithoutPreopData(self, caller, event):
    self.logic.usePreopData = False
    self.cleanupPreopDICOMReceiver()
    self.simulatePreopPhaseButton.enabled = False
    self.simulateIntraopPhaseButton.enabled = True
    self.intraopDataDir = self.intraopDICOMDataDirectory

  def startPreProcessingPreopData(self, caller=None, event=None):
    self.cleanupPreopDICOMReceiver()
    self.logic.intraopDataDir = self.intraopDICOMDataDirectory
    success = self.invokePreProcessing()
    if success:
      self.setSetting('InputLocation', None, moduleName="mpReview")
      self.layoutManager.selectModule("mpReview")
      mpReview = slicer.modules.mpReviewWidget
      self.setSetting('InputLocation', self.mpReviewPreprocessedOutput, moduleName="mpReview")
      mpReview.onReload()
      slicer.modules.mpReviewWidget.saveButton.clicked.connect(self.onReturnFromMpReview)
      self.layoutManager.selectModule(mpReview.moduleName)
    else:
      slicer.util.infoDisplay("No DICOM data could be processed. Please select another directory.",
                              windowTitle="SliceTracker")

  def onReturnFromMpReview(self):
    slicer.modules.mpReviewWidget.saveButton.clicked.disconnect(self.onReturnFromMpReview)
    self.layoutManager.selectModule(self.moduleName)
    slicer.mrmlScene.Clear(0)
    trainingMode = self.logic.trainingMode
    self.logic.stopSmartDICOMReceiver()
    self.logic.resetAndInitializeData()
    self.logic.trainingMode = trainingMode
    self.preopDataDir = self.logic.getFirstMpReviewPreprocessedStudy(self.mpReviewPreprocessedOutput)
    self.simulateIntraopPhaseButton.enabled = self.logic.trainingMode
    self.intraopDataDir = self.intraopDICOMDataDirectory

  def invokePreProcessing(self):
    if not os.path.exists(self.mpReviewPreprocessedOutput):
      self.logic.createDirectory(self.mpReviewPreprocessedOutput)
    from mpReviewPreprocessor import mpReviewPreprocessorLogic
    self.mpReviewPreprocessorLogic = mpReviewPreprocessorLogic()
    self.progress = slicer.util.createProgressDialog()
    self.progress.canceled.connect(self.mpReviewPreprocessorLogic.cancelProcess)
    self.mpReviewPreprocessorLogic.importStudy(self.preopDICOMDataDirectory, progressCallback=self.updateProgressBar)
    success = False
    if self.mpReviewPreprocessorLogic.patientFound():
      success = True
      self.mpReviewPreprocessorLogic.convertData(outputDir=self.mpReviewPreprocessedOutput, copyDICOM=False,
                                                 progressCallback=self.updateProgressBar)
    self.progress.canceled.disconnect(self.mpReviewPreprocessorLogic.cancelProcess)
    self.progress.close()
    return success

  def onOpenCaseButtonClicked(self):
    if not self.checkAndWarnUserIfCaseInProgress():
      return
    path = qt.QFileDialog.getExistingDirectory(self.parent.window(), "Select Case Directory", self.caseRootDir)
    if not path:
      return
    self.clearData()
    self.currentCaseDirectory = path
    if not self.logic.isCaseDirectoryValid(self.currentCaseDirectory):
      slicer.util.warningDisplay("The selected case directory seems not to be valid", windowTitle="SliceTracker")
      self.clearData()
    else:
      self.loadCaseData()

  def loadCaseData(self):
    from mpReview import mpReviewLogic
    savedSessions = self.logic.getSavedSessions(self.currentCaseDirectory)
    if len(savedSessions) > 0: # After registration(s) has been done
      if not self.openSavedSession(savedSessions):
        self.clearData()
    else:
      if os.path.exists(self.mpReviewPreprocessedOutput) and \
              mpReviewLogic.wasmpReviewPreprocessed(self.mpReviewPreprocessedOutput):
        self.preopDataDir = self.logic.getFirstMpReviewPreprocessedStudy(self.mpReviewPreprocessedOutput)
        self.intraopDataDir = self.intraopDICOMDataDirectory
      else:
        if len(os.listdir(self.preopDICOMDataDirectory)):
          self.startPreProcessingPreopData()
        elif len(os.listdir(self.intraopDICOMDataDirectory)):
          self.logic.usePreopData = False
          self.intraopDataDir = self.intraopDICOMDataDirectory
        else:
          self.startPreopDICOMReceiver()
    self.configureAllTargetDisplayNodes()

  def configureAllTargetDisplayNodes(self):
    allTargets = []
    if self.logic.preopTargets:
      allTargets.append(self.logic.preopTargets)
    for result in self.registrationResults.getResultsAsList():
      for targets in [t for t in result.targets.values() if t]:
        allTargets.append(targets)
      if result.approvedTargets:
        allTargets.append(result.approvedTargets)
    for targetNode in allTargets:
      self.logic.applyDefaultTargetDisplayNode(targetNode)

  def openSavedSession(self, sessions):
    # TODO: session selector and if not continuing, ask for creating a new one.
    latestCase = os.path.join(max(sessions, key=os.path.getmtime), "MRgBiopsy")
    self.logic.caseCompleted = self.logic.hasCaseBeenCompleted(latestCase)
    message = "A %s session has been found for the selected case. Do you want to %s?" \
              % ("completed" if self.logic.caseCompleted else "started",
                 "open it" if self.logic.caseCompleted else "continue this session")
    if slicer.util.confirmYesNoDisplay(message):
      self.continueOldCase = True
      self.logic.loadFromJSON(latestCase)
      if self.logic.usePreopData:
        self.preopDataDir = self.logic.getFirstMpReviewPreprocessedStudy(self.mpReviewPreprocessedOutput)
      else:
        if self.logic.preopTargets:
          self.setupPreopLoadedTargets()
      self.generatedOutputDirectory = latestCase
      self.intraopDataDir = os.path.join(self.currentCaseDirectory, "DICOM", "Intraop")
      return True
    else:
      return False

  def updateCaseWatchBox(self):
    value = self.currentCaseDirectory
    self.caseWatchBox.setInformation("CurrentCaseDirectory", os.path.relpath(value, self.caseRootDir), toolTip=value)
    preop = os.path.join(value, "DICOM", "Preop")
    self.caseWatchBox.setInformation("CurrentPreopDICOMDirectory", os.path.relpath(preop, self.caseRootDir),
                                     toolTip=preop)
    intraop = os.path.join(value, "DICOM", "Intraop")
    self.caseWatchBox.setInformation("CurrentIntraopDICOMDirectory", os.path.relpath(intraop, self.caseRootDir),
                                     toolTip=intraop)
    mpReviewPreprocessed = os.path.join(value, "mpReviewPreprocessed")
    self.caseWatchBox.setInformation("mpReviewDirectory", os.path.relpath(mpReviewPreprocessed, self.caseRootDir),
                                     toolTip=mpReviewPreprocessed)

  def onShowZFrameModelToggled(self, checked):
    self.logic.setZFrameVisibility(checked)

  def onShowZFrameTemplateToggled(self, checked):
    self.logic.setTemplateVisibility(checked)

  def onShowTemplatePathToggled(self, checked):
    self.logic.setTemplatePathVisibility(checked)

  def onShowNeedlePathToggled(self, checked):
    self.logic.setNeedlePathVisibility(checked)

  def onShowAnnotationsToggled(self, checked):
    allSliceAnnotations = self.sliceAnnotations[:]

    for attr in ["zFrameInstructionAnnotation", "registrationResultOldImageAnnotation",
                 "registrationResultNewImageAnnotation"]:
      try:
        annotation = getattr(self, attr)
        if annotation:
          allSliceAnnotations.append(annotation)
      except AttributeError:
        pass
    for annotation in allSliceAnnotations:
      if checked:
        annotation.show()
      else:
        annotation.hide()

  def onShowRegistrationDetails(self):
    if self.registrationGroupBox.visible:
      self.registrationGroupBox.hide()
      self.registrationGroupBox.enabled = True
    else:
      self.registrationGroupBox.show()
      self.registrationGroupBox.enabled = False

  def onLayoutChanged(self):
    self.redCompositeNode.SetLinkedControl(False)
    self.crosshairButton.checked = False
    isAllowedLayout = self.layoutManager.layout in self.ALLOWED_LAYOUTS
    self.wlEffectsToolButton.enabled = isAllowedLayout
    self.wlEffectsToolButton.checked = False
    if isAllowedLayout:
      self.refreshZFrameTemplateViewNodes()
      if self.currentStep == self.STEP_EVALUATION:
        self.onLayoutChangedInEvaluationStep()
      elif self.currentStep == self.STEP_OVERVIEW:
        self.onLayoutChangedInOverviewStep()
      elif self.currentStep == self.STEP_SEGMENTATION:
        self.onLayoutChangedInSegmentationStep()

  def onLayoutChangedInEvaluationStep(self):
    self.disableTargetMovingMode()
    self.setupRegistrationResultView()
    self.onRegistrationResultSelected(self.currentResult.name)
    self.onOpacitySpinBoxChanged(self.opacitySpinBox.value)
    self.crosshairButton.checked = self.layoutManager.layout == self.LAYOUT_FOUR_UP
    self.setTargetVisibility(self.logic.preopTargets, show=self.layoutManager.layout != self.LAYOUT_FOUR_UP)

  def onLayoutChangedInOverviewStep(self):
    self.disableTargetMovingMode()
    selectedSeries = self.intraopSeriesSelector.currentText
    if selectedSeries != "":
      if self.layoutManager.layout == self.LAYOUT_FOUR_UP:
        self.redCompositeNode.SetLinkedControl(True)
        result = self.registrationResults.getResult(self.intraopSeriesSelector.currentText)
        if not result:
          volume = self.logic.getOrCreateVolumeForSeries(selectedSeries)
          self.setBackgroundToVolumeID(volume.GetID())
        elif self.registrationResults.registrationResultWasApprovedOrRejected(self.intraopSeriesSelector.currentText):
          self.setupRegistrationResultView()
          self.onRegistrationResultSelected(result.name)
        self.setTargetVisibility(self.logic.preopTargets, show=False)
        self.crosshairButton.checked = self.layoutManager.layout == self.LAYOUT_FOUR_UP
      elif self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
        self.onIntraopSeriesSelectionChanged(selectedSeries)

  def onLayoutChangedInSegmentationStep(self):
    if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
      self.setupSideBySideSegmentationView()
    elif self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      self.removeMissingPreopDataAnnotation()
      self.setBackgroundToVolumeID(self.logic.currentIntraopVolume.GetID())
    self.refreshClippingModelViewNodes()

  def refreshClippingModelViewNodes(self):
    sliceNodes = [self.yellowSliceNode] if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE else \
      [self.redSliceNode, self.yellowSliceNode, self.greenSliceNode]
    nodes = [self.volumeClipToLabelWidget.logic.clippingModelNode, self.volumeClipToLabelWidget.logic.inputMarkupNode]
    for node in [n for n in nodes if n]:
      self.refreshViewNodeIDs(node, sliceNodes)

  def refreshZFrameTemplateViewNodes(self):
    sliceNodes = []
    if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
      sliceNodes = [self.yellowSliceNode]
    elif self.layoutManager.layout == self.LAYOUT_RED_SLICE_ONLY:
      sliceNodes = [self.redSliceNode]
    self.refreshViewNodeIDs(self.logic.pathModelNode, sliceNodes)

  def removeMissingPreopDataAnnotation(self):
    if self.segmentationNoPreopAnnotation:
      self.segmentationNoPreopAnnotation.remove()
      self.segmentationNoPreopAnnotation = None

  def addMissingPreopDataAnnotation(self, widget):
    self.removeMissingPreopDataAnnotation()
    self.segmentationNoPreopAnnotation = SliceAnnotation(widget, self.MISSING_PREOP_ANNOTATION_TEXT, opacity=0.7,
                                                         color=(1, 0, 0))

  def setupSideBySideSegmentationView(self):
    # TODO: red slice view should not be possible to set target
    coverProstate = self.registrationResults.getMostRecentApprovedCoverProstateRegistration()
    redVolume = coverProstate.fixedVolume if self.logic.retryMode and coverProstate else self.logic.preopVolume
    redLabel = coverProstate.fixedLabel if self.logic.retryMode and coverProstate else self.logic.preopLabel

    if redVolume and redLabel:
      self.redCompositeNode.SetBackgroundVolumeID(redVolume.GetID())
      self.redCompositeNode.SetLabelVolumeID(redLabel.GetID())
    else:
      self.redCompositeNode.SetBackgroundVolumeID(None)
      self.redCompositeNode.SetLabelVolumeID(None)
      self.addMissingPreopDataAnnotation(self.redWidget)
    self.yellowCompositeNode.SetBackgroundVolumeID(self.logic.currentIntraopVolume.GetID())
    self.setAxialOrientation()

    if redVolume and redLabel:
      self.redSliceNode.SetUseLabelOutline(True)
      self.redSliceNode.RotateToVolumePlane(redVolume)

  def uncheckLayoutButtons(self):
    for button in self.layoutButtonGroup.buttons():
      button.checked = False

  def onRegistrationButtonChecked(self, buttonId):
    self.disableTargetMovingMode()
    if buttonId == 1:
      self.displayRegistrationResultsByType(registrationType="rigid")
    elif buttonId == 2:
      if not self.currentResult.affineTargets:
        return self.showBSplineResultButton.click()
      self.displayRegistrationResultsByType(registrationType="affine")
    elif buttonId == 3:
      self.displayRegistrationResultsByType(registrationType="bSpline")

  def onIntraopSeriesSelectionChanged(self, selectedSeries=None):
    if self.currentStep != self.STEP_OVERVIEW:
      return
    self.removeSliceAnnotations()
    trackingPossible = False
    if selectedSeries:
      trackingPossible = self.logic.isTrackingPossible(selectedSeries)
      self.showTemplatePathButton.checked = trackingPossible and self.getSetting("COVER_PROSTATE") in selectedSeries
      self.setIntraopSeriesButtons(trackingPossible, selectedSeries)
      self.configureViewersForSelectedIntraopSeries(selectedSeries)
      self.updateSliceAnnotations(selectedSeries)
      self.updateDisplacementChartOnSeriesSelectionChange(selectedSeries)
    self.updateIntraopSeriesSelectorColor(selectedSeries)
    self.updateLayoutButtons(trackingPossible, selectedSeries)

  def setIntraopSeriesButtons(self, trackingPossible, selectedSeries):
    trackingPossible = trackingPossible if not self.logic.caseCompleted else False
    self.trackTargetsButton.setEnabled(trackingPossible)
    self.skipIntraopSeriesButton.setEnabled(trackingPossible and self.logic.isEligibleForSkipping(selectedSeries))

  def updateLayoutButtons(self, trackingPossible, selectedSeries=None):
    self.redOnlyLayoutButton.enabled = True
    self.sideBySideLayoutButton.enabled = True
    if selectedSeries:
      currentSeriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)
      hasResult = len(self.registrationResults.getResultsBySeriesNumber(currentSeriesNumber)) > 0
      isApprovedOrRejected = self.registrationResults.registrationResultWasApprovedOrRejected(selectedSeries)
      isCoverProstateAndNoPreop = self.getSetting("COVER_PROSTATE") in selectedSeries and not self.logic.usePreopData
      self.redOnlyLayoutButton.enabled = not (trackingPossible or isApprovedOrRejected) or not hasResult or isCoverProstateAndNoPreop
      self.sideBySideLayoutButton.enabled = not trackingPossible and isApprovedOrRejected and not isCoverProstateAndNoPreop

  def updateIntraopSeriesSelectorColor(self, selectedSeries):
    if selectedSeries is None:
      self.intraopSeriesSelector.setStyleSheet("")
      return
    style = STYLE.YELLOW_BACKGROUND
    if not self.logic.isTrackingPossible(selectedSeries):
      if self.registrationResults.registrationResultWasApproved(selectedSeries) or \
              (self.logic.zFrameRegistrationSuccessful and self.getSetting("COVER_TEMPLATE") in selectedSeries):
        style = STYLE.GREEN_BACKGROUND
      elif self.registrationResults.registrationResultWasSkipped(selectedSeries):
        style = STYLE.RED_BACKGROUND
      elif self.registrationResults.registrationResultWasRejected(selectedSeries):
        style = STYLE.GRAY_BACKGROUND
    self.intraopSeriesSelector.setStyleSheet(style)

  def updateSliceAnnotations(self, selectedSeries):
    if not self.logic.isTrackingPossible(selectedSeries):
      annotationText = None
      if self.registrationResults.registrationResultWasApproved(selectedSeries):
        annotationText = self.APPROVED_RESULT_TEXT_ANNOTATION
      elif self.registrationResults.registrationResultWasRejected(selectedSeries):
        annotationText = self.REJECTED_RESULT_TEXT_ANNOTATION
      if annotationText:
        self.sliceAnnotations.append(SliceAnnotation(self.yellowWidget, annotationText, fontSize=15, yPos=20))
      if self.registrationResults.registrationResultWasSkipped(selectedSeries):
        self.sliceAnnotations.append(SliceAnnotation(self.redWidget, self.SKIPPED_RESULT_TEXT_ANNOTATION,
                                                       fontSize=15, yPos=20))
    self.onShowAnnotationsToggled(self.showAnnotationsButton.checked)

  def configureViewersForSelectedIntraopSeries(self, selectedSeries):
    firstRun = self.intraopWatchBox.sourceFile is None
    self.intraopWatchBox.sourceFile = self.logic.loadableList[selectedSeries][0]
    if firstRun:
      if not self.logic.usePreopData:
        self.patientWatchBox.sourceFile = self.logic.loadableList[selectedSeries][0]
      self.updateOutputFolder()
    if self.registrationResults.registrationResultWasApproved(selectedSeries) or \
            self.registrationResults.registrationResultWasRejected(selectedSeries):
      if self.getSetting("COVER_PROSTATE") in selectedSeries and not self.logic.usePreopData:
        self.setupRedSlicePreview(selectedSeries)
      else:
        self.setupSideBySideRegistrationView()
    else:
      self.setupRedSlicePreview(selectedSeries)

  def resetVisualEffects(self):
    self.flickerCheckBox.checked = False
    self.rockCheckBox.checked = False
    self.useRevealCursorButton.enabled = False
    self.useRevealCursorButton.checked = False

  def setupFourUpView(self, volume):
    self.disableTargetTable()
    self.setBackgroundToVolumeID(volume.GetID())
    self.layoutManager.setLayout(self.LAYOUT_FOUR_UP)

  def setupRedSlicePreview(self, selectedSeries):
    self.layoutManager.setLayout(self.LAYOUT_RED_SLICE_ONLY)
    self.hideAllTargets()
    try:
      result = self.registrationResults.getResultsBySeries(selectedSeries)[0]
      volume = result.fixedVolume
    except IndexError:
      result = None
      volume = self.logic.getOrCreateVolumeForSeries(selectedSeries)
    self.setBackgroundToVolumeID(volume.GetID())

    if result and self.getSetting("COVER_PROSTATE") in selectedSeries and not self.logic.usePreopData:
      self.currentResult = selectedSeries
      self.currentTargets = self.currentResult.approvedTargets
      self.refreshViewNodeIDs(self.currentTargets, [self.redSliceNode])
      self.showCurrentTargets()
      self.selectLastSelectedTarget()
    elif self.getSetting("VIBE_IMAGE") in selectedSeries:
      seriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)
      mostRecentApprovedTargets = self.registrationResults.getMostRecentApprovedTargetsPriorTo(seriesNumber)
      if mostRecentApprovedTargets:
        self.currentTargets = mostRecentApprovedTargets
        self.refreshViewNodeIDs(self.currentTargets, [])
        self.showCurrentTargets()
        self.selectLastSelectedTarget()
    else:
      self.disableTargetTable()

  def setupSideBySideRegistrationView(self):
    for result in self.registrationResults.getResultsBySeries(self.intraopSeriesSelector.currentText):
      if result.approved or result.rejected:
        self.setupRegistrationResultView(layout=self.LAYOUT_SIDE_BY_SIDE)
        if result.rejected:
          self.onRegistrationResultSelected(result.name, registrationType='bSpline')
        elif result.approved and result.approvedTargets:
          self.onRegistrationResultSelected(result.name, showApproved=True)
        break

  def onTargetTableSelectionChanged(self, modelIndex=None):
    if not modelIndex:
      self.getAndSelectTargetFromTable()
      return
    if self.moveTargetMode is True and modelIndex != self.currentlyMovedTargetModelIndex:
      self.disableTargetMovingMode()
    self.lastSelectedModelIndex = modelIndex
    if not self.currentTargets:
      self.currentTargets = self.logic.preopTargets
    self.jumpSliceNodesToNthTarget(modelIndex.row())
    self.updateNeedleModel()
    self.targetTableModel.currentTargetIndex = self.lastSelectedModelIndex.row()
    self.updateSelection(self.lastSelectedModelIndex.row())

  def updateSelection(self, row):
    self.targetTable.clearSelection()
    first = self.targetTable.model().index(row, 0)
    second = self.targetTable.model().index(row, 1)

    selection = qt.QItemSelection(first, second)
    self.targetTable.selectionModel().select(selection, qt.QItemSelectionModel.Select)

  def jumpSliceNodesToNthTarget(self, targetIndex):
    currentTargetsSliceNodes = []
    if self.layoutManager.layout in [self.LAYOUT_RED_SLICE_ONLY, self.LAYOUT_SIDE_BY_SIDE]:
      targets = self.logic.preopTargets
      if self.getSetting("VIBE_IMAGE") in self.intraopSeriesSelector.currentText:
        targets = self.targetTableModel.targetList
      self.jumpSliceNodeToTarget(self.redSliceNode, targets, targetIndex)
      self.setTargetSelected(targets, selected=False)
      targets.SetNthFiducialSelected(targetIndex, True)

    if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
      currentTargetsSliceNodes = [self.yellowSliceNode]
    elif self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      currentTargetsSliceNodes = [self.redSliceNode, self.yellowSliceNode, self.greenSliceNode]

    for sliceNode in currentTargetsSliceNodes:
      self.jumpSliceNodeToTarget(sliceNode, self.currentTargets, targetIndex)
    self.setTargetSelected(self.currentTargets, selected=False)
    self.currentTargets.SetNthFiducialSelected(targetIndex, True)

  def onMoveTargetRequest(self, modelIndex):
    if self.moveTargetMode:
      self.disableTargetMovingMode()
      if self.currentlyMovedTargetModelIndex != modelIndex:
        self.onMoveTargetRequest(modelIndex)
      self.currentlyMovedTargetModelIndex = None
    else:
      self.currentlyMovedTargetModelIndex = modelIndex
      self.enableTargetMovingMode()

  def enableTargetMovingMode(self):
    self.clearTargetMovementObserverAndAnnotations()
    targetName = self.targetTableModel.targetList.GetNthFiducialLabel(self.currentlyMovedTargetModelIndex.row())

    widgets = [self.yellowWidget] if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE else \
                 [self.redWidget, self.yellowWidget, self.greenWidget]
    for widget in widgets:
      sliceView = widget.sliceView()
      interactor = sliceView.interactorStyle().GetInteractor()
      observer = interactor.AddObserver(vtk.vtkCommand.LeftButtonReleaseEvent, self.onViewerClickEvent)
      sliceView.setCursor(qt.Qt.CrossCursor)
      annotation = SliceAnnotation(widget, "Target Movement Mode (%s)" % targetName, opacity=0.5,
                                   verticalAlign="top", horizontalAlign="center")
      self.mouseReleaseEventObservers[widget] = (observer, annotation)
    self.moveTargetMode = True

  def disableTargetMovingMode(self):
    self.clearTargetMovementObserverAndAnnotations()
    self.mouseReleaseEventObservers = {}
    self.moveTargetMode = False

  def clearTargetMovementObserverAndAnnotations(self):
    for widget, (observer, annotation) in self.mouseReleaseEventObservers.iteritems():
      sliceView = widget.sliceView()
      interactor = sliceView.interactorStyle().GetInteractor()
      interactor.RemoveObserver(observer)
      sliceView.setCursor(qt.Qt.ArrowCursor)
      annotation.remove()

  def onViewerClickEvent(self, observee=None, event=None):
    posXY = observee.GetEventPosition()
    widget = self.getWidgetForInteractor(observee)
    posRAS = self.xyToRAS(widget.sliceLogic(), posXY)
    if self.currentlyMovedTargetModelIndex is not None:
      self.currentResult.isGoingToBeMoved(self.targetTableModel.targetList, self.currentlyMovedTargetModelIndex.row())
      self.targetTableModel.targetList.SetNthFiducialPositionFromArray(self.currentlyMovedTargetModelIndex.row(), posRAS)
    self.disableTargetMovingMode()

  def getWidgetForInteractor(self, observee):
    for widget in self.mouseReleaseEventObservers.keys():
      sliceView = widget.sliceView()
      interactor = sliceView.interactorStyle().GetInteractor()
      if interactor is observee:
        return widget
    return None

  def xyToRAS(self, sliceLogic, xyPoint):
    sliceNode = sliceLogic.GetSliceNode()
    rast = sliceNode.GetXYToRAS().MultiplyPoint(xyPoint + (0,1,))
    return rast[:3]

  def updateNeedleModel(self, caller=None, event=None):
    if self.showNeedlePathButton.checked and self.logic.zFrameRegistrationSuccessful:
      modelIndex = self.lastSelectedModelIndex
      try:
        start, end = self.targetTableModel.currentGuidanceComputation.needleStartEndPositions[modelIndex.row()]
        self.logic.createNeedleModelNode(start, end)
      except KeyError:
        self.logic.removeNeedleModelNode()

  def getAndSelectTargetFromTable(self):
    modelIndex = None
    if self.lastSelectedModelIndex:
      modelIndex = self.lastSelectedModelIndex
    else:
      if self.targetTableModel.rowCount():
        modelIndex = self.targetTableModel.index(0,0)
    if modelIndex:
      self.targetTable.clicked(modelIndex)

  def updateDisplacementChartTargetSelectorTable(self):
    self.targetDisplacementChart.DisplacementChartTargetSelector.blockSignals(True)
    self.targetDisplacementChart.DisplacementChartTargetSelector.clear()
    if self.targetTableModel.rowCount():
      rows = self.targetTableModel.rowCount()
      for i in range(rows):
        self.targetDisplacementChart.DisplacementChartTargetSelector.addItem(self.targetTableModel.targetList.GetNthFiducialLabel(i))
    self.targetDisplacementChart.DisplacementChartTargetSelector.setCurrentIndex(-1)
    self.targetDisplacementChart.DisplacementChartTargetSelector.blockSignals(False)

  def calculateTargetDisplacement(self, prevTargets, currTargets):
    prevPos = [0.0, 0.0, 0.0]
    currPos = [0.0, 0.0, 0.0]
    for i in range(0, currTargets.GetNumberOfFiducials()):
      if currTargets.GetNthFiducialLabel(i) == self.targetDisplacementChart.DisplacementChartTargetSelector.currentText:
        currTargets.GetNthFiducialPosition(i, currPos)
        prevTargets.GetNthFiducialPosition(i, prevPos)
        displacement = [currPos[0] - prevPos[0], currPos[1] - prevPos[1], currPos[2] - prevPos[2]]
        return displacement

  def onDisplacementChartTargetSelectionChanged(self):
    if self.targetDisplacementChart.DisplacementChartTargetSelector.currentText != self.targetDisplacementChart.lastSelectedTarget:
      self.targetDisplacementChart.resetAndInitializeChart()
      if self.targetDisplacementChart.DisplacementChartTargetSelector.currentText:
        self.targetDisplacementChart.lastSelectedTarget = self.targetDisplacementChart.DisplacementChartTargetSelector.currentText
      results = sorted(self.registrationResults.getResultsAsList(), key=lambda s: s.seriesNumber)
      prevIndex = 0
      for currIndex, currResult in enumerate(results[1:], 1):
        if results[prevIndex].approved and currResult.approved:
          prevTargets = results[prevIndex].approvedTargets
          currTargets = currResult.approvedTargets
          displacement = self.calculateTargetDisplacement(prevTargets, currTargets)
          self.targetDisplacementChart.addPlotPoints([displacement])
          prevIndex = currIndex
        elif currResult.approved:
          prevIndex = currIndex
        else:
          prevIndex += 1

  def updateDisplacementChartOnSeriesSelectionChange(self, selectedSeries):
    if self.getSetting("COVER_PROSTATE") in selectedSeries or self.targetDisplacementChart.DisplacementChartTargetSelector.count == 0:
      self.targetDisplacementChart.DisplacementChartTargetSelector.setEnabled(False)
      self.targetDisplacementChart.lastSelectedTarget = None
      self.targetDisplacementChart.resetAndInitializeChart()
    elif not self.targetDisplacementChart.DisplacementChartTargetSelector.isEnabled():
      self.targetDisplacementChart.DisplacementChartTargetSelector.setEnabled(True)
    if self.targetDisplacementChart.lastSelectedTarget is not None:
      lastTargetIndex = self.targetDisplacementChart.DisplacementChartTargetSelector.findText(self.targetDisplacementChart.lastSelectedTarget)
      if lastTargetIndex is not -1:
        self.targetDisplacementChart.DisplacementChartTargetSelector.setCurrentIndex(lastTargetIndex)
      else:
        self.targetDisplacementChart.resetAndInitializeChart()



  def removeSliceAnnotations(self):
    for annotation in self.sliceAnnotations:
      annotation.remove()
    self.sliceAnnotations = []
    for attr in ["zFrameInstructionAnnotation", "registrationResultOldImageAnnotation",
                 "registrationResultNewImageAnnotation"]:
      try:
        annotation = getattr(self, attr)
        if annotation:
          annotation.remove()
          setattr(self, attr, None)
      except AttributeError:
        pass
    self.removeZFrameInstructionAnnotation()
    self.clearTargetMovementObserverAndAnnotations()
    self.removeMissingPreopDataAnnotation()

  def addSideBySideSliceAnnotations(self):
    self.sliceAnnotations.append(SliceAnnotation(self.redWidget, self.LEFT_VIEWER_SLICE_ANNOTATION_TEXT, fontSize=30,
                                                 yPos=55))
    self.sliceAnnotations.append(SliceAnnotation(self.yellowWidget, self.RIGHT_VIEWER_SLICE_ANNOTATION_TEXT, yPos=55,
                                                 fontSize=30))
    self.registrationResultNewImageAnnotation = SliceAnnotation(self.yellowWidget,
                                                                self.RIGHT_VIEWER_SLICE_NEEDLE_IMAGE_ANNOTATION_TEXT, yPos=35,
                                                                opacity=0.0, color=(0,0.5,0))
    self.registrationResultOldImageAnnotation = SliceAnnotation(self.yellowWidget,
                                                                self.RIGHT_VIEWER_SLICE_TRANSFORMED_ANNOTATION_TEXT, yPos=35)
    self.registrationResultStatusAnnotation = None
    self.onShowAnnotationsToggled(self.showAnnotationsButton.checked)

  def addFourUpSliceAnnotations(self):
    self.sliceAnnotations.append(SliceAnnotation(self.redWidget, self.RIGHT_VIEWER_SLICE_ANNOTATION_TEXT, yPos=50, fontSize=20))
    self.registrationResultNewImageAnnotation = SliceAnnotation(self.redWidget,
                                                                self.RIGHT_VIEWER_SLICE_NEEDLE_IMAGE_ANNOTATION_TEXT,
                                                                yPos=35, opacity=0.0, color=(0,0.5,0), fontSize=15)
    self.registrationResultOldImageAnnotation = SliceAnnotation(self.redWidget,
                                                                self.RIGHT_VIEWER_SLICE_TRANSFORMED_ANNOTATION_TEXT,
                                                                yPos=35, fontSize=15)
    self.registrationResultStatusAnnotation = None
    self.onShowAnnotationsToggled(self.showAnnotationsButton.checked)

  def onRevealToggled(self, checked):
    if self.revealCursor:
      self.revealCursor.tearDown()
    if checked:
      import CompareVolumes
      self.revealCursor = CompareVolumes.LayerReveal()

  def setOldNewIndicatorAnnotationOpacity(self, value):
    if self.registrationResultNewImageAnnotation:
      self.registrationResultNewImageAnnotation.opacity = value
    if self.registrationResultOldImageAnnotation:
      self.registrationResultOldImageAnnotation.opacity = 1.0 - value

  def showOpacitySliderPopup(self, show):
    if show:
      if not self.opacitySliderPopup.visible:
        self.opacitySpinBox.enabled = False
        self.opacitySlider.enabled = False
        self.opacitySliderPopup.show()
        self.opacitySliderPopup.autoHide = False
    else:
      self.opacitySpinBox.enabled = True
      self.opacitySlider.enabled = True
      self.opacitySliderPopup.hide()
      self.opacitySliderPopup. autoHide = True

  def onRockToggled(self):
    if self.rockCheckBox.checked:
      self.startRocking()
    else:
      self.stopRocking()

  def startRocking(self):
    if self.flickerCheckBox.checked:
      self.flickerCheckBox.checked = False
    self.wlEffectsToolButton.checked = False
    self.wlEffectsToolButton.enabled = False
    self.rockTimer.start()
    self.opacitySpinBox.value = 0.5 + numpy.sin(self.rockCount / 10.) / 2.
    self.rockCount += 1

  def stopRocking(self):
    self.wlEffectsToolButton.enabled = True
    self.rockTimer.stop()
    self.opacitySpinBox.value = 1.0

  def onFlickerToggled(self):
    if self.flickerCheckBox.checked:
      self.startFlickering()
    else:
      self.stopFlickering()

  def startFlickering(self):
    if self.rockCheckBox.checked:
      self.rockCheckBox.checked = False
    self.wlEffectsToolButton.checked = False
    self.wlEffectsToolButton.enabled = False
    self.flickerTimer.start()
    self.opacitySpinBox.value = 1.0 if self.opacitySpinBox.value == 0.0 else 0.0

  def stopFlickering(self):
    self.wlEffectsToolButton.enabled = True
    self.flickerTimer.stop()
    self.opacitySpinBox.value = 1.0

  def onCompleteCaseButtonClicked(self):
    self.logic.caseCompleted = True
    self.save(showDialog=True)
    self.clearData()
    self.completeCaseButton.enabled = False

  def save(self, showDialog=False):
    if not os.path.exists(self.outputDir) or self.generatedOutputDirectory == "":
      slicer.util.infoDisplay("CRITICAL ERROR: You need to provide a valid output directory for saving data. Please make "
                              "sure to select one.", windowTitle="SliceTracker")
    else:
      if self.logic.saveSession(self.generatedOutputDirectory) and \
        self.volumeClipToLabelWidget.logic.save(self.generatedOutputDirectory):
        message = "Case data has been save successfully."
      else:
        message = "Case data could not be saved successfully. Please see log for further information."
      if showDialog:
        slicer.util.infoDisplay(message, windowTitle="SliceTracker")

  def inputsAreSet(self):
    return not (self.movingVolumeSelector.currentNode() is None and self.fixedVolumeSelector.currentNode() is None and
                self.movingLabelSelector.currentNode() is None and self.fixedLabelSelector.currentNode() is None and
                self.fiducialSelector.currentNode() is None)

  def updateIntraopSeriesSelectorTable(self):
    self.intraopSeriesSelector.blockSignals(True)
    self.seriesModel.clear()
    for series in self.logic.seriesList:
      sItem = qt.QStandardItem(series)
      self.seriesModel.appendRow(sItem)
      color = COLOR.YELLOW
      if self.registrationResults.registrationResultWasApproved(series) or \
        (self.getSetting("COVER_TEMPLATE") in series and self.logic.zFrameRegistrationSuccessful):
        color = COLOR.GREEN
      elif self.registrationResults.registrationResultWasSkipped(series):
        color = COLOR.RED
      elif self.registrationResults.registrationResultWasRejected(series):
        color = COLOR.GRAY
      self.seriesModel.setData(sItem.index(), color, qt.Qt.BackgroundRole)
    self.intraopSeriesSelector.setCurrentIndex(-1)
    self.intraopSeriesSelector.blockSignals(False)
    self.selectMostRecentEligibleSeries()

  def selectMostRecentEligibleSeries(self):
    if self.currentStep != self.STEP_OVERVIEW:
      self.intraopSeriesSelector.blockSignals(True)
    substring = self.getSetting("NEEDLE_IMAGE")
    index = -1
    if not self.registrationResults.getMostRecentApprovedCoverProstateRegistration():
      substring = self.getSetting("COVER_TEMPLATE") if not self.logic.zFrameRegistrationSuccessful else self.getSetting("COVER_PROSTATE")
    for item in list(reversed(range(len(self.logic.seriesList)))):
      series = self.seriesModel.item(item).text()
      if substring in series:
        if index != -1:
          if self.registrationResults.registrationResultWasApprovedOrRejected(series) or self.registrationResults.registrationResultWasSkipped(series):
            break
        index = self.intraopSeriesSelector.findText(series)
        break
      elif self.getSetting("VIBE_IMAGE") in series and index == -1:
        index = self.intraopSeriesSelector.findText(series)
    rowCount = self.intraopSeriesSelector.model().rowCount()
    self.intraopSeriesSelector.setCurrentIndex(index if index != -1 else (rowCount-1 if rowCount else -1))
    self.intraopSeriesSelector.blockSignals(False)

  def onRegistrationResultSelected(self, seriesText, registrationType=None, showApproved=False):
    self.disableTargetMovingMode()
    if not seriesText:
      return
    self.hideAllTargets()
    self.currentResult = seriesText
    self.showAffineResultButton.setEnabled(self.currentResult.affineTargets is not None)
    if registrationType:
      self.checkButtonByRegistrationType(registrationType)
    elif showApproved:
      self.displayApprovedRegistrationResults()
    elif self.registrationButtonGroup.checkedId() != -1:
      self.onRegistrationButtonChecked(self.registrationButtonGroup.checkedId())
    else:
      self.showBSplineResultButton.click()

  def checkButtonByRegistrationType(self, registrationType):
    for button in self.registrationButtonGroup.buttons():
      if button.name == registrationType:
        button.click()
        break

  def hideAllTargets(self):
    for result in self.registrationResults.getResultsAsList():
      for targetNode in [targets for targets in result.targets.values() if targets]:
        self.setTargetVisibility(targetNode, show=False)
      if result.approvedTargets:
        self.setTargetVisibility(result.approvedTargets, show=False)
    self.setTargetVisibility(self.logic.preopTargets, show=False)

  def displayApprovedRegistrationResults(self):
    self.displayRegistrationResults(self.currentResult.approvedTargets, self.currentResult.approvedRegistrationType)

  def displayRegistrationResultsByType(self, registrationType):
    self.displayRegistrationResults(self.currentResult.getTargets(registrationType), registrationType)

  def displayRegistrationResults(self, targets, registrationType):
    self.hideAllTargets()
    self.currentTargets = targets
    self.showCurrentTargets()
    self.setupRegistrationResultSliceViews(registrationType)
    self.setPreopTargetVisibility()
    self.selectLastSelectedTarget()

  def selectLastSelectedTarget(self):
    if not self.lastSelectedModelIndex:
      self.lastSelectedModelIndex = self.targetTableModel.index(0, 0)
    self.targetTable.clicked(self.lastSelectedModelIndex)

  def setPreopTargetVisibility(self):
    if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
      self.setTargetVisibility(self.logic.preopTargets, show=True)
    elif self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      self.setTargetVisibility(self.logic.preopTargets, show=False)

  @beforeRunProcessEvents
  def setDefaultFOV(self, sliceLogic, factor=0.5):
    sliceLogic.FitSliceToAll()
    FOV = sliceLogic.GetSliceNode().GetFieldOfView()
    self.setFOV(sliceLogic, [FOV[0] * factor, FOV[1] * factor, FOV[2]])

  def setupRegistrationResultSliceViews(self, registrationType):
    if self.layoutManager.layout in [self.LAYOUT_SIDE_BY_SIDE, self.LAYOUT_RED_SLICE_ONLY]:
      self.redCompositeNode.SetForegroundVolumeID(None)
      self.redCompositeNode.SetBackgroundVolumeID(self.logic.preopVolume.GetID())
      compositeNodes = []

    if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
      compositeNodes = [self.yellowCompositeNode]
    elif self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      compositeNodes = [self.redCompositeNode, self.yellowCompositeNode, self.greenCompositeNode]

    bgVolume = self.currentResult.getVolume(registrationType)
    bgVolume = bgVolume if bgVolume and self.logic.isVolumeExtentValid(bgVolume) else self.currentResult.fixedVolume

    for compositeNode in compositeNodes:
      compositeNode.SetForegroundVolumeID(self.currentResult.fixedVolume.GetID())
      compositeNode.SetBackgroundVolumeID(bgVolume.GetID())

    if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
      self.setAxialOrientation()
    elif self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      self.setDefaultOrientation()
      self.setTargetVisibility(self.logic.preopTargets, show=False)
    self.centerViewsToProstate()

  def centerViewsToProstate(self):
    if self.layoutManager.layout in [self.LAYOUT_SIDE_BY_SIDE, self.LAYOUT_FOUR_UP]:
      fixedCentroid = self.logic.getCentroidForLabel(self.currentResult.fixedLabel, self.logic.segmentedLabelValue)
      if self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE:
        movingCentroid = self.logic.getCentroidForLabel(self.logic.preopLabel, self.logic.segmentedLabelValue)
        if movingCentroid:
          self.redSliceNode.JumpSliceByCentering(movingCentroid[0], movingCentroid[1], movingCentroid[2])
        if fixedCentroid:
          self.yellowSliceNode.JumpSliceByCentering(fixedCentroid[0], fixedCentroid[1], fixedCentroid[2])
        self.setTargetVisibility(self.logic.preopTargets, show=True)
      elif self.layoutManager.layout == self.LAYOUT_FOUR_UP:
        if fixedCentroid:
          for sliceNode in [self.redSliceNode, self.yellowSliceNode, self.greenSliceNode]:
            sliceNode.JumpSliceByCentering(fixedCentroid[0], fixedCentroid[1], fixedCentroid[2])
    elif self.layoutManager.layout == self.LAYOUT_RED_SLICE_ONLY:
      movingCentroid = self.logic.getCentroidForLabel(self.logic.preopLabel, self.logic.segmentedLabelValue)
      self.redSliceNode.JumpSliceByCentering(movingCentroid[0], movingCentroid[1], movingCentroid[2])

  def centerLabelsOnVisibleSliceWidgets(self):
    for widget in self.getAllVisibleWidgets():
      compositeNode = widget.mrmlSliceCompositeNode()
      sliceNode = widget.sliceLogic().GetSliceNode()
      labelID = compositeNode.GetLabelVolumeID()
      if labelID:
        label =  slicer.mrmlScene.GetNodeByID(labelID)
        centroid = self.logic.getCentroidForLabel(label, self.logic.segmentedLabelValue)
        if centroid:
          sliceNode.JumpSliceByCentering(centroid[0], centroid[1], centroid[2])

  def showCurrentTargets(self):
    self.logic.applyDefaultTargetDisplayNode(self.currentTargets)
    self.setTargetVisibility(self.currentTargets)

  def setTargetVisibility(self, targetNode, show=True):
    self.markupsLogic.SetAllMarkupsVisibility(targetNode, show)

  def setTargetSelected(self, targetNode, selected=False):
    self.markupsLogic.SetAllMarkupsSelected(targetNode, selected)

  def loadPreopData(self):
    dicomFileName = self.logic.getFileList(self.preopDICOMDataDirectory)[0]
    self.patientWatchBox.sourceFile = os.path.join(self.preopDICOMDataDirectory, dicomFileName)
    self.currentID = self.patientWatchBox.getInformation("PatientID")

    message = self.loadMpReviewProcessedData()
    if message:
      slicer.util.warningDisplay(message, winowTitle="SliceTracker")
      return

    success = self.logic.loadT2Label() and self.logic.loadPreopVolume() and self.logic.loadPreopTargets()
    if not success:
      slicer.util.warningDisplay("Loading preop data failed.\nMake sure that the correct directory structure like mpReview "
                                 "explains is used. SliceTracker expects a volume, label and target")
      return

    self.movingLabelSelector.setCurrentNode(self.logic.preopLabel)
    self.logic.preopLabel.GetDisplayNode().SetAndObserveColorNodeID('vtkMRMLColorTableNode1')

    self.configureRedSliceNodeForPreopData()
    self.promptUserAndApplyBiasCorrectionIfNeeded()

    self.layoutManager.setLayout(self.LAYOUT_RED_SLICE_ONLY)
    self.setAxialOrientation()
    self.redSliceNode.RotateToVolumePlane(self.logic.preopVolume)
    self.setupPreopLoadedTargets()

  def loadMpReviewProcessedData(self):
    from mpReview import mpReviewLogic
    resourcesDir = os.path.join(self.preopDataDir, 'RESOURCES')\

    if not os.path.exists(resourcesDir):
      message = "The selected directory does not fit the mpReview directory structure. Make sure that you select the " \
                "study root directory which includes directories RESOURCES"
      return message

    self.progress = slicer.util.createProgressDialog(maxiumu=len(os.listdir(resourcesDir)))
    seriesMap, metaFile = mpReviewLogic.loadMpReviewProcessedData(resourcesDir,
                                                                  updateProgressCallback=self.updateProgressBar)
    self.progress.delete()

    # TODO: targets shall be reference image specific in mpReview
    self.logic.preopTargetsPath = os.path.join(self.preopDataDir, 'Targets')

    self.logic.loadPreopImageAndSegmentation(seriesMap)

    if self.logic.preopSegmentationPath is None:
      message = "No segmentations found.\nMake sure that you used mpReview for segmenting the prostate first and using " \
                "its output as the preop data input here."
      return message
    return None

  def configureRedSliceNodeForPreopData(self):
    self.redSliceNode.RotateToVolumePlane(self.logic.preopLabel)
    self.redSliceNode.SetUseLabelOutline(True)
    self.redSliceNode.SetOrientationToAxial()
    self.redCompositeNode.SetLabelOpacity(1)
    self.centerViewsToProstate()

  def setupPreopLoadedTargets(self):
    self.setTargetVisibility(self.logic.preopTargets, show=True)
    self.targetTableModel.targetList = self.logic.preopTargets
    self.fiducialSelector.setCurrentNode(self.logic.preopTargets)
    self.logic.applyDefaultTargetDisplayNode(self.logic.preopTargets)
    self.markupsLogic.JumpSlicesToNthPointInMarkup(self.logic.preopTargets.GetID(), 0)
    self.targetTable.selectRow(0)
    self.targetTable.enabled = True
    self.updateDisplacementChartTargetSelectorTable()

  def promptUserAndApplyBiasCorrectionIfNeeded(self):
    if not self.continueOldCase:
      if slicer.util.confirmYesNoDisplay("Was an endorectal coil used for preop image acquisition?",
                                         windowTitle="SliceTracker"):
        progress = slicer.util.createProgressDialog(maximum=2, value=1)
        progress.labelText = '\nBias Correction'
        self.logic.applyBiasCorrection()
        progress.setValue(2)
        progress.close()
    self.movingVolumeSelector.setCurrentNode(self.logic.preopVolume)

  def getAllNewSeriesNumbersIncludingPatientIDs(self, fileList):
    seriesNumberPatientID = {}
    for currentFile in [os.path.join(self.intraopDataDir, f) for f in fileList]:
      seriesNumber = int(self.logic.getDICOMValue(currentFile, DICOMTAGS.SERIES_NUMBER))
      if seriesNumber not in seriesNumberPatientID.keys():
        seriesNumberPatientID[seriesNumber]= {"PatientID":
                                                self.logic.getDICOMValue(currentFile, DICOMTAGS.PATIENT_ID),
                                              "PatientName":
                                                self.logic.getDICOMValue(currentFile, DICOMTAGS.PATIENT_NAME)}
    return seriesNumberPatientID

  def verifyPatientIDEquality(self, seriesNumberPatientID):
    acceptedSeriesNumbers = []
    for seriesNumber, info in seriesNumberPatientID.iteritems():
      patientID = info["PatientID"]
      patientName = info["PatientName"]
      if patientID is not None and patientID != self.currentID:
        currentPatientName = self.patientWatchBox.getInformation("PatientName")
        message = 'WARNING:\n' \
                  'Current case:\n' \
                  '  Patient ID: {0}\n' \
                  '  Patient Name: {1}\n' \
                  'Received image\n' \
                  '  Patient ID: {2}\n' \
                  '  Patient Name : {3}\n\n' \
                  'Do you want to keep this series? '.format(self.currentID, currentPatientName, patientID, patientName)
        if not slicer.util.confirmYesNoDisplay(message, title="Patient's ID Not Matching", windowTitle="SliceTracker"):
          self.logic.deleteSeriesFromSeriesList(seriesNumber)
          continue
      acceptedSeriesNumbers.append(seriesNumber)
    acceptedSeriesNumbers.sort()
    return acceptedSeriesNumbers

  def setBackgroundToVolumeID(self, volumeID):
    for compositeNode in [self.redCompositeNode, self.yellowCompositeNode, self.greenCompositeNode]:
      compositeNode.SetLabelVolumeID(None)
      compositeNode.SetForegroundVolumeID(None)
      compositeNode.SetBackgroundVolumeID(volumeID)
    self.setDefaultOrientation()

  def hideAllLabels(self):
    for compositeNode in [self.redCompositeNode, self.yellowCompositeNode, self.greenCompositeNode]:
      compositeNode.SetLabelVolumeID(None)

  def disableEditorWidgetAndResetEditorTool(self, enabledButton=False):
    self.editorWidgetParent.hide()
    self.editorParameterNode.SetParameter('effect', 'DefaultTool')
    self.editorWidgetButton.setEnabled(enabledButton)

  def onOpacitySpinBoxChanged(self, value):
    if self.opacitySlider.value != value:
      self.opacitySlider.value = value
    self.onOpacityChanged(value)

  def onOpacitySliderChanged(self, value):
    if self.opacitySpinBox.value != value:
      self.opacitySpinBox.value = value

  def onOpacityChanged(self, value):
    if self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      self.redCompositeNode.SetForegroundOpacity(value)
      self.greenCompositeNode.SetForegroundOpacity(value)
    self.yellowCompositeNode.SetForegroundOpacity(value)
    self.setOldNewIndicatorAnnotationOpacity(value)

  def connectKeyEventObservers(self):
    interactors = [self.yellowSliceViewInteractor]
    if self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      interactors += [self.redSliceViewInteractor, self.greenSliceViewInteractor]
    for interactor in interactors:
      self.keyPressEventObservers[interactor] = interactor.AddObserver("KeyPressEvent", self.onKeyPressedEvent)
      self.keyReleaseEventObservers[interactor] = interactor.AddObserver("KeyReleaseEvent", self.onKeyReleasedEvent)

  def disconnectKeyEventObservers(self):
    for interactor, tag in self.keyPressEventObservers.iteritems():
      interactor.RemoveObserver(tag)
    for interactor, tag in self.keyReleaseEventObservers.iteritems():
      interactor.RemoveObserver(tag)

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def calcCursorTargetsDistance(self, caller, event, callData):
    if not self.targetTableModel.computeCursorDistances:
      return
    ras = [0.0,0.0,0.0]
    xyz = [0.0,0.0,0.0]
    insideView = callData.GetCursorPositionRAS(ras)
    sliceNode = callData.GetCursorPositionXYZ(xyz)

    if not insideView or sliceNode not in [self.redSliceNode, self.yellowSliceNode, self.greenSliceNode]:
      self.targetTableModel.cursorPosition = None
      return

    if (self.currentStep == self.STEP_EVALUATION and (self.layoutManager.layout == self.LAYOUT_FOUR_UP or
       (self.layoutManager.layout == self.LAYOUT_SIDE_BY_SIDE and sliceNode is self.yellowSliceNode))) or \
      (self.currentStep != self.STEP_EVALUATION and sliceNode is self.yellowSliceNode):
      self.targetTableModel.cursorPosition = ras

  def onKeyPressedEvent(self, caller, event):
    if not caller.GetKeySym() == 'd':
      return
    if not self.targetTableModel.computeCursorDistances:
      self.targetTableModel.computeCursorDistances = True
      # self.calcCursorTargetsDistance()
      self.crosshairButton.addEventObserver(self.crosshairButton.CursorPositionModifiedEvent, self.calcCursorTargetsDistance)

  def onKeyReleasedEvent(self, caller, event):
    if not caller.GetKeySym() == 'd':
      return
    self.targetTableModel.computeCursorDistances = False
    self.crosshairButton.removeEventObserver(self.crosshairButton.CursorPositionModifiedEvent, self.calcCursorTargetsDistance)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onRatingDone(self, caller, event, callData):
    rating = int(callData)
    self.currentResult.score = rating
    self.openOverviewStep()

  def openOverviewStep(self):
    self.currentStep = self.STEP_OVERVIEW
    self.logic.removeNeedleModelNode()
    self.targetTableModel.computeCursorDistances = False
    self.targetDisplacementChart.resetAndInitializeChart()
    self.save()
    self.disconnectKeyEventObservers()
    self.hideAllLabels()
    self.updateIntraopSeriesSelectorTable()
    self.removeSliceAnnotations()
    self.resetVisualEffects()

  def openTargetingStep(self):
    self.currentStep = self.STEP_TARGETING
    self.setupFourUpView(self.logic.currentIntraopVolume)
    self.fiducialsWidget.createNewFiducialNode(name="IntraopTargets")
    self.fiducialsWidget.startPlacing()

  def onRetryRegistrationButtonClicked(self):
    self.logic.retryMode = True
    self.onTrackTargetsButtonClicked()

  def onApproveRegistrationResultButtonClicked(self):
    self.currentResult.approve(registrationType=self.registrationButtonGroup.checkedButton().name)

    if self.ratingWindow.isRatingEnabled():
      self.ratingWindow.show(disableWidget=self.parent)
    else:
      self.openOverviewStep()

  def onRejectRegistrationResultButtonClicked(self):
    results = self.registrationResults.getResultsBySeriesNumber(self.currentResult.seriesNumber)
    for result in results:
      result.reject()
    self.openOverviewStep()

  def onSkipIntraopSeriesButtonClicked(self):
    self.skipAllUnregisteredPreviousSeries(self.intraopSeriesSelector.currentText)
    self.skipSeries(self.intraopSeriesSelector.currentText)
    self.save()
    self.updateIntraopSeriesSelectorTable()

  def skipAllUnregisteredPreviousSeries(self, selectedSeries):
    selectedSeriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)
    for series in [series for series in self.logic.seriesList if not self.getSetting("COVER_TEMPLATE") in series]:
      currentSeriesNumber = RegistrationResult.getSeriesNumberFromString(series)
      if currentSeriesNumber < selectedSeriesNumber and self.logic.isTrackingPossible(series):
        results = self.registrationResults.getResultsBySeriesNumber(currentSeriesNumber)
        if len(results) == 0:
          self.skipSeries(series)
      elif currentSeriesNumber >= selectedSeriesNumber:
        break

  def skipSeries(self, seriesText):
    volume = self.logic.getOrCreateVolumeForSeries(seriesText)
    name, suffix = self.logic.getRegistrationResultNameAndGeneratedSuffix(volume.GetName())
    result = self.registrationResults.createResult(name+suffix)
    result.fixedVolume = volume
    result.skip()

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def onSegmentationFinished(self, caller, event, labelNode):
    self.currentIntraopLabel = labelNode
    fixedVolumeName = self.fixedVolumeSelector.currentNode().GetName()
    _, suffix = self.logic.getRegistrationResultNameAndGeneratedSuffix(fixedVolumeName)
    self.currentIntraopLabel.SetName(self.currentIntraopLabel.GetName() + suffix)
    if self.logic.usePreopData or self.logic.retryMode:
      self.setAxialOrientation()

    self.fixedLabelSelector.setCurrentNode(self.currentIntraopLabel)
    self.openSegmentationComparisonStep()

  def openSegmentationComparisonStep(self):
    self.currentStep = self.STEP_SEGMENTATION_COMPARISON
    self.hideAllLabels()
    self.hideAllTargets()
    self.removeMissingPreopDataAnnotation()
    if self.logic.usePreopData or self.logic.retryMode:
      self.layoutManager.setLayout(self.LAYOUT_SIDE_BY_SIDE)

      if self.logic.retryMode:
        coverProstateRegResult = self.registrationResults.getMostRecentApprovedCoverProstateRegistration()
        if coverProstateRegResult:
          self.movingVolumeSelector.setCurrentNode(coverProstateRegResult.fixedVolume)
          self.movingLabelSelector.setCurrentNode(coverProstateRegResult.fixedLabel)
          self.fiducialSelector.setCurrentNode(coverProstateRegResult.approvedTargets)

      self.setupScreenForSegmentationComparison("red", self.movingVolumeSelector.currentNode(),
                                                self.movingLabelSelector.currentNode())
      self.setupScreenForSegmentationComparison("yellow", self.fixedVolumeSelector.currentNode(),
                                                self.fixedLabelSelector.currentNode())
      self.setAxialOrientation()
      self.applyRegistrationButton.setEnabled(1 if self.inputsAreSet() else 0)
      self.editorWidgetButton.setEnabled(True)
      self.centerLabelsOnVisibleSliceWidgets()
    else:
      self.openTargetingStep()

  def setupScreenForSegmentationComparison(self, viewName, volume, label):
    compositeNode = getattr(self, viewName+"CompositeNode")
    compositeNode.SetReferenceBackgroundVolumeID(volume.GetID())
    compositeNode.SetLabelVolumeID(label.GetID())
    compositeNode.SetLabelOpacity(1)
    sliceNode = getattr(self, viewName+"SliceNode")
    sliceNode.RotateToVolumePlane(volume)
    sliceNode.SetUseLabelOutline(True)

  def onTrackTargetsButtonClicked(self):
    self.removeSliceAnnotations()
    self.targetTableModel.computeCursorDistances = False
    volume = self.logic.getOrCreateVolumeForSeries(self.intraopSeriesSelector.currentText)
    if volume:
      if not self.logic.zFrameRegistrationSuccessful and self.getSetting("COVER_TEMPLATE") in self.intraopSeriesSelector.currentText:
        self.openZFrameRegistrationStep(volume)
        return
      else:
        if self.currentResult is None or \
           self.registrationResults.getMostRecentApprovedCoverProstateRegistration() is None or \
           self.logic.retryMode or self.getSetting("COVER_PROSTATE") in self.intraopSeriesSelector.currentText:
          self.openSegmentationStep(volume)
        else:
          self.repeatRegistrationForCurrentSelection(volume)

  def openZFrameRegistrationStep(self, volume):
    self.resetZFrameRegistration()
    self.currentStep = self.STEP_ZFRAME_REGISTRATION
    self.setupFourUpView(volume)
    self.redSliceNode.SetSliceVisible(True)
    if self.zFrameRegistrationClass is OpenSourceZFrameRegistration:
      self.addROIObserver()
      self.activateCreateROIMode()
      self.addZFrameInstructions()

  def resetZFrameRegistration(self):
    self.applyZFrameRegistrationButton.enabled = False
    self.approveZFrameRegistrationButton.enabled = False
    self.retryZFrameRegistrationButton.enabled = False
    self.removeNodeFromMRMLScene(self.coverTemplateROI)
    self.removeNodeFromMRMLScene(self.zFrameCroppedVolume)
    self.removeNodeFromMRMLScene(self.zFrameLabelVolume)
    self.removeNodeFromMRMLScene(self.zFrameMaskedVolume)
    self.removeNodeFromMRMLScene(self.logic.zFrameTransform)

  def addROIObserver(self):

    @vtk.calldata_type(vtk.VTK_OBJECT)
    def onNodeAdded(caller, event, calldata):
      node = calldata
      if isinstance(node, slicer.vtkMRMLAnnotationROINode):
        self.removeROIObserver()
        self.coverTemplateROI = node
        self.applyZFrameRegistrationButton.enabled = self.isRegistrationPossible()

    if self.roiObserverTag:
      self.removeROIObserver()
    self.roiObserverTag = slicer.mrmlScene.AddObserver(slicer.vtkMRMLScene.NodeAddedEvent, onNodeAdded)

  def removeROIObserver(self):
    if self.roiObserverTag:
      self.roiObserverTag = slicer.mrmlScene.RemoveObserver(self.roiObserverTag)

  def addZFrameInstructions(self, step=1):
    self.zFrameStep = step
    text = self.ZFrame_INSTRUCTION_STEPS[self.zFrameStep]
    self.zFrameInstructionAnnotation = SliceAnnotation(self.redWidget, text, yPos=55, horizontalAlign="center",
                                                       opacity=0.6, color=(0,0.6,0))
    self.zFrameClickObserver = self.redSliceViewInteractor.AddObserver(vtk.vtkCommand.LeftButtonReleaseEvent,
                                                      self.onZFrameStepAccomplished)
    self.onShowAnnotationsToggled(self.showAnnotationsButton.checked)

  def onZFrameStepAccomplished(self, observee, event):
    self.removeZFrameInstructionAnnotation()
    nextStep = self.zFrameStep + 1
    if nextStep in self.ZFrame_INSTRUCTION_STEPS.keys():
      self.addZFrameInstructions(nextStep)

  def removeZFrameInstructionAnnotation(self):
    if hasattr(self, "zFrameInstructionAnnotation") and self.zFrameInstructionAnnotation:
      self.zFrameInstructionAnnotation.remove()
      self.zFrameInstructionAnnotation = None
    if self.zFrameClickObserver :
      self.redSliceViewInteractor.RemoveObserver(self.zFrameClickObserver)
      self.zFrameClickObserver = None

  def openSegmentationStep(self, volume):
    self.currentStep = self.STEP_SEGMENTATION
    self.logic.currentIntraopVolume = volume
    self.fixedVolumeSelector.setCurrentNode(self.logic.currentIntraopVolume)
    self.volumeClipToLabelWidget.imageVolumeSelector.setCurrentNode(self.logic.currentIntraopVolume)
    if self.getSetting("COVER_PROSTATE") in self.intraopSeriesSelector.currentText:
      self.showTemplatePathButton.checked = False
    self.layoutManager.setLayout(self.LAYOUT_FOUR_UP)
    self.setupFourUpView(self.logic.currentIntraopVolume)
    self.volumeClipToLabelWidget.quickSegmentationButton.click()
    self.volumeClipToLabelWidget.addEventObserver(self.volumeClipToLabelWidget.SegmentationFinishedEvent,
                                                  self.onSegmentationFinished)
    self.setDefaultOrientation()

  def isRegistrationPossible(self):
    return self.coverTemplateROI is not None

  def onApplyZFrameRegistrationButtonClicked(self):
    progress = slicer.util.createProgressDialog(maximum=2, value=1)
    progress.labelText = '\nZFrame registration'
    zFrameTemplateVolume = self.logic.getOrCreateVolumeForSeries(self.intraopSeriesSelector.currentText)

    try:
      if self.zFrameRegistrationClass is OpenSourceZFrameRegistration:
        self.annotationLogic.SetAnnotationLockedUnlocked(self.coverTemplateROI.GetID())
        self.zFrameCroppedVolume = self.logic.createCroppedVolume(zFrameTemplateVolume, self.coverTemplateROI)
        self.zFrameLabelVolume = self.logic.createLabelMapFromCroppedVolume(self.zFrameCroppedVolume)
        self.zFrameMaskedVolume = self.logic.createMaskedVolume(zFrameTemplateVolume, self.zFrameLabelVolume)
        self.zFrameMaskedVolume.SetName(zFrameTemplateVolume.GetName() + "-label")

        if not self.zFrameRegistrationManualIndexesGroupBox.checked:
          start, center, end = self.getROIMinCenterMaxSliceNumbers()
          otsuOutputVolume = self.logic.applyOtsuFilter(self.zFrameMaskedVolume)
          self.logic.dilateMask(otsuOutputVolume)
          start, end = self.getStartEndWithConnectedComponents(otsuOutputVolume, center)
          self.zFrameRegistrationStartIndex.value = start
          self.zFrameRegistrationEndIndex.value = end
        else:
          start = self.zFrameRegistrationStartIndex.value
          end = self.zFrameRegistrationEndIndex.value

        self.logic.runZFrameRegistration(self.zFrameMaskedVolume, self.zFrameRegistrationClass, startSlice=start, endSlice=end)
      else:
        self.logic.runZFrameRegistration(zFrameTemplateVolume, self.zFrameRegistrationClass, )
    except AttributeError as exc:
      progress.close()
      slicer.util.errorDisplay("An error occurred. For further information click 'Show Details...'",
                   windowTitle=self.__class__.__name__, detailedText=str(exc.message))
    else:
      self.setBackgroundToVolumeID(zFrameTemplateVolume.GetID())
      self.approveZFrameRegistrationButton.enabled = True
      self.retryZFrameRegistrationButton.enabled = True
      progress.setValue(2)
      progress.close()

  def getROIMinCenterMaxSliceNumbers(self):
    center = [0.0, 0.0, 0.0]
    self.coverTemplateROI.GetXYZ(center)
    bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    self.coverTemplateROI.GetRASBounds(bounds)
    pMin = [bounds[0], bounds[2], bounds[4]]
    pMax = [bounds[1], bounds[3], bounds[5]]
    return [self.getIJKForXYZ(pMin)[2], self.getIJKForXYZ(center)[2], self.getIJKForXYZ(pMax)[2]]

  def getIJKForXYZ(self, p):
    def roundInt(value):
      try:
        return int(round(value))
      except ValueError:
        return 0

    xyz = self.redSliceView.convertRASToXYZ(p)
    layerLogic = self.redSliceLogic.GetBackgroundLayer()
    xyToIJK = layerLogic.GetXYToIJKTransform()
    ijkFloat = xyToIJK.TransformDoublePoint(xyz)
    ijk = [roundInt(value) for value in ijkFloat]
    return ijk

  def getStartEndWithConnectedComponents(self, volume, center):
    address = sitkUtils.GetSlicerITKReadWriteAddress(volume.GetName())
    image = sitk.ReadImage(address)
    start = self.getStartSliceUsingConnectedComponents(center, image)
    end = self.getEndSliceUsingConnectedComponents(center, image)

    return start, end

  def getStartSliceUsingConnectedComponents(self, center, image):
    sliceIndex = start = center
    while sliceIndex > 0:
      if self.getIslandCount(image, sliceIndex) > 6:
        start = sliceIndex
        sliceIndex -= 1
        continue
      break
    return start

  def getEndSliceUsingConnectedComponents(self, center, image):
    imageSize = image.GetSize()
    sliceIndex = end = center
    while sliceIndex < imageSize[2]:
      if self.getIslandCount(image, sliceIndex) > 6:
        end = sliceIndex
        sliceIndex += 1
        continue
      break
    return end

  @staticmethod
  def getIslandCount(image, index):
    imageSize = image.GetSize()
    index = [0, 0, index]
    extractor = sitk.ExtractImageFilter()
    extractor.SetSize([imageSize[0], imageSize[1], 0])
    extractor.SetIndex(index)
    slice = extractor.Execute(image)
    cc = sitk.ConnectedComponentImageFilter()
    cc.Execute(slice)
    return cc.GetObjectCount()

  def activateCreateROIMode(self):
    mrmlScene = self.annotationLogic.GetMRMLScene()
    selectionNode = mrmlScene.GetNthNodeByClass(0, "vtkMRMLSelectionNode")
    selectionNode.SetReferenceActivePlaceNodeClassName("vtkMRMLAnnotationROINode")
    self.annotationLogic.StartPlaceMode(False)

  def onRetryZFrameRegistrationButtonClicked(self):
    self.removeZFrameInstructionAnnotation()
    self.annotationLogic.SetAnnotationVisibility(self.coverTemplateROI.GetID())
    volume = self.logic.getOrCreateVolumeForSeries(self.intraopSeriesSelector.currentText)
    self.openZFrameRegistrationStep(volume)

  def onApproveZFrameRegistrationButtonClicked(self):
    self.logic.zFrameRegistrationSuccessful = True
    self.redSliceNode.SetSliceVisible(False)
    self.showZFrameModelButton.checked = False
    self.showTemplateButton.checked = False
    self.showTemplatePathButton.checked = False
    if self.zFrameRegistrationClass is OpenSourceZFrameRegistration:
      self.annotationLogic.SetAnnotationVisibility(self.coverTemplateROI.GetID())
    self.openOverviewStep()

  def disableTargetTable(self):
    self.hideAllTargets()
    self.currentTargets = None
    self.targetTable.clearSelection()
    self.targetTable.enabled = False

  def repeatRegistrationForCurrentSelection(self, volume):
    logging.debug('Performing Re-Registration')
    self.skipAllUnregisteredPreviousSeries(self.intraopSeriesSelector.currentText)
    self.logic.currentIntraopVolume = volume
    self.onInvokeRegistration(initial=False)

  def onEditorGearIconClicked(self):
    if self.editorWidgetParent.visible:
      self.disableEditorWidgetAndResetEditorTool(enabledButton=True)
    else:
      self.editorWidgetParent.show()
      displayNode = self.currentIntraopLabel.GetDisplayNode()
      displayNode.SetAndObserveColorNodeID(self.logic.mpReviewColorNode.GetID())
      self.editorParameterNode.SetParameter('effect', 'DrawEffect')
      self.editUtil.setLabel(self.logic.segmentedLabelValue)
      self.editUtil.setLabelOutline(1)

  def onInvokeRegistration(self, initial=True):
    self.disableEditorWidgetAndResetEditorTool()
    self.applyRegistrationButton.setEnabled(False)
    self.progress = slicer.util.createProgressDialog(maximum=4, value=1)
    if initial:
      self.logic.applyInitialRegistration(fixedVolume=self.fixedVolumeSelector.currentNode(),
                                          movingVolume=self.movingVolumeSelector.currentNode(),
                                          fixedLabel=self.fixedLabelSelector.currentNode(),
                                          movingLabel=self.movingLabelSelector.currentNode(),
                                          targets=self.fiducialSelector.currentNode(),
                                          progressCallback=self.updateProgressBar)
    else:
      self.logic.applyRegistration(progressCallback=self.updateProgressBar)
    self.progress.close()
    self.progress = None
    self.openEvaluationStep()
    logging.debug('Re-Registration is done')

  @onReturnProcessEvents
  def updateProgressBar(self, **kwargs):
    if self.progress:
      for key, value in kwargs.iteritems():
        if hasattr(self.progress, key):
          setattr(self.progress, key, value)

  def addNewTargetsToScene(self):
    for targetNode in [targets for targets in self.currentResult.targets.values() if targets]:
      slicer.mrmlScene.AddNode(targetNode)

  def setupRegistrationResultView(self, layout=None):
    if layout:
      self.layoutManager.setLayout(layout)
    self.hideAllLabels()
    self.addSliceAnnotationsBasedOnLayoutAndSetOrientation()
    self.refreshViewNodeIDs(self.logic.preopTargets, [self.redSliceNode])
    self.setupViewNodesForCurrentTargets()

  def addSliceAnnotationsBasedOnLayoutAndSetOrientation(self):
    self.removeSliceAnnotations()
    if self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      self.addFourUpSliceAnnotations()
      self.setDefaultOrientation()
    else:
      self.addSideBySideSliceAnnotations()
      self.setAxialOrientation()

  def setupViewNodesForCurrentTargets(self):
    if self.layoutManager.layout == self.LAYOUT_FOUR_UP:
      sliceNodes = [self.redSliceNode, self.yellowSliceNode, self.greenSliceNode]
    else:
      sliceNodes = [self.yellowSliceNode]
    for targetNode in [targets for targets in self.currentResult.targets.values() if targets]:
      self.refreshViewNodeIDs(targetNode, sliceNodes)
      targetNode.SetLocked(True)
    if self.currentResult.approvedTargets:
      self.refreshViewNodeIDs(self.currentResult.approvedTargets, sliceNodes)
      self.currentResult.approvedTargets.SetLocked(True)

  def openEvaluationStep(self):
    self.currentStep = self.STEP_EVALUATION
    self.currentResult.save(self.generatedOutputDirectory)
    self.targetTable.connect('doubleClicked(QModelIndex)', self.onMoveTargetRequest)
    self.addNewTargetsToScene()

    self.updateRegistrationResultSelector()
    defaultLayout = self.getSetting("DEFAULT_EVALUATION_LAYOUT")
    self.setupRegistrationResultView(layout=getattr(SliceTrackerConstants, defaultLayout, self.LAYOUT_SIDE_BY_SIDE))

    self.currentResult.printSummary()
    self.connectKeyEventObservers()
    if not self.logic.isVolumeExtentValid(self.currentResult.bSplineVolume):
      slicer.util.infoDisplay(
        "One or more empty volume were created during registration process. You have three options:\n"
        "1. Reject the registration result \n"
        "2. Retry with creating a new segmentation \n"
        "3. Set targets to your preferred position (in Four-Up layout)",
        title="Action needed: Registration created empty volume(s)", windowTitle="SliceTracker")

  def updateRegistrationResultSelector(self):
    self.resultSelector.clear()
    results = self.registrationResults.getResultsBySeriesNumber(self.currentResult.seriesNumber)
    for result in reversed(results):
      self.resultSelector.addItem(result.name)
    self.resultSelector.visible = len(results) > 1

  def onIntraopDICOMReceiverStopped(self, caller, event, callData=None):
    self.customStatusProgressBar.reset()

  @onReturnProcessEvents
  @vtk.calldata_type(vtk.VTK_STRING)
  def onDICOMReceiverStatusChanged(self, caller, event, callData):
    if not self.customStatusProgressBar.visible:
      self.customStatusProgressBar.show()
    self.customStatusProgressBar.maximum = 0
    self.customStatusProgressBar.text = callData

  @vtk.calldata_type(vtk.VTK_STRING)
  def onNewFileIndexed(self, caller, event, callData):
    text, size, currentIndex = ast.literal_eval(callData)
    if not self.customStatusProgressBar.visible:
      self.customStatusProgressBar.show()
    self.customStatusProgressBar.maximum = size
    self.customStatusProgressBar.updateStatus(text, currentIndex)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onNewImageDataReceived(self, caller, event, callData):
    self.customStatusProgressBar.text = "New image data has been received."
    newFileList = ast.literal_eval(callData)
    seriesNumberPatientIDs = self.getAllNewSeriesNumbersIncludingPatientIDs(newFileList)
    if self.logic.usePreopData:
      newSeriesNumbers = self.verifyPatientIDEquality(seriesNumberPatientIDs)
    else:
      self.simulateIntraopPhaseButton.enabled = False
      newSeriesNumbers = seriesNumberPatientIDs.keys()
    self.updateIntraopSeriesSelectorTable()
    selectedSeries = self.intraopSeriesSelector.currentText
    if selectedSeries != "" and self.logic.isTrackingPossible(selectedSeries):
      selectedSeriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)
      if not self.logic.zFrameRegistrationSuccessful and self.getSetting("COVER_TEMPLATE") in selectedSeries and \
                      selectedSeriesNumber in newSeriesNumbers:
        self.onTrackTargetsButtonClicked()
        return

      if self.currentStep == self.STEP_OVERVIEW and selectedSeriesNumber in newSeriesNumbers and \
              self.logic.isInGeneralTrackable(self.intraopSeriesSelector.currentText):
        if self.notifyUserAboutNewData and not self.logic.caseCompleted:
          dialog = IncomingDataMessageBox()
          self.notifyUserAboutNewDataAnswer, checked = dialog.exec_()
          self.notifyUserAboutNewData = not checked
        if hasattr(self, "notifyUserAboutNewDataAnswer") and self.notifyUserAboutNewDataAnswer == qt.QMessageBox.AcceptRole:
          self.onTrackTargetsButtonClicked()


class SliceTrackerLogic(ModuleLogicMixin, ScriptedLoadableModuleLogic):

  ZFRAME_MODEL_PATH = 'Resources/zframe/zframe-model.vtk'
  ZFRAME_TEMPLATE_CONFIG_FILE_NAME = 'Resources/zframe/ProstateTemplate.csv'
  ZFRAME_MODEL_NAME = 'ZFrameModel'
  ZFRAME_TEMPLATE_NAME = 'NeedleGuideTemplate'
  ZFRAME_TEMPLATE_PATH_NAME = 'NeedleGuideNeedlePath'
  COMPUTED_NEEDLE_MODEL_NAME = 'ComputedNeedleModel'
  DEFAULT_JSON_FILE_NAME = "results.json"

  @property
  def intraopDataDir(self):
    return self._intraopDataDir

  @intraopDataDir.setter
  def intraopDataDir(self, path):
    self._intraopDataDir = path
    if not self.caseCompleted:
      self.startSmartDICOMReceiver(runStoreSCP=not self.trainingMode)
    else:
      self.invokeEvent(SlicerProstateEvents.DICOMReceiverStoppedEvent)
    self.importDICOMSeries(self.getFileList(self.intraopDataDir))
    if self.smartDicomReceiver:
      self.smartDicomReceiver.forceStatusChangeEvent()

  @property
  def caseCompleted(self):
    return self._caseCompleted

  @caseCompleted.setter
  def caseCompleted(self, value):
    self._caseCompleted = value
    if value is True:
      self.stopSmartDICOMReceiver()

  @property
  def currentResult(self):
      return self.registrationResults.activeResult

  @currentResult.setter
  def currentResult(self, series):
    self.registrationResults.activeResult = series

  @property
  def templateSuccessfulLoaded(self):
    return self.tempModelNode and self.pathModelNode

  @property
  def zFrameSuccessfulLoaded(self):
    return self.zFrameModelNode

  def __init__(self, parent=None):
    ScriptedLoadableModuleLogic.__init__(self, parent)
    self.modulePath = os.path.dirname(slicer.util.modulePath(self.moduleName))
    self.markupsLogic = slicer.modules.markups.logic()
    self.volumesLogic = slicer.modules.volumes.logic()
    self.cropVolumeLogic = slicer.modules.cropvolume.logic()
    self.registrationLogic = SliceTrackerRegistrationLogic()
    self.scalarVolumePlugin = slicer.modules.dicomPlugins['DICOMScalarVolumePlugin']()
    self.defaultTemplateFile = os.path.join(self.modulePath, self.ZFRAME_TEMPLATE_CONFIG_FILE_NAME)
    self.resetAndInitializeData()

  def resetAndInitializeData(self):

    self.trainingMode = False
    self.caseCompleted = False
    self.seriesList = []
    self.loadableList = {}
    self.alreadyLoadedSeries = {}

    self.currentIntraopVolume = None
    self.usePreopData = True
    self.preopVolume = None
    self.biasCorrectionDone = False
    self.preopLabel = None
    self.preopTargets = None
    self.registrationResults = RegistrationResults()

    self._intraopDataDir = None

    self.retryMode = False
    self.zFrameRegistrationSuccessful = False
    self.zFrameModelNode = None
    self.zFrameTransform = None

    self.showTemplatePath = False
    self.showNeedlePath = False

    self.needleModelNode = None
    self.tempModelNode = None
    self.pathModelNode = None
    self.templateConfig = []
    self.templateMaxDepth = []
    self.pathOrigins = []  ## Origins of needle paths (after transformation by parent transform node)
    self.pathVectors = []  ## Normal vectors of needle paths (after transformation by parent transform node)

    from mpReview import mpReviewLogic
    self.mpReviewColorNode, self.structureNames = mpReviewLogic.loadColorTable(self.getSetting("Color_File_Name"))
    self.segmentedColorName = self.getSetting("Segmentation_Color_Name")
    self.segmentedLabelValue = self.mpReviewColorNode.GetColorIndexByName(self.segmentedColorName)
    self.clearOldNodes()
    self.loadZFrameModel()
    self.loadTemplateConfigFile()
    self._caseCompleted = False
    self.stopSmartDICOMReceiver()

  def clearOldNodes(self):
    self.clearOldNodesByName(self.ZFRAME_TEMPLATE_NAME)
    self.clearOldNodesByName(self.ZFRAME_TEMPLATE_PATH_NAME)
    self.clearOldNodesByName(self.ZFRAME_MODEL_NAME)
    self.clearOldNodesByName(self.COMPUTED_NEEDLE_MODEL_NAME)

  def setupDisplayNode(self, displayNode=None, starBurst=False):
    if not displayNode:
      displayNode = slicer.vtkMRMLMarkupsDisplayNode()
      slicer.mrmlScene.AddNode(displayNode)
    displayNode.SetTextScale(0)
    displayNode.SetGlyphScale(2.5)
    if starBurst:
      displayNode.SetGlyphType(slicer.vtkMRMLAnnotationPointDisplayNode.StarBurst2D)
    return displayNode

  def applyDefaultTargetDisplayNode(self, targetNode, new=False):
    displayNode = None if new else targetNode.GetDisplayNode()
    modifiedDisplayNode = self.setupDisplayNode(displayNode, True)
    targetNode.SetAndObserveDisplayNodeID(modifiedDisplayNode.GetID())

  def createNewCase(self, newCaseDirectory):
    self.continueOldCase = False
    os.mkdir(newCaseDirectory)
    os.mkdir(os.path.join(newCaseDirectory, "DICOM"))
    os.mkdir(os.path.join(newCaseDirectory, "DICOM", "Preop"))
    os.mkdir(os.path.join(newCaseDirectory, "DICOM", "Intraop"))
    os.mkdir(os.path.join(newCaseDirectory, "mpReviewPreprocessed"))
    os.mkdir(os.path.join(newCaseDirectory, "SliceTrackerOutputs"))
    return newCaseDirectory

  def isInGeneralTrackable(self, series):
    return self.isAnyListItemInString(series, [self.getSetting("COVER_TEMPLATE"), self.getSetting("COVER_PROSTATE"),
                                               self.getSetting("NEEDLE_IMAGE")])

  def isAnyListItemInString(self, string, listItem):
    return any(item in string for item in listItem)

  def resultHasNotBeenProcessed(self, series):
    return not (self.registrationResults.registrationResultWasApproved(series) or
                self.registrationResults.registrationResultWasSkipped(series) or
                self.registrationResults.registrationResultWasRejected(series))

  def isTrackingPossible(self, series):
    if self.caseCompleted:
      return False
    if self.isInGeneralTrackable(series) and self.resultHasNotBeenProcessed(series):
      if self.getSetting("NEEDLE_IMAGE") in series:
        return self.registrationResults.getMostRecentApprovedCoverProstateRegistration() or not self.usePreopData
      elif self.getSetting("COVER_PROSTATE") in series:
        return self.zFrameRegistrationSuccessful
      elif self.getSetting("COVER_TEMPLATE") in series:
        return not self.zFrameRegistrationSuccessful
    return False

  def isEligibleForSkipping(self, series):
    return not self.isAnyListItemInString(series, [self.getSetting("COVER_PROSTATE"), self.getSetting("COVER_TEMPLATE")])

  def isCaseDirectoryValid(self, directory):
    return os.path.exists(os.path.join(directory, "DICOM", "Preop")) \
           and os.path.exists(os.path.join(directory, "DICOM", "Intraop"))

  def hasCaseBeenCompleted(self, directory):
    self.caseCompleted = False
    filename = os.path.join(directory, self.DEFAULT_JSON_FILE_NAME)
    if not os.path.exists(filename):
      return
    with open(filename) as data_file:
      data = json.load(data_file)
      self.caseCompleted = data["completed"]
    return self.caseCompleted

  def getSavedSessions(self, caseDirectory):
    outputDir = os.path.join(caseDirectory, "SliceTrackerOutputs")
    validDirectories = []
    if not os.path.exists(outputDir):
      return validDirectories
    for d in [os.path.join(outputDir, d) for d in os.listdir(outputDir) if os.path.isdir(os.path.join(outputDir, d))]:
      if self.getDirectorySize(d) > 0:
        validDirectories.append(d)
    return validDirectories

  def closeCase(self, directory):
    self.stopSmartDICOMReceiver()
    if os.path.exists(directory):
      self.caseCompleted = False
      if self.getDirectorySize(directory) == 0:
        shutil.rmtree(directory)

  def getFirstMpReviewPreprocessedStudy(self, directory):
    # TODO: if several studies are available provide a drop down or anything similar for choosing
    directoryNames = [x[0] for x in os.walk(directory)]
    assert len(directoryNames) > 1
    return directoryNames[1]

  def loadPreopImageAndSegmentation(self, seriesMap):
    self.preopImagePath = None
    self.preopSegmentationPath = None
    for series in seriesMap:
      seriesName = str(seriesMap[series]['LongName'])
      logging.debug('series Number ' + series + ' ' + seriesName)

      imagePath = os.path.join(seriesMap[series]['NRRDLocation'])
      segmentationPath = os.path.dirname(os.path.dirname(imagePath))
      segmentationPath = os.path.join(segmentationPath, 'Segmentations')

      if not os.path.exists(segmentationPath):
        continue
      else:
        if any(self.segmentedColorName in name for name in os.listdir(segmentationPath)):
          logging.debug(' FOUND THE SERIES OF INTEREST, ITS ' + seriesName)
          logging.debug(' LOCATION OF VOLUME : ' + str(seriesMap[series]['NRRDLocation']))
          logging.debug(' LOCATION OF IMAGE path : ' + str(imagePath))

          logging.debug(' LOCATION OF SEGMENTATION path : ' + segmentationPath)

          self.preopImagePath = seriesMap[series]['NRRDLocation']
          self.preopSegmentationPath = segmentationPath
          break

  def loadT2Label(self):
    if self.preopLabel:
      return True
    mostRecentFilename = self.getMostRecentWholeGlandSegmentation(self.preopSegmentationPath)
    success = False
    if mostRecentFilename:
      filename = os.path.join(self.preopSegmentationPath, mostRecentFilename)
      success, self.preopLabel = slicer.util.loadLabelVolume(filename, returnNode=True)
      if success:
        self.preopLabel.SetName('t2-label')
    return success

  def loadPreopVolume(self):
    if self.preopVolume:
      return True
    success, self.preopVolume = slicer.util.loadVolume(self.preopImagePath, returnNode=True)
    if success:
      self.preopVolume.SetName('VOLUME-PREOP')
    return success

  def loadPreopTargets(self):
    if self.preopTargets:
      return True
    mostRecentTargets = self.getMostRecentTargetsFile(self.preopTargetsPath)
    success = False
    if mostRecentTargets:
      filename = os.path.join(self.preopTargetsPath, mostRecentTargets)
      success, self.preopTargets = slicer.util.loadMarkupsFiducialList(filename, returnNode=True)
      if success:
        self.preopTargets.SetName('targets-PREOP')
        self.preopTargets.SetLocked(True)
    return success

  def loadFromJSON(self, directory):
    filename = os.path.join(directory, self.DEFAULT_JSON_FILE_NAME)
    if not os.path.exists(filename):
      return
    with open(filename) as data_file:
      data = json.load(data_file)
      self.usePreopData = data["usedPreopData"]
      if data["VOLUME-PREOP-N4"]:
        self.loadBiasCorrectedImage(os.path.join(directory, data["VOLUME-PREOP-N4"]))
      self.loadZFrameTransform(os.path.join(directory, data["zFrameTransform"]))
    self.registrationResults.loadFromJSON(directory, filename)
    coverProstate = self.registrationResults.getMostRecentApprovedCoverProstateRegistration()
    if coverProstate:
      if not self.preopVolume:
        self.preopVolume = coverProstate.movingVolume if self.usePreopData else coverProstate.fixedVolume
      self.preopTargets = coverProstate.originalTargets
      if self.usePreopData:
        self.preopLabel = coverProstate.movingLabel
    if self.preopTargets:
      self.preopTargets.SetLocked(True)
    return True

  def loadZFrameTransform(self, transformFile):
    self.zFrameRegistrationSuccessful = False
    if not os.path.exists(transformFile):
      return False
    success, self.zFrameTransform = slicer.util.loadTransform(transformFile, returnNode=True)
    self.zFrameRegistrationSuccessful = success
    self.applyZFrameTransform(self.zFrameTransform)
    return success

  def loadBiasCorrectedImage(self, n4File):
    self.biasCorrectionDone = False
    if not os.path.exists(n4File):
      return False
    self.biasCorrectionDone = True
    success, self.preopVolume = slicer.util.loadVolume(n4File, returnNode=True)
    return success

  def saveSession(self, outputDir):
    if not os.path.exists(outputDir):
      self.createDirectory(outputDir)

    successfullySavedFileNames = []
    failedSaveOfFileNames = []

    def saveIntraopSegmentation():
      intraopLabel = self.registrationResults.intraopLabel
      if intraopLabel:
        seriesNumber = intraopLabel.GetName().split(":")[0]
        success, name = self.saveNodeData(intraopLabel, outputDir, FileExtension.NRRD, name=seriesNumber+"-LABEL",
                                          overwrite=True)
        self.handleSaveNodeDataReturn(success, name, successfullySavedFileNames, failedSaveOfFileNames)

    def saveOriginalTargets():
      originalTargets = self.registrationResults.originalTargets
      if originalTargets:
        success, name = self.saveNodeData(originalTargets, outputDir, FileExtension.FCSV, name="PreopTargets",
                                          overwrite=True)
        self.handleSaveNodeDataReturn(success, name, successfullySavedFileNames, failedSaveOfFileNames)

    def saveBiasCorrectionResult():
      if not self.biasCorrectionDone:
        return None
      success, name = self.saveNodeData(self.preopVolume, outputDir, FileExtension.NRRD, overwrite=True)
      self.handleSaveNodeDataReturn(success, name, successfullySavedFileNames, failedSaveOfFileNames)
      return name+FileExtension.NRRD

    def saveZFrameTransformation():
      success, name = self.saveNodeData(self.zFrameTransform, outputDir, FileExtension.H5, overwrite=True)
      self.handleSaveNodeDataReturn(success, name, successfullySavedFileNames, failedSaveOfFileNames)
      return name+FileExtension.H5

    def createResultsDict():
      resultDict = OrderedDict()
      for result in self.registrationResults.getResultsAsList():
        resultDict.update(result.toDict())
      return resultDict

    def saveJSON(dictString):
      with open(os.path.join(outputDir, self.DEFAULT_JSON_FILE_NAME), 'w') as outfile:
        json.dump(dictString, outfile, indent=2)

    saveIntraopSegmentation()
    saveOriginalTargets()
    saveBiasCorrectionResult()

    savedSuccessfully, failedToSave = self.registrationResults.save(outputDir)
    successfullySavedFileNames += savedSuccessfully
    failedSaveOfFileNames += failedToSave

    saveJSON({"completed":self.caseCompleted, "usedPreopData":self.usePreopData, "results":createResultsDict(),
              "VOLUME-PREOP-N4":saveBiasCorrectionResult(), "zFrameTransform":saveZFrameTransformation()})

    if len(failedSaveOfFileNames):
      messageOutput = "The following data failed to saved:\n"
      for filename in failedSaveOfFileNames:
        messageOutput += filename + "\n"
      logging.debug(messageOutput)

    if len(successfullySavedFileNames):
      messageOutput = "The following data was successfully saved:\n"
      for filename in successfullySavedFileNames:
        messageOutput += filename + "\n"
      logging.debug(messageOutput)
    return len(failedSaveOfFileNames) == 0

  def getMostRecentWholeGlandSegmentation(self, path):
    return self.getMostRecentFile(path, FileExtension.NRRD, filter="WholeGland")

  def getMostRecentTargetsFile(self, path):
    return self.getMostRecentFile(path, FileExtension.FCSV)

  def applyBiasCorrection(self):
    outputVolume = slicer.vtkMRMLScalarVolumeNode()
    outputVolume.SetName('VOLUME-PREOP-N4')
    slicer.mrmlScene.AddNode(outputVolume)
    params = {'inputImageName': self.preopVolume.GetID(),
              'maskImageName': self.preopLabel.GetID(),
              'outputImageName': outputVolume.GetID(),
              'numberOfIterations': '500,400,300'}

    slicer.cli.run(slicer.modules.n4itkbiasfieldcorrection, None, params, wait_for_completion=True)
    self.preopVolume = outputVolume
    self.biasCorrectionDone = True

  def applyInitialRegistration(self, fixedVolume, movingVolume, fixedLabel, movingLabel, targets, progressCallback=None):

    if not self.retryMode:
      self.registrationResults = RegistrationResults()
    self.retryMode = False

    self.generateNameAndCreateRegistrationResult(fixedVolume)

    parameterNode = slicer.vtkMRMLScriptedModuleNode()
    parameterNode.SetAttribute('FixedImageNodeID', fixedVolume.GetID())
    parameterNode.SetAttribute('FixedLabelNodeID', fixedLabel.GetID())
    parameterNode.SetAttribute('MovingImageNodeID', movingVolume.GetID())
    parameterNode.SetAttribute('MovingLabelNodeID', movingLabel.GetID())
    parameterNode.SetAttribute('TargetsNodeID', targets.GetID())

    self.registrationLogic.run(parameterNode, progressCallback=progressCallback)

  def generateNameAndCreateRegistrationResult(self, fixedVolume):
    name, suffix = self.getRegistrationResultNameAndGeneratedSuffix(fixedVolume.GetName())
    result = self.registrationResults.createResult(name + suffix)
    result.suffix = suffix
    self.registrationLogic.registrationResult = result
    return result

  def applyRegistration(self, progressCallback=None):

    coverProstateRegResult = self.registrationResults.getMostRecentApprovedCoverProstateRegistration()
    lastRigidTfm = self.registrationResults.getLastApprovedRigidTransformation()
    lastApprovedTfm = self.registrationResults.getMostRecentApprovedTransform()
    initialTransform = lastApprovedTfm if lastApprovedTfm else lastRigidTfm

    self.generateNameAndCreateRegistrationResult(self.currentIntraopVolume)
    parameterNode = slicer.vtkMRMLScriptedModuleNode()
    parameterNode.SetAttribute('FixedImageNodeID', self.currentIntraopVolume.GetID())

    fixedLabel = self.volumesLogic.CreateAndAddLabelVolume(slicer.mrmlScene, self.currentIntraopVolume,
                                                           self.currentIntraopVolume.GetName() + '-label')

    self.runBRAINSResample(inputVolume=coverProstateRegResult.fixedLabel, referenceVolume=self.currentIntraopVolume,
                           outputVolume=fixedLabel, warpTransform=initialTransform)

    self.dilateMask(fixedLabel, dilateValue=self.segmentedLabelValue)

    parameterNode.SetAttribute('FixedLabelNodeID', fixedLabel.GetID())
    parameterNode.SetAttribute('MovingImageNodeID', coverProstateRegResult.fixedVolume.GetID())
    parameterNode.SetAttribute('MovingLabelNodeID', coverProstateRegResult.fixedLabel.GetID())
    parameterNode.SetAttribute('TargetsNodeID', coverProstateRegResult.approvedTargets.GetID())

    self.registrationLogic.run(parameterNode, progressCallback=progressCallback)

  def getRegistrationResultNameAndGeneratedSuffix(self, name):
    nOccurrences = sum([1 for result in self.registrationResults.getResultsAsList() if name in result.name])
    suffix = ""
    if nOccurrences:
      suffix = "_Retry_" + str(nOccurrences)
    return name, suffix

  def runBRAINSResample(self, inputVolume, referenceVolume, outputVolume, warpTransform):

    params = {'inputVolume': inputVolume, 'referenceVolume': referenceVolume, 'outputVolume': outputVolume,
              'warpTransform': warpTransform, 'interpolationMode': 'NearestNeighbor', 'pixelType':'short'}

    logging.debug('About to run BRAINSResample CLI with those params: %s' % params)
    slicer.cli.run(slicer.modules.brainsresample, None, params, wait_for_completion=True)
    logging.debug('resample labelmap through')
    slicer.mrmlScene.AddNode(outputVolume)

  def startSmartDICOMReceiver(self, runStoreSCP=True):
    self.stopSmartDICOMReceiver()
    self.smartDicomReceiver = SmartDICOMReceiver(self.intraopDataDir)
    self.smartDicomReceiver.addEventObserver(SlicerProstateEvents.IncomingDataReceiveFinishedEvent,
                                        self.onDICOMSeriesReceived)
    self.smartDicomReceiver.addEventObserver(SlicerProstateEvents.StatusChangedEvent,
                                        self.onDICOMReceiverStatusChanged)
    self.smartDicomReceiver.addEventObserver(SlicerProstateEvents.DICOMReceiverStoppedEvent,
                                        self.onSmartDICOMReceiverStopped)
    self.smartDicomReceiver.start(runStoreSCP)

  def onSmartDICOMReceiverStopped(self, caller, event, callData=None):
    self.invokeEvent(SlicerProstateEvents.DICOMReceiverStoppedEvent)

  def stopSmartDICOMReceiver(self):
    self.smartDicomReceiver = getattr(self, "smartDicomReceiver", None)
    if self.smartDicomReceiver:
      self.smartDicomReceiver.stop()
      self.smartDicomReceiver.removeEventObservers()

  @vtk.calldata_type(vtk.VTK_STRING)
  def onDICOMReceiverStatusChanged(self, caller, event, callData):
    self.invokeEvent(SlicerProstateEvents.StatusChangedEvent, callData)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onDICOMSeriesReceived(self, caller, event, callData):
    newFileList = ast.literal_eval(callData)
    self.importDICOMSeries(newFileList)
    if self.trainingMode is True:
      self.stopSmartDICOMReceiver()

  def importDICOMSeries(self, newFileList):
    indexer = ctk.ctkDICOMIndexer()

    eligibleSeriesFiles = []
    size = len(newFileList)
    for currentIndex, currentFile in enumerate(newFileList, start=1):
      self.invokeEvent(SlicerProstateEvents.NewFileIndexedEvent, ["Indexing file %s" % currentFile, size, currentIndex].__str__())
      slicer.app.processEvents()
      currentFile = os.path.join(self._intraopDataDir, currentFile)
      indexer.addFile(slicer.dicomDatabase, currentFile, None)
      series = self.makeSeriesNumberDescription(currentFile)
      if series:
        eligibleSeriesFiles.append(currentFile)
        if series not in self.seriesList:
          self.seriesList.append(series)
          self.createLoadableFileListForSeries(series)

    self.seriesList = sorted(self.seriesList, key=lambda s: RegistrationResult.getSeriesNumberFromString(s))

    if len(eligibleSeriesFiles):
      self.invokeEvent(SlicerProstateEvents.NewImageDataReceivedEvent, eligibleSeriesFiles.__str__())

  def createLoadableFileListForSeries(self, selectedSeries):
    selectedSeriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)
    self.loadableList[selectedSeries] = []
    for dcm in self.getFileList(self._intraopDataDir):
      currentFile = os.path.join(self._intraopDataDir, dcm)
      currentSeriesNumber = int(self.getDICOMValue(currentFile, DICOMTAGS.SERIES_NUMBER))
      if currentSeriesNumber and currentSeriesNumber == selectedSeriesNumber:
        self.loadableList[selectedSeries].append(currentFile)

  def getOrCreateVolumeForSeries(self, series):
    try:
      volume = self.alreadyLoadedSeries[series]
    except KeyError:
      files = self.loadableList[series]
      loadables = self.scalarVolumePlugin.examine([files])
      success, volume = slicer.util.loadVolume(files[0], returnNode=True)
      volume.SetName(loadables[0].name)
      self.alreadyLoadedSeries[series] = volume
    return volume

  def deleteSeriesFromSeriesList(self, seriesNumber):
    for series in self.seriesList:
      currentSeriesNumber = RegistrationResult.getSeriesNumberFromString(series)
      if currentSeriesNumber == seriesNumber:
        self.seriesList.remove(series)

  def extractDateFromDICOMFile(self, currentFile, tag=DICOMTAGS.STUDY_DATE):
    extractedDate = self.getDICOMValue(currentFile, tag)
    if extractedDate:
      formatted = datetime.date(int(extractedDate[0:4]), int(extractedDate[4:6]), int(extractedDate[6:8]))
      return formatted.strftime("%Y-%b-%d")
    else:
      return ""

  def makeSeriesNumberDescription(self, dicomFile):
    seriesDescription = self.getDICOMValue(dicomFile, DICOMTAGS.SERIES_DESCRIPTION)
    seriesNumber = self.getDICOMValue(dicomFile, DICOMTAGS.SERIES_NUMBER)
    if not (seriesNumber and seriesDescription):
      raise DICOMValueError("Missing Attribute(s):\nFile: {}\nseriesNumber: {}\nseriesDescription: {}".format(dicomFile,
                                                                                                              seriesNumber,
                                                                                                              seriesDescription))
    return "{}: {}".format(seriesNumber, seriesDescription)

  def loadZFrameModel(self):
    zFrameModelPath = os.path.join(self.modulePath, self.ZFRAME_MODEL_PATH)
    if not self.zFrameModelNode:
      _, self.zFrameModelNode = slicer.util.loadModel(zFrameModelPath, returnNode=True)
      self.zFrameModelNode.SetName(self.ZFRAME_MODEL_NAME)
      slicer.mrmlScene.AddNode(self.zFrameModelNode)
      modelDisplayNode = self.zFrameModelNode.GetDisplayNode()
      modelDisplayNode.SetColor(1, 1, 0)
    self.zFrameModelNode.SetDisplayVisibility(False)

  def clearOldNodesByName(self, name):
    collection = slicer.mrmlScene.GetNodesByName(name)
    for index in range(collection.GetNumberOfItems()):
      slicer.mrmlScene.RemoveNode(collection.GetItemAsObject(index))

  def createLabelMapFromCroppedVolume(self, volume):
    labelVolume = self.volumesLogic.CreateAndAddLabelVolume(volume, "labelmap")
    imagedata = labelVolume.GetImageData()
    imageThreshold = vtk.vtkImageThreshold()
    imageThreshold.SetInputData(imagedata)
    imageThreshold.ThresholdBetween(0, 2000)
    imageThreshold.SetInValue(1)
    imageThreshold.Update()
    labelVolume.SetAndObserveImageData(imageThreshold.GetOutput())
    return labelVolume

  def createCroppedVolume(self, inputVolume, roi):
    cropVolumeParameterNode = slicer.vtkMRMLCropVolumeParametersNode()
    cropVolumeParameterNode.SetROINodeID(roi.GetID())
    cropVolumeParameterNode.SetInputVolumeNodeID(inputVolume.GetID())
    cropVolumeParameterNode.SetVoxelBased(True)
    self.cropVolumeLogic.Apply(cropVolumeParameterNode)
    croppedVolume = slicer.mrmlScene.GetNodeByID(cropVolumeParameterNode.GetOutputVolumeNodeID())
    return croppedVolume

  def createMaskedVolume(self, inputVolume, labelVolume):
    maskedVolume = slicer.vtkMRMLScalarVolumeNode()
    maskedVolume.SetName("maskedTemplateVolume")
    slicer.mrmlScene.AddNode(maskedVolume)
    params = {'InputVolume': inputVolume, 'MaskVolume': labelVolume, 'OutputVolume': maskedVolume}
    slicer.cli.run(slicer.modules.maskscalarvolume, None, params, wait_for_completion=True)
    return maskedVolume

  def runZFrameRegistration(self, inputVolume, algorithm, **kwargs):
    registration = algorithm(inputVolume)
    if isinstance(registration, OpenSourceZFrameRegistration):
      registration.runRegistration(start=kwargs.pop("startSlice"), end=kwargs.pop("endSlice"))
    elif isinstance(registration, LineMarkerRegistration):
      registration.runRegistration()
    self.zFrameTransform = registration.getOutputTransformation()
    self.applyZFrameTransform(self.zFrameTransform)

  def loadTemplateConfigFile(self):
    self.templateIndex = []
    self.templateConfig = []

    reader = csv.reader(open(self.defaultTemplateFile, 'rb'))
    try:
      next(reader)
      for row in reader:
        self.templateIndex.append(row[0:2])
        self.templateConfig.append([float(row[2]), float(row[3]), float(row[4]),
                                    float(row[5]), float(row[6]), float(row[7]),
                                    float(row[8])])
    except csv.Error as e:
      print('file %s, line %d: %s' % (self.defaultTemplateFile, reader.line_num, e))
      return

    self.createTemplateAndNeedlePathModel()
    self.setTemplateVisibility(0)
    self.setTemplatePathVisibility(0)
    self.setNeedlePathVisibility(0)
    self.updateTemplateVectors()

  def createTemplateAndNeedlePathModel(self):

    self.templatePathVectors = []
    self.templatePathOrigins = []

    self.checkAndCreateTemplateModelNode()
    self.checkAndCreatePathModelNode()

    pathModelAppend = vtk.vtkAppendPolyData()
    templateModelAppend = vtk.vtkAppendPolyData()

    for row in self.templateConfig:
      p, n = self.extractPointsAndNormalVectors(row)

      tempTubeFilter = self.createTubeFilter(p[0], p[1], radius=1.0, numSides=18)
      templateModelAppend.AddInputData(tempTubeFilter.GetOutput())
      templateModelAppend.Update()

      pathTubeFilter = self.createTubeFilter(p[0], p[2], radius=0.8, numSides=18)
      pathModelAppend.AddInputData(pathTubeFilter.GetOutput())
      pathModelAppend.Update()

      self.templatePathOrigins.append([row[0], row[1], row[2], 1.0])
      self.templatePathVectors.append([n[0], n[1], n[2], 1.0])
      self.templateMaxDepth.append(row[6])

    self.tempModelNode.SetAndObservePolyData(templateModelAppend.GetOutput())
    modelDisplayNode = self.tempModelNode.GetDisplayNode()
    modelDisplayNode.SetColor(0.5,0,1)
    self.pathModelNode.SetAndObservePolyData(pathModelAppend.GetOutput())
    modelDisplayNode = self.pathModelNode.GetDisplayNode()
    modelDisplayNode.SetColor(0.8,0.5,1)

  def createNeedleModelNode(self, start, end):
    self.removeNeedleModelNode()
    if start is not None and end is not None:
      self.needleModelNode = self.createModelNode(self.COMPUTED_NEEDLE_MODEL_NAME)
      modelDisplayNode = self.setAndObserveDisplayNode(self.needleModelNode)
      modelDisplayNode.SetColor(0, 1, 0)
      pathTubeFilter = self.createTubeFilter(start, end, radius=1.0, numSides=18)
      self.needleModelNode.SetAndObservePolyData(pathTubeFilter.GetOutput())
      self.setNeedlePathVisibility(self.showNeedlePath)

  def removeNeedleModelNode(self):
    if self.needleModelNode:
      slicer.mrmlScene.RemoveNode(self.needleModelNode)
    self.clearOldNodesByName(self.COMPUTED_NEEDLE_MODEL_NAME)

  def extractPointsAndNormalVectors(self, row):
    p1 = numpy.array(row[0:3])
    p2 = numpy.array(row[3:6])
    v = p2-p1
    nl = numpy.linalg.norm(v)
    n = v/nl  # normal vector
    l = row[6]
    p3 = p1 + l * n
    return [p1, p2, p3], n

  def createTubeFilter(self, start, end, radius, numSides):
    lineSource = vtk.vtkLineSource()
    lineSource.SetPoint1(start)
    lineSource.SetPoint2(end)
    tubeFilter = vtk.vtkTubeFilter()

    tubeFilter.SetInputConnection(lineSource.GetOutputPort())
    tubeFilter.SetRadius(radius)
    tubeFilter.SetNumberOfSides(numSides)
    tubeFilter.CappingOn()
    tubeFilter.Update()
    return tubeFilter

  def checkAndCreatePathModelNode(self):
    if self.pathModelNode is None:
      self.pathModelNode = self.createModelNode(self.ZFRAME_TEMPLATE_PATH_NAME)
      self.setAndObserveDisplayNode(self.pathModelNode)

  def checkAndCreateTemplateModelNode(self):
    if self.tempModelNode is None:
      self.tempModelNode = self.createModelNode(self.ZFRAME_TEMPLATE_NAME)
      self.setAndObserveDisplayNode(self.tempModelNode)
      self.modelNodeTag = self.tempModelNode.AddObserver(slicer.vtkMRMLTransformableNode.TransformModifiedEvent,
                                                         self.updateTemplateVectors)

  def applyZFrameTransform(self, transform):
    for node in [node for node in [self.pathModelNode, self.tempModelNode, self.zFrameModelNode, self.needleModelNode] if node]:
      node.SetAndObserveTransformNodeID(transform.GetID())

  def setModelVisibility(self, node, visible):
    dnode = node.GetDisplayNode()
    if dnode is not None:
      dnode.SetVisibility(visible)

  def setModelSliceIntersectionVisibility(self, node, visible):
    dnode = node.GetDisplayNode()
    if dnode is not None:
      dnode.SetSliceIntersectionVisibility(visible)

  def setZFrameVisibility(self, visibility):
    self.setModelVisibility(self.zFrameModelNode, visibility)
    self.setModelSliceIntersectionVisibility(self.zFrameModelNode, visibility)

  def setTemplateVisibility(self, visibility):
    self.setModelVisibility(self.tempModelNode, visibility)

  def setTemplatePathVisibility(self, visibility):
    self.showTemplatePath = visibility
    self.setModelVisibility(self.pathModelNode, visibility)
    self.setModelSliceIntersectionVisibility(self.pathModelNode, visibility)

  def setNeedlePathVisibility(self, visibility):
    self.showNeedlePath = visibility
    if self.needleModelNode:
      self.setModelVisibility(self.needleModelNode, visibility)
      self.setModelSliceIntersectionVisibility(self.needleModelNode, visibility)

  def updateTemplateVectors(self, observee=None, event=None):
    if self.tempModelNode is None:
      return

    trans = vtk.vtkMatrix4x4()
    transformNode = self.tempModelNode.GetParentTransformNode()
    if transformNode is not None:
      transformNode.GetMatrixTransformToWorld(trans)
    else:
      trans.Identity()

    # Calculate offset
    zero = [0.0, 0.0, 0.0, 1.0]
    offset = trans.MultiplyDoublePoint(zero)

    self.pathOrigins = []
    self.pathVectors = []

    for i, orig in enumerate(self.templatePathOrigins):
      torig = trans.MultiplyDoublePoint(orig)
      self.pathOrigins.append(numpy.array(torig[0:3]))
      vec = self.templatePathVectors[i]
      tvec = trans.MultiplyDoublePoint(vec)
      self.pathVectors.append(numpy.array([tvec[0]-offset[0], tvec[1]-offset[1], tvec[2]-offset[2]]))
      i += 1

  def computeNearestPath(self, pos):
    minMag2 = numpy.Inf
    minDepth = 0.0
    minIndex = -1
    needleStart = None
    needleEnd = None

    p = numpy.array(pos)
    for i, orig in enumerate(self.pathOrigins):
      vec = self.pathVectors[i]
      op = p - orig
      aproj = numpy.inner(op, vec)
      perp = op-aproj*vec
      mag2 = numpy.vdot(perp, perp)
      if mag2 < minMag2:
        minMag2 = mag2
        minIndex = i
        minDepth = aproj
      i += 1

    indexX = '--'
    indexY = '--'
    inRange = False

    if minIndex != -1:
      indexX = self.templateIndex[minIndex][0]
      indexY = self.templateIndex[minIndex][1]
      if 0 < minDepth < self.templateMaxDepth[minIndex]:
        inRange = True
        needleStart, needleEnd = self.getNeedleStartEndPointFromPathOrigins(minIndex)
      else:
        self.removeNeedleModelNode()

    return needleStart, needleEnd, indexX, indexY, minDepth, inRange

  def getNeedleStartEndPointFromPathOrigins(self, index):
    start = self.pathOrigins[index]
    v = self.pathVectors[index]
    nl = numpy.linalg.norm(v)
    n = v / nl  # normal vector
    l = self.templateMaxDepth[index]
    end = start + l * n
    return start, end


class SliceTrackerTest(ScriptedLoadableModuleTest):
  """
  This is the test case for your scripted module.
  Uses ScriptedLoadableModuleTest base class, available at:
  https://github.com/Slicer/Slicer/blob/master/Base/Python/slicer/ScriptedLoadableModule.py
  """

  def setUp(self):
    """ Do whatever is needed to reset the state - typically a scene clear will be enough.
    """
    slicer.mrmlScene.Clear(0)

  def runTest(self):
    """Run as few or as many tests as needed here.
    """
    self.setUp()
    self.test_SliceTracker1()

  def test_SliceTracker1(self):
    """ Ideally you should have several levels of tests.  At the lowest level
    tests should exercise the functionality of the logic with different inputs
    (both valid and invalid).  At higher levels your tests should emulate the
    way the user would interact with your code and confirm that it still works
    the way you intended.
    One of the most important features of the tests is that it should alert other
    developers when their changes will have an impact on the behavior of your
    module.  For example, if a developer removes a feature that you depend on,
    your test should break so they know that the feature is needed.
    """
    logging.debug(' ___ performing selfTest ___ ')


class CustomTargetTableModel(qt.QAbstractTableModel, ParameterNodeObservationMixin):

  PLANNING_IMAGE_NAME = "Initial registration"

  COLUMN_NAME = 'Name'
  COLUMN_DISTANCE = 'Distance[cm]'
  COLUMN_HOLE = 'Hole'
  COLUMN_DEPTH = 'Depth[cm]'

  headers = [COLUMN_NAME, COLUMN_DISTANCE, COLUMN_HOLE, COLUMN_DEPTH]

  @property
  def targetList(self):
    return self._targetList

  @targetList.setter
  def targetList(self, targetList):
    self._targetList = targetList
    if self.currentGuidanceComputation and self.observer:
      self.self.currentGuidanceComputation.RemoveObserver(self.observer)
    self.currentGuidanceComputation = self.getOrCreateNewGuidanceComputation(targetList)
    if self.currentGuidanceComputation:
      self.observer = self.currentGuidanceComputation.addEventObserver(vtk.vtkCommand.ModifiedEvent, self.updateHoleAndDepth)
    self.reset()

  @property
  def coverProstateTargetList(self):
    return self._coverProstateTargetList

  @coverProstateTargetList.setter
  def coverProstateTargetList(self, targetList):
    self._coverProstateTargetList = targetList

  @property
  def cursorPosition(self):
    return self._cursorPosition

  @cursorPosition.setter
  def cursorPosition(self, cursorPosition):
    self._cursorPosition = cursorPosition
    self.dataChanged(self.index(0, 1), self.index(self.rowCount()-1, 2))

  def __init__(self, logic, targets=None, parent=None, *args):
    qt.QAbstractTableModel.__init__(self, parent, *args)
    self.logic = logic
    self._cursorPosition = None
    self._targetList = None
    self._guidanceComputations = []
    self.currentGuidanceComputation = None
    self.targetList = targets
    self.computeCursorDistances = False
    self.currentTargetIndex = -1
    self.observer = None

  def getOrCreateNewGuidanceComputation(self, targetList):
    if not targetList:
      return None
    guidance = None
    for crntGuidance in self._guidanceComputations:
      if crntGuidance.targetList is targetList:
        guidance = crntGuidance
        break
    if not guidance:
      self._guidanceComputations.append(ZFrameGuidanceComputation(targetList, self))
      guidance = self._guidanceComputations[-1]
    if self._targetList is targetList:
      self.updateHoleAndDepth()
    return guidance

  def updateHoleAndDepth(self, caller=None, event=None):
    self.dataChanged(self.index(0, 3), self.index(self.rowCount() - 1, 4))
    self.invokeEvent(vtk.vtkCommand.ModifiedEvent)

  def headerData(self, col, orientation, role):
    if orientation == qt.Qt.Horizontal and role in [qt.Qt.DisplayRole, qt.Qt.ToolTipRole]:
        return self.headers[col]
    return None

  def rowCount(self):
    try:
      number_of_targets = self.targetList.GetNumberOfFiducials()
      return number_of_targets
    except AttributeError:
      return 0

  def columnCount(self):
    return len(self.headers)

  def data(self, index, role):
    result = self.getBackgroundOrToolTipData(index, role)
    if result:
      return result

    row = index.row()
    col = index.column()

    if not index.isValid() or role not in [qt.Qt.DisplayRole, qt.Qt.ToolTipRole]:
      return None

    if col == 0:
      return self.targetList.GetNthFiducialLabel(row)

    if col == 1 and self.cursorPosition and self.computeCursorDistances and self.currentTargetIndex == row:
      targetPosition = self.logic.getTargetPosition(self.targetList, row)
      distance2D = self.logic.get3DDistance(targetPosition, self.cursorPosition)
      distance2D = [str(round(distance2D[0]/10, 1)), str(round(distance2D[1]/10, 1)), str(round(distance2D[2]/10, 1))]
      distance3D = self.logic.get3DEuclideanDistance(targetPosition, self.cursorPosition)
      text = 'x= ' + distance2D[0] + '  y= ' + distance2D[1] + '  z= ' + distance2D[2] + '  (3D= ' + str(round(distance3D/10, 1)) + ')'
      return text
    elif col == 2 and self.logic.zFrameRegistrationSuccessful:
      return self.currentGuidanceComputation.getZFrameHole(row)
    elif col == 3 and self.logic.zFrameRegistrationSuccessful:
      return self.currentGuidanceComputation.getZFrameDepth(row)
    return ""

  def getBackgroundOrToolTipData(self, index, role):
    if role not in [qt.Qt.BackgroundRole, qt.Qt.ToolTipRole]:
      return None
    backgroundRequested = role == qt.Qt.BackgroundRole
    row = index.row()
    col = index.column()
    outOfRangeText = "" if self.currentGuidanceComputation.getZFrameDepthInRange(row) else "Current depth: out of range"
    if self.coverProstateTargetList and not self.coverProstateTargetList is self.targetList:
      if col in [2, 3]:
        coverProstateGuidance = self.getOrCreateNewGuidanceComputation(self.coverProstateTargetList)
        if col == 2:
          coverProstateHole = coverProstateGuidance.getZFrameHole(row)
          if self.currentGuidanceComputation.getZFrameHole(row) == coverProstateHole:
            return qt.QColor(qt.Qt.green) if backgroundRequested else ""
          else:
            return qt.QColor(qt.Qt.red) if backgroundRequested else "{} hole: {}".format(self.PLANNING_IMAGE_NAME, coverProstateHole)
        elif col == 3:
          currentDepth = self.currentGuidanceComputation.getZFrameDepth(row, asString=False)
          coverProstateDepth = coverProstateGuidance.getZFrameDepth(row, asString=False)
          if abs(currentDepth - coverProstateDepth) <= max(1e-9 * max(abs(currentDepth), abs(coverProstateDepth)), 0.5):
            if backgroundRequested:
              return qt.QColor(qt.Qt.red) if len(outOfRangeText) else qt.QColor(qt.Qt.green)
            return "%s depth: '%.1f' %s" % (self.PLANNING_IMAGE_NAME, coverProstateDepth, "\n"+outOfRangeText)
          else:
            if backgroundRequested:
              return qt.QColor(qt.Qt.red)
            return "%s depth: '%.1f' %s" % (self.PLANNING_IMAGE_NAME, coverProstateDepth, "\n"+outOfRangeText)
    elif self.coverProstateTargetList is self.targetList and col == 3:
      if backgroundRequested and len(outOfRangeText):
        return qt.QColor(qt.Qt.red)
      elif len(outOfRangeText):
        return outOfRangeText
    return None


class ZFrameGuidanceComputation(ParameterNodeObservationMixin):

  SUPPORTED_EVENTS = [vtk.vtkCommand.ModifiedEvent]

  @property
  def logic(self):
    return self.parent.logic

  def __init__(self, targetList, parent):
    self.targetList = targetList
    self.parent = parent
    self.observer = self.targetList.AddObserver(self.targetList.PointModifiedEvent, self.calculate)
    self.reset()
    self.calculate()

  def __del__(self):
    if self.targetList and self.observer:
      self.targetList.RemoveObserver(self.observer)

  def reset(self):
    self.needleStartEndPositions = {}
    self.computedHoles = {}
    self.computedDepth = {}

  def calculate(self, caller=None, event=None):
    if not self.targetList or not self.logic.zFrameRegistrationSuccessful:
      return
    for index in range(self.targetList.GetNumberOfFiducials()):
      self.calculateZFrameHoleAndDepth(index)
    self.invokeEvent(vtk.vtkCommand.ModifiedEvent)

  def getZFrameHole(self, index):
    if index not in self.computedHoles.keys():
      self.calculateZFrameHoleAndDepth(index)
    return '(%s, %s)' % (self.computedHoles[index][0], self.computedHoles[index][1])

  def getZFrameDepth(self, index, asString=True):
    if index not in self.computedHoles.keys():
      self.calculateZFrameHoleAndDepth(index)
    if asString:
      return '%.1f' % self.computedDepth[index][1] if self.computedDepth[index][0] else '(%.1f)' % self.computedDepth[index][1]
    else:
      return self.computedDepth[index][1]

  def getZFrameDepthInRange(self, index):
    if index not in self.computedHoles.keys():
      self.calculateZFrameHoleAndDepth(index)
    return self.computedDepth[index][0]

  def calculateZFrameHoleAndDepth(self, index):
    targetPosition = self.logic.getTargetPosition(self.targetList, index)
    (start, end, indexX, indexY, depth, inRange) = self.logic.computeNearestPath(targetPosition)
    self.needleStartEndPositions[index] = (start, end)
    self.computedHoles[index] = [indexX, indexY]
    self.computedDepth[index] = [inRange, round(depth/10, 1)]


class NewCaseSelectionNameWidget(qt.QMessageBox, ModuleWidgetMixin):

  PREFIX = "Case"
  SUFFIX = "-" + datetime.date.today().strftime("%Y%m%d")
  SUFFIX_PATTERN = "-[0-9]{8}"
  CASE_NUMBER_DIGITS = 3
  PATTERN = PREFIX+"[0-9]{"+str(CASE_NUMBER_DIGITS-1)+"}[0-9]{1}"+SUFFIX_PATTERN

  def __init__(self, destination, parent=None):
    super(NewCaseSelectionNameWidget, self).__init__(parent)
    if not os.path.exists(destination):
      raise
    self.destinationRoot = destination
    self.newCaseDirectory = None
    self.minimum = self.getNextCaseNumber()
    self.setupUI()
    self.setupConnections()
    self.onCaseNumberChanged(self.minimum)

  def getNextCaseNumber(self):
    import re
    caseNumber = 0
    for dirName in [dirName for dirName in os.listdir(self.destinationRoot)
                     if os.path.isdir(os.path.join(self.destinationRoot, dirName)) and re.match(self.PATTERN, dirName)]:
      number = int(re.split(self.SUFFIX_PATTERN, dirName)[0].split(self.PREFIX)[1])
      caseNumber = caseNumber if caseNumber > number else number
    return caseNumber+1

  def setupUI(self):
    self.setWindowTitle("Case Number Selection")
    self.setText("Please select a case number for the new case.")
    self.setIcon(qt.QMessageBox.Question)
    self.spinbox = qt.QSpinBox()
    self.spinbox.setRange(self.minimum, int("9"*self.CASE_NUMBER_DIGITS))
    self.preview = qt.QLabel()
    self.notice = qt.QLabel()
    self.layout().addWidget(self.createVLayout([self.createHLayout([qt.QLabel("Proposed Case Number"), self.spinbox]),
                                                self.preview, self.notice]), 2, 1)
    self.okButton = self.addButton(self.Ok)
    self.okButton.enabled = False
    self.cancelButton = self.addButton(self.Cancel)
    self.setDefaultButton(self.okButton)

  def setupConnections(self):
    self.spinbox.valueChanged.connect(self.onCaseNumberChanged)

  def onCaseNumberChanged(self, caseNumber):
    formatString = '%0'+str(self.CASE_NUMBER_DIGITS)+'d'
    caseNumber = formatString % caseNumber
    directory = self.PREFIX+caseNumber+self.SUFFIX
    self.newCaseDirectory = os.path.join(self.destinationRoot, directory)
    self.preview.setText("New case directory: " + self.newCaseDirectory)
    self.okButton.enabled = not os.path.exists(self.newCaseDirectory)
    self.notice.text = "" if not os.path.exists(self.newCaseDirectory) else "Note: Directory already exists."


class TargetDisplacementChartWidget(object):

  @property
  def chartView(self):
    return self._chartView

  @property
  def chart(self):
    return self._chartView.chart()

  @property
  def xAxis(self):
    return self.chart.GetAxis(1)

  @property
  def yAxis(self):
    return self.chart.GetAxis(0)

  @property
  def showLegend(self):
    return self._showLegend

  @showLegend.setter
  def showLegend(self, value):
    assert type(value) is bool, "Only boolean values are allowed for this class member"
    self._showLegend = value
    self.chart.SetShowLegend(value)

  def __init__(self):
    self._chartView = ctk.ctkVTKChartView()
    self._chartView.minimumSize = qt.QSize(200, 200)
    self._showLegend = False
    self.xAxis.SetTitle('Timepoint')
    self.yAxis.SetTitle('Displacement')
    self.chartTable = vtk.vtkTable()

    self.arrX = vtk.vtkFloatArray()
    self.arrX.SetName('X Axis')

    self.arrXD = vtk.vtkFloatArray()
    self.arrXD.SetName('L/R Displacement')

    self.arrYD = vtk.vtkFloatArray()
    self.arrYD.SetName('P/A Displacement')

    self.arrZD = vtk.vtkFloatArray()
    self.arrZD.SetName('I/S Displacement')

    self.arrD = vtk.vtkFloatArray()
    self.arrD.SetName('3-D Distance')

    self.chartTable.AddColumn(self.arrX)
    self.chartTable.AddColumn(self.arrXD)
    self.chartTable.AddColumn(self.arrYD)
    self.chartTable.AddColumn(self.arrZD)
    self.chartTable.AddColumn(self.arrD)

    self.chartPopupWindow = None
    self.chartPopupSize = qt.QSize(600, 300)
    self.chartPopupPosition = qt.QPoint(0, 0)

    self.showLegendCheckBox = qt.QCheckBox('Show legend')
    self.showLegendCheckBox.setChecked(0)

    self.plotFrame = ctk.ctkCollapsibleButton()
    self.plotFrame.text = "Plotting"
    self.plotFrame.collapsed = 0
    plotFrameLayout = qt.QGridLayout(self.plotFrame)
    self.plottingFrameWidget = qt.QWidget()
    self.plottingFrameLayout = qt.QGridLayout()
    self.plottingFrameWidget.setLayout(self.plottingFrameLayout)
    self.plottingFrameLayout.addWidget(self.showLegendCheckBox, 1, 0)
    self.plottingFrameLayout.addWidget(self._chartView, 2, 0)

    self.popupChartButton = qt.QPushButton("Undock chart")
    self.popupChartButton.setCheckable(True)
    self.plottingFrameLayout.addWidget(self.popupChartButton, 3, 0)

    self.DisplacementChartTargetSelector = qt.QComboBox()
    self.lastSelectedTarget = None
    self.targetSelectorLayout = qt.QFormLayout()
    self.targetSelectorLayout.addRow("Target:", self.DisplacementChartTargetSelector)
    self.plottingFrameLayout.addLayout(self.targetSelectorLayout, 0, 0)

    plotFrameLayout.addWidget(self.plottingFrameWidget)

    self.popupChartButton.connect('toggled(bool)', self.onDockChartViewToggled)
    self.showLegendCheckBox.connect('stateChanged(int)', self.onShowLegendChanged)

    self.addPlotPoints([[0, 0, 0], [0, 0, 0]])

  def addPlotPoints(self, triplets):
    numCurrentRows = self.chartTable.GetNumberOfRows()
    self.chartView.removeAllPlots()
    for i in range(0, len(triplets)):
      if numCurrentRows == 0:
        self._chartView.hide()
        self.arrX.InsertNextValue(0)
      else:
        self._chartView.show()
        self.arrX.InsertNextValue(numCurrentRows + i - 1)
      self.arrXD.InsertNextValue(triplets[i][0])
      self.arrYD.InsertNextValue(triplets[i][1])
      self.arrZD.InsertNextValue(triplets[i][2])
      distance = (triplets[i][0] ** 2 + triplets[i][1] ** 2 + triplets[i][2] ** 2) ** 0.5
      self.arrD.InsertNextValue(distance)

    xvals = vtk.vtkDoubleArray()
    xlabels = vtk.vtkStringArray()
    maxX = self.arrX.GetNumberOfValues() - 2
    for j in range(0, maxX + 1):
      xvals.InsertNextValue(j)
      xlabels.InsertNextValue(str(j))
    self.xAxis.SetCustomTickPositions(xvals, xlabels)
    self.xAxis.SetBehavior(vtk.vtkAxis.FIXED)
    self.xAxis.SetRange(0, maxX + 0.1)

    plot = self.chart.AddPlot(vtk.vtkChart.LINE)
    plot.SetInputData(self.chartTable, 0, 1)
    plot.SetColor(255, 0, 0, 255)
    vtk.vtkPlotLine.SafeDownCast(plot).SetMarkerStyle(4)
    vtk.vtkPlotLine.SafeDownCast(plot).SetMarkerSize(3 * plot.GetPen().GetWidth())

    plot2 = self.chart.AddPlot(vtk.vtkChart.LINE)
    plot2.SetInputData(self.chartTable, 0, 2)
    plot2.SetColor(0, 255, 0, 255)
    vtk.vtkPlotLine.SafeDownCast(plot2).SetMarkerStyle(4)
    vtk.vtkPlotLine.SafeDownCast(plot2).SetMarkerSize(3 * plot2.GetPen().GetWidth())

    plot3 = self.chart.AddPlot(vtk.vtkChart.LINE)
    plot3.SetInputData(self.chartTable, 0, 3)
    plot3.SetColor(0, 0, 255, 255)
    vtk.vtkPlotLine.SafeDownCast(plot3).SetMarkerStyle(4)
    vtk.vtkPlotLine.SafeDownCast(plot3).SetMarkerSize(3 * plot3.GetPen().GetWidth())

    plot4 = self.chart.AddPlot(vtk.vtkChart.LINE)
    plot4.SetInputData(self.chartTable, 0, 4)
    plot4.SetColor(0, 0, 0, 255)
    vtk.vtkPlotLine.SafeDownCast(plot4).SetMarkerStyle(4)
    vtk.vtkPlotLine.SafeDownCast(plot4).SetMarkerSize(3 * plot4.GetPen().GetWidth())

  def resetAndInitializeChart(self):
    self.arrX.Initialize()
    self.arrXD.Initialize()
    self.arrYD.Initialize()
    self.arrZD.Initialize()
    self.arrD.Initialize()
    self.addPlotPoints([[0, 0, 0], [0, 0, 0]])
    if self.showLegendCheckBox.isChecked():
      self.showLegendCheckBox.setChecked(False)

  def onShowLegendChanged(self, checked):
    self.chart.SetShowLegend(True if checked == 2 else False)
    self.chartView.scene().SetDirty(True)

  def onDockChartViewToggled(self, checked):
    if checked:
      self.targetSelectorLayout.labelForField(self.DisplacementChartTargetSelector).hide()
      self.chartPopupWindow = qt.QDialog()
      self.chartPopupWindow.setWindowFlags(qt.Qt.WindowStaysOnTopHint)
      layout = qt.QGridLayout()
      self.chartPopupWindow.setLayout(layout)
      targetLayout = qt.QFormLayout()
      targetLayout.addRow("Target:", self.DisplacementChartTargetSelector)
      layout.addLayout(targetLayout, 1, 0)
      layout.addWidget(self._chartView, 2, 0)
      child = self.targetSelectorLayout.takeAt(0)
      self.chartPopupWindow.finished.connect(self.dockChartView)
      self.chartPopupWindow.resize(self.chartPopupSize)
      self.chartPopupWindow.move(self.chartPopupPosition)
      self.chartPopupWindow.show()
      self.popupChartButton.hide()
      if self.DisplacementChartTargetSelector.currentText:
        self._chartView.show()
    else:
      self.chartPopupWindow.close()

  def dockChartView(self):
    self.chartPopupSize = self.chartPopupWindow.size
    self.chartPopupPosition = self.chartPopupWindow.pos
    self.plottingFrameLayout.addWidget(self._chartView, 2, 0)
    self.plottingFrameLayout.addWidget(self.popupChartButton, 3, 0)
    self.targetSelectorLayout.insertRow(0, "Target:", self.DisplacementChartTargetSelector)
    self.targetSelectorLayout.labelForField(self.DisplacementChartTargetSelector).show()
    self.popupChartButton.blockSignals(True)
    self.popupChartButton.checked = False
    self.popupChartButton.show()
    self.popupChartButton.blockSignals(False)
