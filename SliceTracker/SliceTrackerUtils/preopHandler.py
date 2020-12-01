import os
import slicer
import qt
import vtk
import xml
import getpass
import datetime
import logging
import re

from SlicerDevelopmentToolboxUtils.mixins import ModuleWidgetMixin, ModuleLogicMixin
from SlicerDevelopmentToolboxUtils.widgets import CustomStatusProgressbar, SliceWidgetConfirmYesNoDialog
from SlicerDevelopmentToolboxUtils.constants import FileExtension
from SlicerDevelopmentToolboxUtils.decorators import onReturnProcessEvents
from SlicerDevelopmentToolboxUtils.exceptions import PreProcessedDataError, NoEligibleSeriesFoundError

from .algorithms.automaticProstateSegmentation import AutomaticSegmentationLogic
from .steps.plugins.segmentationValidator import SliceTrackerSegmentationValidatorPlugin
from .constants import SliceTrackerConstants
from .sessionData import SegmentationData, PreopData


class PreopDataHandler(ModuleWidgetMixin, ModuleLogicMixin):

  MODULE_NAME = SliceTrackerConstants.MODULE_NAME

  PreprocessingStartedEvent = vtk.vtkCommand.UserEvent + 434
  PreprocessingFinishedEvent = vtk.vtkCommand.UserEvent + 435
  PreprocessedDataErrorEvent = vtk.vtkCommand.UserEvent + 436

  @staticmethod
  def getFirstMpReviewPreprocessedStudy(directory):
    # TODO add check here and selected the one which has targets in it
    # TODO: if several studies are available provide a drop down or anything similar for choosing
    directoryNames = filter(os.path.isdir, [os.path.join(directory, f) for f in os.listdir(directory)])
    return None if not len(directoryNames) else directoryNames[0]

  @staticmethod
  def wasDirectoryPreprocessed(directory):
    # TODO: this check needs to be more specific with expected sub directories
    firstDir = PreopDataHandler.getFirstMpReviewPreprocessedStudy(directory)
    if not firstDir:
      return False
    from mpReview import mpReviewLogic
    return mpReviewLogic.wasmpReviewPreprocessed(directory)

  @property
  def outputDirectory(self):
    self._outputDirectory = getattr(self, "_outputDirectory", None)
    return self._outputDirectory

  @outputDirectory.setter
  def outputDirectory(self, directory):
    if directory and not os.path.exists(directory):
      self.createDirectory(directory)
    self._outputDirectory = directory

  @property
  def segmentationData(self):
    try:
      return self.data.preopData.segmentation
    except AttributeError:
      return None

  @segmentationData.setter
  def segmentationData(self, value):
    assert self.preopData
    self.preopData.segmentation = value

  @property
  def preopData(self):
    return self.data.preopData

  @preopData.setter
  def preopData(self, value):
    self.data.preopData = value

  def __init__(self, inputDirectory, outputDirectory, data):
    self._inputDirectory = inputDirectory
    self.outputDirectory = outputDirectory
    self.data = data

  def handle(self):
    if self._runPreProcessing():
      self.runModule()
    else:
      slicer.util.infoDisplay("No DICOM data could be processed. Please select another directory.",
                              windowTitle="SliceTracker")

  def _runPreProcessing(self):
    from mpReviewPreprocessor import mpReviewPreprocessorLogic
    mpReviewPreprocessorLogic = mpReviewPreprocessorLogic()
    progress = self.createProgressDialog()
    progress.canceled.connect(lambda: mpReviewPreprocessorLogic.cancelProcess())

    @onReturnProcessEvents
    def updateProgressBar(**kwargs):
      for key, value in kwargs.items():
        if hasattr(progress, key):
          setattr(progress, key, value)

    success = mpReviewPreprocessorLogic.importAndProcessData(self._inputDirectory, outputDir=self.outputDirectory,
                                                             copyDICOM=False,
                                                             progressCallback=updateProgressBar)
    progress.canceled.disconnect(lambda: mpReviewPreprocessorLogic.cancelProcess())
    progress.close()
    return success

  def runModule(self, invokeEvent=True):
    if invokeEvent:
      self.invokeEvent(self.PreprocessingStartedEvent)

    def onModuleReturn():
      slicer.modules.mpReviewWidget.saveButton.clicked.disconnect(onModuleReturn)
      self.layoutManager.selectModule(self.MODULE_NAME)
      slicer.mrmlScene.Clear(0)
      self.loadPreProcessedData()

    self.setSetting('InputLocation', None, moduleName="mpReview")
    self.layoutManager.selectModule("mpReview")
    mpReview = slicer.modules.mpReviewWidget
    self.setSetting('InputLocation', self.outputDirectory, moduleName="mpReview")
    mpReview.onReload()
    slicer.modules.mpReviewWidget.saveButton.clicked.connect(onModuleReturn)
    self.layoutManager.selectModule(mpReview.moduleName)

  def loadPreProcessedData(self):
    try:
      self.loadMpReviewProcessedData()
    except PreProcessedDataError as exc:
      self.onPreopLoadingFailed(str(exc))
    except NoEligibleSeriesFoundError as exc:
      slicer.util.errorDisplay(str(exc), windowTitle="SliceTracker")
      self.invokeEvent(self.PreprocessedDataErrorEvent)

  def loadMpReviewProcessedData(self):
    studyDir = self.getFirstMpReviewPreprocessedStudy(self.outputDirectory)
    resourcesDir = os.path.join(studyDir, 'RESOURCES')
    if not self.isMpReviewStudyDirectoryValid(resourcesDir):
      raise PreProcessedDataError

    self.preopImagePath, self.preopSegmentationPath = self.findPreopImageAndSegmentationPaths(resourcesDir)

    self.data.initialTargetsPath = os.path.join(studyDir, 'Targets')

    loadedPreopVolume = self.loadPreopVolume()
    loadedPreopTargets = self.loadPreopTargets()
    loadedPreopT2Label = self.loadT2Label() if os.path.exists(self.preopSegmentationPath) else False
    success = all(r is True for r in [loadedPreopT2Label, loadedPreopVolume, loadedPreopTargets])

    if success:
      if not self.data.usePreopData:
        self._createPreopData(algorithm="Manual")
        self.segmentationData.note = "mpReview preprocessed"
        self.segmentationData._label = self.data.initialLabel
      self.invokeEvent(self.PreprocessingFinishedEvent)
    else:
      useDeepLearning = str(self.getSetting("Use_Deep_Learning", moduleName=self.MODULE_NAME)).lower() == "true"
      if loadedPreopTargets and loadedPreopVolume and useDeepLearning:
        if slicer.util.confirmYesNoDisplay("No WholeGland segmentation found in preop data. Automatic segmentation is "
                                           "available. Would you like to proceed with the automatic segmentation?",
                                           windowTitle="SliceTracker"):
          self._createPreopData(algorithm="Automatic")
          self.runAutomaticSegmentation()
          return
      raise PreProcessedDataError

  def onPreopLoadingFailed(self, detailed=None, offerRevisit=True):
    detailed = detailed if detailed else "Make sure that the correct mpReview directory structure is used. " \
                                         "\n\nSliceTracker expects a T2 volume, WholeGland segmentation and target(s)."
    message = "Loading preop data failed. Do you want to " \
              "open/revisit pre-processing for the current case?"
    if slicer.util.confirmYesNoDisplay(message, windowTitle="SliceTracker", detailedText=detailed):
      self.runModule()
    else:
      self.invokeEvent(self.PreprocessedDataErrorEvent)

  def _createPreopData(self, algorithm, segmentationType="Prostate"):
    self.preopData = PreopData()
    self.segmentationData = SegmentationData(segmentationType=segmentationType, algorithm=algorithm)

  def runAutomaticSegmentation(self):
    logic = AutomaticSegmentationLogic()
    logic.addEventObserver(logic.DeepLearningFinishedEvent, self.onSegmentationFinished)
    logic.addEventObserver(logic.DeepLearningFailedEvent,
                           lambda c,e: self.onPreopLoadingFailed("Automatic segmentation failed."))
    customStatusProgressBar = CustomStatusProgressbar()
    customStatusProgressBar.text = "Running DeepInfer for automatic prostate segmentation"

    from mpReview import mpReviewLogic
    mpReviewColorNode, _ = mpReviewLogic.loadColorTable(self.getSetting("Color_File_Name", moduleName=self.MODULE_NAME))

    domain = 'BWH_WITHOUT_ERC'
    prompt = SliceWidgetConfirmYesNoDialog(self.data.initialVolume,
                                           "Was an endorectal coil used during preop acquisition?").exec_()

    self.preopData.usedERC = False
    if prompt == qt.QDialogButtonBox.Yes:
      self.preopData.usedERC = True
      domain = 'BWH_WITH_ERC'
    elif prompt == qt.QDialogButtonBox.Cancel:
      raise PreProcessedDataError

    self.segmentationData.startTime = self.getTime()
    logic.run(self.data.initialVolume, domain, mpReviewColorNode)

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def onSegmentationValidated(self, caller, event, labelNode):
    if "_modified" in labelNode.GetName():
      self.segmentationData.userModified["endTime"] = self.getTime()
      self.segmentationData._modifiedLabel = labelNode
    else:
      self.segmentationData.userModified = None
    if not self.preopSegmentationPath:
      self.createDirectory(self.preopSegmentationPath)
    segmentedColorName = self.getSetting("Segmentation_Color_Name", moduleName=self.MODULE_NAME)
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    filename = getpass.getuser() + '-' + segmentedColorName + '-' + timestamp
    self.saveNodeData(labelNode, self.preopSegmentationPath, extension=FileExtension.NRRD, name=filename)
    self.loadPreProcessedData()
    customStatusProgressBar = CustomStatusProgressbar()
    customStatusProgressBar.text = "Done automatic prostate segmentation"

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def onSegmentationFinished(self, caller, event, labelNode):
    self.segmentationData.endTime = self.getTime()
    self.segmentationData._label = labelNode
    segmentationValidator = SliceTrackerSegmentationValidatorPlugin(self.data.initialVolume, labelNode)
    segmentationValidator.addEventObserver(segmentationValidator.StartedEvent, lambda c, e: self.invokeEvent(e))
    segmentationValidator.addEventObserver(segmentationValidator.ModifiedEvent, self.onSegmentationModificationStarted)
    segmentationValidator.addEventObserver(segmentationValidator.FinishedEvent, self.onSegmentationValidated)
    segmentationValidator.addEventObserver(segmentationValidator.CanceledEvent,
                                           lambda c, e: self.onPreopLoadingFailed("Segmentation validation canceled."))
    segmentationValidator.run()

  def onSegmentationModificationStarted(self, caller, event):
    self.segmentationData.setModified(startTime=self.getTime())

  def isMpReviewStudyDirectoryValid(self, resourcesDir):
    if not os.path.exists(resourcesDir):
      logging.debug("The selected directory `{}` does not fit the mpReview directory structure. Make sure that you select "
                   "the study root directory which includes directories RESOURCES".format(resourcesDir))
      return False
    return True

  def findPreopImageAndSegmentationPaths(self, resourcesDir):
    from mpReview import mpReviewLogic
    seriesMap, _ = mpReviewLogic.loadMpReviewProcessedData(resourcesDir)

    regex = self.getSetting("PLANNING_IMAGE_PATTERN", moduleName=self.MODULE_NAME)

    for series in seriesMap:
      seriesName = str(seriesMap[series]['LongName'])
      logging.debug('series Number ' + series + ' ' + seriesName)

      xmlFile = os.path.join(seriesMap[series]['NRRDLocation']).replace(".nrrd", ".xml")
      segmentationsPath = os.path.join(os.path.dirname(os.path.dirname(xmlFile)), 'Segmentations')

      dom = xml.dom.minidom.parse(xmlFile)
      seriesDescription = self.findElement(dom, "SeriesDescription")
      if re.match(regex, seriesDescription) or seriesDescription == regex:
        return seriesMap[series]['NRRDLocation'], segmentationsPath

    raise NoEligibleSeriesFoundError("No eligible series found for preop AX T2 segmentation. MpReview might not have "
                                "processed the right series or series names are different from BWH internally used ones")

  def loadT2Label(self):
    if self.data.initialLabel and self.data.initialLabel.GetScene() is not None:
      return True
    mostRecentFilename = self.getMostRecentWholeGlandSegmentation(self.preopSegmentationPath)
    success = False
    if mostRecentFilename:
      filename = os.path.join(self.preopSegmentationPath, mostRecentFilename)
      success, self.data.initialLabel = slicer.util.loadLabelVolume(filename, returnNode=True)
      if success:
        self.data.initialLabel.SetName('t2-label')
    return success

  def loadPreopVolume(self):
    if self.data.initialVolume and self.data.initialVolume.GetScene() is not None:
      return True
    success, self.data.initialVolume = slicer.util.loadVolume(self.preopImagePath, returnNode=True)
    if success:
      self.data.initialVolume.SetName('VOLUME-PREOP')
    return success

  def loadPreopTargets(self):
    if self.data.initialTargets and self.data.initialTargets.GetScene() is not None:
      return True
    if not os.path.exists(self.data.initialTargetsPath):
      return False
    mostRecentTargets = self.getMostRecentTargetsFile(self.data.initialTargetsPath)
    success = False
    if mostRecentTargets:
      filename = os.path.join(self.data.initialTargetsPath, mostRecentTargets)
      success, self.data.initialTargets = slicer.util.loadMarkupsFiducialList(filename, returnNode=True)
      if success:
        self.data.initialTargets.SetName('initialTargets')
        self.data.initialTargets.SetLocked(True)
    return success

  def getMostRecentWholeGlandSegmentation(self, path):
    return self.getMostRecentFile(path, FileExtension.NRRD, filter="WholeGland")

  def getMostRecentTargetsFile(self, path):
    return self.getMostRecentFile(path, FileExtension.FCSV)
