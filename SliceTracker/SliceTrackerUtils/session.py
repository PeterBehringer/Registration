import os, logging
import vtk, ctk, ast
import qt

import slicer
from .sessionData import SessionData, RegistrationResult, RegistrationTypeData
from .constants import SliceTrackerConstants
from .helpers import SeriesTypeManager
from .preopHandler import PreopDataHandler

from SlicerDevelopmentToolboxUtils.constants import DICOMTAGS, STYLE
from SlicerDevelopmentToolboxUtils.events import SlicerDevelopmentToolboxEvents
from SlicerDevelopmentToolboxUtils.helpers import SmartDICOMReceiver
from SlicerDevelopmentToolboxUtils.mixins import ModuleWidgetMixin
from SlicerDevelopmentToolboxUtils.exceptions import DICOMValueError, UnknownSeriesError
from SlicerDevelopmentToolboxUtils.widgets import IncomingDataWindow, CustomStatusProgressbar
from SlicerDevelopmentToolboxUtils.widgets import RadioButtonChoiceMessageBox
from SlicerDevelopmentToolboxUtils.decorators import singleton, onExceptionReturnFalse
from SlicerDevelopmentToolboxUtils.decorators import onReturnProcessEvents, onExceptionReturnNone
from SlicerDevelopmentToolboxUtils.module.session import StepBasedSession

from SliceTrackerRegistration import SliceTrackerRegistrationLogic


@singleton
class SliceTrackerSession(StepBasedSession):

  IncomingDataSkippedEvent = SlicerDevelopmentToolboxEvents.SkippedEvent

  IncomingIntraopDataReceiveFinishedEvent = SlicerDevelopmentToolboxEvents.FinishedEvent + 111
  NewImageSeriesReceivedEvent = SlicerDevelopmentToolboxEvents.NewImageDataReceivedEvent

  ZFrameRegistrationSuccessfulEvent = vtk.vtkCommand.UserEvent + 140
  PreprocessingSuccessfulEvent = vtk.vtkCommand.UserEvent + 141
  LoadingMetadataSuccessfulEvent = vtk.vtkCommand.UserEvent + 143
  SegmentationCancelledEvent = vtk.vtkCommand.UserEvent + 144

  CurrentSeriesChangedEvent = vtk.vtkCommand.UserEvent + 151
  CurrentResultChangedEvent = vtk.vtkCommand.UserEvent + 152
  RegistrationStatusChangedEvent = vtk.vtkCommand.UserEvent + 153
  TargetSelectionEvent = vtk.vtkCommand.UserEvent + 154

  InitiateZFrameCalibrationEvent = vtk.vtkCommand.UserEvent + 160
  InitiateSegmentationEvent = vtk.vtkCommand.UserEvent + 161
  InitiateRegistrationEvent = vtk.vtkCommand.UserEvent + 162
  InitiateEvaluationEvent = vtk.vtkCommand.UserEvent + 163

  SeriesTypeManuallyAssignedEvent = SeriesTypeManager.SeriesTypeManuallyAssignedEvent

  MODULE_NAME = SliceTrackerConstants.MODULE_NAME

  @property
  def preprocessedDirectory(self):
    return os.path.join(self.directory, "mpReviewPreprocessed") if self.directory else None

  @property
  def preopDICOMDirectory(self):
    return os.path.join(self.directory, "DICOM", "Preop") if self.directory else None

  @property
  def intraopDICOMDirectory(self):
    return os.path.join(self.directory, "DICOM", "Intraop") if self.directory else None

  @property
  def outputDirectory(self):
    return os.path.join(self.directory, "SliceTrackerOutputs")

  @property
  def approvedCoverTemplate(self):
    try:
      return self.data.zFrameRegistrationResult.volume
    except AttributeError:
      return None

  @approvedCoverTemplate.setter
  def approvedCoverTemplate(self, volume):
    self.data.zFrameRegistrationResult.volume = volume
    self.zFrameRegistrationSuccessful = volume is not None

  @property
  def zFrameRegistrationSuccessful(self):
    self._zFrameRegistrationSuccessful = getattr(self, "_zFrameRegistrationSuccessful", None)
    return self.data.zFrameRegistrationResult is not None and self._zFrameRegistrationSuccessful

  @zFrameRegistrationSuccessful.setter
  def zFrameRegistrationSuccessful(self, value):
    self._zFrameRegistrationSuccessful = value
    if self._zFrameRegistrationSuccessful:
      self.save()
      self.invokeEvent(self.ZFrameRegistrationSuccessfulEvent)

  @property
  def currentResult(self):
    return self._getCurrentResult()

  @onExceptionReturnNone
  def _getCurrentResult(self):
    return self.data.registrationResults[self._currentResult]

  @currentResult.setter
  def currentResult(self, series):
    if self.currentResult and self.currentResult.name == series:
      return
    if self.currentResult is not None:
      self.currentResult.removeEventObservers()
    self._currentResult = series
    if self.currentResult:
      for event in RegistrationResult.StatusEvents.values():
        self.currentResult.addEventObserver(event, self.onRegistrationResultStatusChanged)
    self.invokeEvent(self.CurrentResultChangedEvent)

  @property
  def currentSeries(self):
    self._currentSeries = getattr(self, "_currentSeries", None)
    return self._currentSeries

  @currentSeries.setter
  def currentSeries(self, series):
    if series == self.currentSeries:
      return
    if series and series not in self.seriesList :
      raise UnknownSeriesError("Series %s is unknown" % series)
    self._currentSeries = series
    self.invokeEvent(self.CurrentSeriesChangedEvent, series)

  @property
  def currentSeriesVolume(self):
    if not self.currentSeries:
      return None
    else:
      return self.getOrCreateVolumeForSeries(self.currentSeries)

  @property
  def movingVolume(self):
    self._movingVolume = getattr(self, "_movingVolume", None)
    return self._movingVolume

  @movingVolume.setter
  def movingVolume(self, value):
    self._movingVolume = value

  @property
  def movingLabel(self):
    self._movingLabel = getattr(self, "_movingLabel", None)
    return self._movingLabel

  @movingLabel.setter
  def movingLabel(self, value):
    self._movingLabel = value

  @property
  def movingTargets(self):
    self._movingTargets = getattr(self, "_movingTargets", None)
    if self.isCurrentSeriesCoverProstateInNonPreopMode():
      return self.data.initialTargets
    return self._movingTargets

  @movingTargets.setter
  def movingTargets(self, value):
    if self.isCurrentSeriesCoverProstateInNonPreopMode():
      self.data.initialTargets = value
    self._movingTargets = value

  @property
  def fixedVolume(self):
    self._fixedVolume = getattr(self, "_fixedVolume", None)
    if self.isCurrentSeriesCoverProstateInNonPreopMode():
      return self.data.initialVolume
    return self._fixedVolume

  @fixedVolume.setter
  def fixedVolume(self, value):
    if self.isCurrentSeriesCoverProstateInNonPreopMode():
      self.data.initialVolume = value
    self._fixedVolume = value

  @property
  def fixedLabel(self):
    self._fixedLabel = getattr(self, "_fixedLabel", None)
    if self.isCurrentSeriesCoverProstateInNonPreopMode():
      return self.data.initialLabel
    return self._fixedLabel

  @fixedLabel.setter
  def fixedLabel(self, value):
    if self.isCurrentSeriesCoverProstateInNonPreopMode():
      self.data.initialLabel = value
    self._fixedLabel = value

  def setSelectedTarget(self, info):
    self.invokeEvent(self.TargetSelectionEvent, str(info))

  def __init__(self):
    StepBasedSession.__init__(self)
    self.registrationLogic = SliceTrackerRegistrationLogic()
    self.seriesTypeManager = SeriesTypeManager()
    self.seriesTypeManager.addEventObserver(self.seriesTypeManager.SeriesTypeManuallyAssignedEvent,
                                            lambda caller, event: self.invokeEvent(self.SeriesTypeManuallyAssignedEvent))
    self.resetAndInitializeMembers()

  def resetAndInitializeMembers(self):
    self._busy = False
    self.seriesTypeManager.clear()
    self.initializeColorNodes()
    self.directory = None
    self.data = SessionData()
    self.data.addEventObserver(self.data.NewResultCreatedEvent, self.onNewRegistrationResultCreated)
    self.trainingMode = False
    self.resetPreopDICOMReceiver()
    self.resetIntraopDICOMReceiver()
    self.loadableList = {}
    self.seriesList = []
    self.seriesTimeStamps = dict()
    self.alreadyLoadedSeries = {}
    self._currentResult = None
    self._currentSeries = None
    self.retryMode = False
    self.previousStep = None
    self.temporaryIntraopTargets = None

  def initializeColorNodes(self):
    from mpReview import mpReviewLogic
    self.mpReviewColorNode, self.structureNames = mpReviewLogic.loadColorTable(self.getSetting("Color_File_Name"))
    self.segmentedColorName = self.getSetting("Segmentation_Color_Name")
    self.segmentedLabelValue = self.mpReviewColorNode.GetColorIndexByName(self.segmentedColorName)

  def __del__(self):
    super(SliceTrackerSession, self).__del__()
    self.clearData()

  def clearData(self):
    self.resetAndInitializeMembers()

  def onMrmlSceneCleared(self, caller, event):
    self.initializeColorNodes()

  @onExceptionReturnFalse
  def isCurrentSeriesCoverProstateInNonPreopMode(self):
    return self.seriesTypeManager.isCoverProstate(self.currentSeries) and not self.data.usePreopData

  def isBusy(self):
    return self.isPreProcessing() or self._busy

  def isPreProcessing(self):
    return slicer.util.selectedModule() != self.MODULE_NAME

  def isCaseDirectoryValid(self):
    return all(os.path.exists(p) for p in [self.preopDICOMDirectory, self.intraopDICOMDirectory])

  def isRunning(self):
    return not self.directory in [None, '']

  def processDirectory(self):
    self.newCaseCreated = getattr(self, "newCaseCreated", False)
    if self.newCaseCreated:
      return
    if self.isCaseDirectoryValid():
      self.loadCaseData()
      self.invokeEvent(self.CaseOpenedEvent)
    else:
      slicer.util.warningDisplay("The selected case directory seems not to be valid", windowTitle="SliceTracker")
      self.close(save=False)

  def createNewCase(self, destination):
    self.newCaseCreated = True
    self.resetAndInitializeMembers()
    self.directory = destination
    self.createDirectory(self.preopDICOMDirectory)
    self.createDirectory(self.intraopDICOMDirectory)
    self.createDirectory(self.preprocessedDirectory)
    self.createDirectory(self.outputDirectory)
    self.startPreopDICOMReceiver()
    self.newCaseCreated = False
    self.invokeEvent(self.NewCaseStartedEvent)

  def close(self, save=False):
    if not self.isRunning():
      return
    message = None
    if save:
      success, failedFileNames = self.data.close(self.outputDirectory)
      message = "Case data has been saved successfully." if success else \
        "The following data failed to saved:\n %s" % failedFileNames
    self.resetAndInitializeMembers()
    self.invokeEvent(self.CloseCaseEvent, str(message))

  def save(self):
    success, failedFileNames = self.data.save(self.outputDirectory)
    return success and not len(failedFileNames), "The following data failed to saved:\n %s" % failedFileNames

  def complete(self):
    self.data.completed = True
    self.close(save=True)

  def load(self):
    filename = os.path.join(self.outputDirectory, SliceTrackerConstants.JSON_FILENAME)
    completed = self.data.wasSessionCompleted(filename)
    if slicer.util.confirmYesNoDisplay("A %s session has been found for the selected case. Do you want to %s?" \
                                        % ("completed" if completed else "started",
                                           "open it" if completed else "continue this session")):
      slicer.app.layoutManager().blockSignals(True)
      self._busy = True
      self.data.load(filename)
      self.postProcessLoadedSessionData()
      slicer.app.layoutManager().blockSignals(False)
      self.invokeEvent(self.LoadingMetadataSuccessfulEvent)
    else:
      self.clearData()

  def postProcessLoadedSessionData(self):
    coverProstate = self.data.getMostRecentApprovedCoverProstateRegistration()
    if coverProstate:
      if not self.data.initialVolume:
        self.data.initialVolume = coverProstate.volumes.moving if self.data.usePreopData else coverProstate.volumes.fixed
      self.data.initialTargets = coverProstate.targets.original
      if self.data.usePreopData:  # TODO: makes sense?
        self.data.preopLabel = coverProstate.labels.moving
    if self.data.zFrameRegistrationResult:
      self._zFrameRegistrationSuccessful = True
    self.data.resumed = not self.data.completed
    if self.data.usePreopData:
      preopDataManager = self.createPreopHandler()
      preopDataManager.loadPreProcessedData()
    else:
      if self.data.initialTargets:
        self.setupPreopLoadedTargets()
      self.startIntraopDICOMReceiver()

  def createPreopHandler(self):
    preopDataManager = PreopDataHandler(self.preopDICOMDirectory, self.preprocessedDirectory, self.data)
    preopDataManager.addEventObserver(preopDataManager.PreprocessedDataErrorEvent,
                                      lambda caller, event: self.close(save=False))
    preopDataManager.addEventObserver(preopDataManager.PreprocessingStartedEvent,
                                      self.onPreprocessingStarted)
    preopDataManager.addEventObserver(preopDataManager.PreprocessingFinishedEvent,
                                      self.onPreprocessingSuccessful)
    return preopDataManager

  def startPreopDICOMReceiver(self):
    self.resetPreopDICOMReceiver()
    self.preopDICOMReceiver = IncomingDataWindow(incomingDataDirectory=self.preopDICOMDirectory,
                                                 incomingPort=self.getSetting("Incoming_DICOM_Port"),
                                                 skipText="No preoperative images available")
    self.preopDICOMReceiver.addEventObserver(SlicerDevelopmentToolboxEvents.SkippedEvent,
                                             self.onSkippingPreopDataReception)
    self.preopDICOMReceiver.addEventObserver(SlicerDevelopmentToolboxEvents.CanceledEvent,
                                             lambda caller, event: self.close())
    self.preopDICOMReceiver.addEventObserver(SlicerDevelopmentToolboxEvents.FinishedEvent,
                                             self.onPreopDataReceptionFinished)
    self.preopDICOMReceiver.show()

  def onSkippingPreopDataReception(self, caller, event):
    self.data.usePreopData = False
    self.startIntraopDICOMReceiver()
    self.invokeEvent(self.IncomingDataSkippedEvent)

  def onPreopDataReceptionFinished(self, caller=None, event=None):
    self.data.usePreopData = True
    preopDataManager = self.createPreopHandler()
    preopDataManager.handle()

  def onPreprocessingStarted(self, caller, event):
    self._busy = True
    self.startIntraopDICOMReceiver()

  def resetPreopDICOMReceiver(self):
    self.preopDICOMReceiver = getattr(self, "preopDICOMReceiver", None)
    if self.preopDICOMReceiver:
      self.preopDICOMReceiver.hide()
      self.preopDICOMReceiver.removeEventObservers()
      self.preopDICOMReceiver = None

  def startIntraopDICOMReceiver(self):
    self.resetPreopDICOMReceiver()
    logging.debug("Starting DICOM Receiver for intra-procedural data")
    if not self.data.completed:
      self.resetIntraopDICOMReceiver()
      self.intraopDICOMReceiver = SmartDICOMReceiver(self.intraopDICOMDirectory,
                                                     self.getSetting("Incoming_DICOM_Port"))
      self._observeIntraopDICOMReceiverEvents()
      self.intraopDICOMReceiver.start(not (self.trainingMode or self.data.completed))
    else:
      self.invokeEvent(SlicerDevelopmentToolboxEvents.StoppedEvent)
    self.importDICOMSeries(self.getFileList(self.intraopDICOMDirectory))
    if self.intraopDICOMReceiver:
      self.intraopDICOMReceiver.forceStatusChangeEventUpdate()

  def resetIntraopDICOMReceiver(self):
    self.intraopDICOMReceiver = getattr(self, "intraopDICOMReceiver", None)
    if self.intraopDICOMReceiver:
      self.intraopDICOMReceiver.stop()
      self.intraopDICOMReceiver.removeEventObservers()

  def _observeIntraopDICOMReceiverEvents(self):
    self.intraopDICOMReceiver.addEventObserver(self.intraopDICOMReceiver.IncomingDataReceiveFinishedEvent,
                                               self.onDICOMSeriesReceived)
    self.intraopDICOMReceiver.addEventObserver(SlicerDevelopmentToolboxEvents.StatusChangedEvent,
                                               self.onDICOMReceiverStatusChanged)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onDICOMSeriesReceived(self, caller, event, callData):
    self.importDICOMSeries(ast.literal_eval(callData))
    if self.trainingMode is True:
      self.resetIntraopDICOMReceiver()

  @vtk.calldata_type(vtk.VTK_STRING)
  def onDICOMReceiverStatusChanged(self, caller, event, callData):
    customStatusProgressBar = CustomStatusProgressbar()
    customStatusProgressBar.text = callData
    customStatusProgressBar.busy = "Waiting" in callData

  def importDICOMSeries(self, newFileList):
    indexer = ctk.ctkDICOMIndexer()

    newSeries = []
    for currentIndex, currentFile in enumerate(newFileList, start=1):
      self.invokeEvent(SlicerDevelopmentToolboxEvents.NewFileIndexedEvent,
                       ["Indexing file %s" % currentFile, len(newFileList), currentIndex].__str__())
      slicer.app.processEvents()
      currentFile = os.path.join(self.intraopDICOMDirectory, currentFile)
      if not slicer.dicomDatabase:
          logging.error("slicer.dicomDatabase is not initialized!")
      indexer.addFile(slicer.dicomDatabase, currentFile, None)
      series = self.makeSeriesNumberDescription(currentFile)
      if series not in self.seriesList:
        self.seriesTimeStamps[series] = self.getTime()
        self.seriesList.append(series)
        newSeries.append(series)
        self.loadableList[series] = self.createLoadableFileListForSeries(series)
    self.seriesList = sorted(self.seriesList, key=lambda s: RegistrationResult.getSeriesNumberFromString(s))

    if len(newFileList):
      self.verifyPatientIDEquality(newFileList)
      self.invokeEvent(self.NewImageSeriesReceivedEvent, newSeries.__str__())

  def verifyPatientIDEquality(self, receivedFiles):
    seriesNumberPatientID = self.getAdditionalInformationForReceivedSeries(receivedFiles)
    dicomFileName = self.getPatientIDValidationSource()
    if not dicomFileName:
      return
    currentInfo = self.getPatientInformation(dicomFileName)
    currentID = currentInfo["PatientID"]
    patientName = currentInfo["PatientName"]
    for seriesNumber, receivedInfo in seriesNumberPatientID.items():
      patientID = receivedInfo["PatientID"]
      if patientID is not None and patientID != currentID:
        m = 'WARNING:\n' \
            'Current case:\n' \
            '  Patient ID: {0}\n' \
            '  Patient Name: {1}\n' \
            'Received image\n' \
            '  Patient ID: {2}\n' \
            '  Patient Name : {3}\n\n' \
            'Do you want to keep this series? '.format(currentID, patientName, patientID, receivedInfo["PatientName"])
        if not slicer.util.confirmYesNoDisplay(m, title="Patient IDs Not Matching", windowTitle="SliceTracker"):
          self.deleteSeriesFromSeriesList(seriesNumber)

  def getPatientIDValidationSource(self):
    if len(self.loadableList.keys()) > 1:
      keys = sorted(self.loadableList.keys())
      return self.loadableList[keys[0]][0]
    else:
      return None

  def getOrCreateVolumeForSeries(self, series):
    try:
      volume = self.alreadyLoadedSeries[series]
    except KeyError:
      files = self.loadableList[series]
      loadables = self.scalarVolumePlugin.examine([files])
      assert len(loadables)
      volume = self.scalarVolumePlugin.load(loadables[0])
      volume.SetName(loadables[0].name)
      self.alreadyLoadedSeries[series] = volume
    slicer.app.processEvents()
    return volume

  def createLoadableFileListForSeries(self, series):
    seriesNumber = RegistrationResult.getSeriesNumberFromString(series)
    loadableList = []
    for dcm in self.getFileList(self.intraopDICOMDirectory):
      currentFile = os.path.join(self.intraopDICOMDirectory, dcm)
      currentSeriesNumber = int(self.getDICOMValue(currentFile, DICOMTAGS.SERIES_NUMBER))
      if currentSeriesNumber and currentSeriesNumber == seriesNumber:
        loadableList.append(currentFile)
    return loadableList

  def deleteSeriesFromSeriesList(self, seriesNumber):
    for series in self.seriesList:
      currentSeriesNumber = RegistrationResult.getSeriesNumberFromString(series)
      if currentSeriesNumber == seriesNumber:
        self.seriesList.remove(series)
        for seriesFile in self.loadableList[series]:
          logging.debug("removing {} from filesystem".format(seriesFile))
          os.remove(seriesFile)
        del self.loadableList[series]

  def makeSeriesNumberDescription(self, dcmFile):
    seriesDescription = self.getDICOMValue(dcmFile, DICOMTAGS.SERIES_DESCRIPTION)
    seriesNumber = self.getDICOMValue(dcmFile, DICOMTAGS.SERIES_NUMBER)
    if not (seriesNumber and seriesDescription):
      raise DICOMValueError("Missing Attribute(s):\nFile: {}\nseriesNumber: {}\nseriesDescription: {}"
                            .format(dcmFile, seriesNumber, seriesDescription))
    return "{}: {}".format(seriesNumber, seriesDescription)

  def getAdditionalInformationForReceivedSeries(self, fileList):
    seriesNumberPatientID = {}
    for currentFile in [os.path.join(self.intraopDICOMDirectory, f) for f in fileList]:
      seriesNumber = int(self.getDICOMValue(currentFile, DICOMTAGS.SERIES_NUMBER))
      if seriesNumber not in seriesNumberPatientID.keys():
        seriesNumberPatientID[seriesNumber]= self.getPatientInformation(currentFile)
    return seriesNumberPatientID

  def getPatientInformation(self, currentFile):
    return {
      "PatientID": self.getDICOMValue(currentFile, DICOMTAGS.PATIENT_ID),
      "PatientName": self.getDICOMValue(currentFile, DICOMTAGS.PATIENT_NAME),
      "SeriesDescription": self.getDICOMValue(currentFile, DICOMTAGS.SERIES_DESCRIPTION)}

  def getSeriesForSubstring(self, substring):
    for series in reversed(self.seriesList):
      if substring in series:
        return series
    return None

  def loadCaseData(self):
    self._busy = True
    if self.hasJSONResults():
      self.load()
    else:
      if PreopDataHandler.wasDirectoryPreprocessed(self.preprocessedDirectory):
        self.startIntraopDICOMReceiver()
        preopDataManager = self.createPreopHandler()
        preopDataManager.loadPreProcessedData()
      else:
        self.continueWithUnprocessedData()

  def continueWithUnprocessedData(self):
    if os.listdir(self.preopDICOMDirectory):
      self.onPreopDataReceptionFinished()
    elif os.listdir(self.intraopDICOMDirectory):
      self.data.usePreopData = False
      self.startIntraopDICOMReceiver()
    else:
      self.startPreopDICOMReceiver()

  def hasJSONResults(self):
    return os.path.exists(os.path.join(self.outputDirectory, SliceTrackerConstants.JSON_FILENAME))

  def onPreprocessingSuccessful(self, caller, event):
    self.setupPreopLoadedTargets()
    self._busy = False
    self.invokeEvent(self.PreprocessingSuccessfulEvent)
    self.startIntraopDICOMReceiver()

  def setupPreopLoadedTargets(self):
    targets = self.data.initialTargets
    ModuleWidgetMixin.setFiducialNodeVisibility(targets, show=True)
    self.applyDefaultTargetDisplayNode(targets)
    self.markupsLogic.JumpSlicesToNthPointInMarkup(targets.GetID(), 0)

  def applyDefaultTargetDisplayNode(self, targetNode, new=False):
    displayNode = None if new else targetNode.GetDisplayNode()
    modifiedDisplayNode = self.setupDisplayNode(displayNode, True)
    targetNode.SetAndObserveDisplayNodeID(modifiedDisplayNode.GetID())

  def setupDisplayNode(self, displayNode=None, starBurst=False):
    if not displayNode:
      displayNode = slicer.vtkMRMLMarkupsDisplayNode()
      slicer.mrmlScene.AddNode(displayNode)
    displayNode.SetTextScale(0)
    displayNode.SetGlyphScale(2.5)
    if starBurst:
      displayNode.SetGlyphType(slicer.vtkMRMLAnnotationPointDisplayNode.StarBurst2D)
    return displayNode

  def getColorForSelectedSeries(self, series=None):
    series = series if series else self.currentSeries
    if series in [None, '']:
      return STYLE.WHITE_BACKGROUND
    style = STYLE.YELLOW_BACKGROUND
    if not self.isTrackingPossible(series):
      if self.data.registrationResultWasApproved(series) or \
              (self.zFrameRegistrationSuccessful and self.seriesTypeManager.isCoverTemplate(series)):
        style = STYLE.GREEN_BACKGROUND
      elif self.data.registrationResultWasSkipped(series):
        style = STYLE.RED_BACKGROUND
      elif self.data.registrationResultWasRejected(series):
        style = STYLE.GRAY_BACKGROUND
    return style

  def isTrackingPossible(self, series):
    if self.data.completed:
      logging.debug("No tracking possible. Case has been marked as completed!")
      return False
    if self.isInGeneralTrackable(series) and self.resultHasNotBeenProcessed(series):
      if self.seriesTypeManager.isGuidance(series):
        return self.data.getMostRecentApprovedCoverProstateRegistration()
      elif self.seriesTypeManager.isCoverProstate(series):
        return self.zFrameRegistrationSuccessful
      elif self.isCoverTemplateTrackable(series):
        return True
    return False

  def isCoverTemplateTrackable(self, series):
    if not self.seriesTypeManager.isCoverTemplate(series):
      return False
    if not self.approvedCoverTemplate:
      return True
    currentSeriesNumber = RegistrationResult.getSeriesNumberFromString(series)
    vName = self.approvedCoverTemplate.GetName()
    approvedSeriesNumber = int(vName.split(":" if vName.find(":") > -1 else "-")[0])
    return currentSeriesNumber > approvedSeriesNumber

  def isInGeneralTrackable(self, series):
    seriesType = self.seriesTypeManager.getSeriesType(series)
    return self.isAnyListItemInString(seriesType, [self.getSetting("COVER_TEMPLATE_PATTERN"),
                                                   self.getSetting("COVER_PROSTATE_PATTERN"),
                                                   self.getSetting("NEEDLE_IMAGE_PATTERN")])

  def resultHasNotBeenProcessed(self, series):
    return not (self.data.registrationResultWasApproved(series) or
                self.data.registrationResultWasSkipped(series) or
                self.data.registrationResultWasRejected(series))

  def isEligibleForSkipping(self, series):
    seriesType = self.seriesTypeManager.getSeriesType(series)
    return not self.isAnyListItemInString(seriesType,[self.getSetting("COVER_PROSTATE_PATTERN"),
                                                      self.getSetting("COVER_TEMPLATE_PATTERN")])

  def takeActionForCurrentSeries(self):
    event = None
    callData = None
    if self.seriesTypeManager.isCoverProstate(self.currentSeries):
      event = self.InitiateSegmentationEvent
      callData = str(False)
    elif self.seriesTypeManager.isCoverTemplate(self.currentSeries):
      event = self.InitiateZFrameCalibrationEvent
    elif self.seriesTypeManager.isGuidance(self.currentSeries):
      self.onInvokeRegistration(initial=False)
      return
    if event:
      self.invokeEvent(event, callData)
    else:
      raise UnknownSeriesError("Action for currently selected series unknown")

  def retryRegistration(self):
    self.invokeEvent(self.InitiateSegmentationEvent, str(True))

  def getRegistrationResultNameAndGeneratedSuffix(self, name):
    nOccurrences = sum([1 for result in self.data.getResultsAsList() if name in result.name])
    suffix = ""
    if nOccurrences:
      suffix = "_Retry_" + str(nOccurrences)
    return name, suffix

  def onInvokeRegistration(self, initial=True, retryMode=False, segmentationData=None):
    self.progress = ModuleWidgetMixin.createProgressDialog(maximum=4, value=1, windowFlags=qt.Qt.CustomizeWindowHint |
                                                                                           qt.Qt.WindowTitleHint)
    self.progress.setCancelButton(None)
    if initial:
      self.applyInitialRegistration(retryMode, segmentationData, progressCallback=self.updateProgressBar)
    else:
      self.applyRegistration(progressCallback=self.updateProgressBar)
    self.progress.close()
    self.progress = None
    logging.debug('Re-Registration is done')

  @onReturnProcessEvents
  def updateProgressBar(self, **kwargs):
    if self.progress:
      for key, value in kwargs.items():
        if hasattr(self.progress, key):
          setattr(self.progress, key, value)

  def generateNameAndCreateRegistrationResult(self, fixedVolume):
    name, suffix = self.getRegistrationResultNameAndGeneratedSuffix(fixedVolume.GetName())
    result = self.data.createResult(name + suffix)
    result.suffix = suffix
    self.registrationLogic.registrationResult = result
    return result

  def applyInitialRegistration(self, retryMode, segmentationData, progressCallback=None):
    if not retryMode:
      self.data.initializeRegistrationResults()

    self.runBRAINSResample(inputVolume=self.fixedLabel, referenceVolume=self.fixedVolume,
                           outputVolume=self.fixedLabel)
    self._runRegistration(self.fixedVolume, self.fixedLabel, self.movingVolume,
                          self.movingLabel, self.movingTargets, segmentationData, progressCallback)

  def applyRegistration(self, progressCallback=None):

    coverProstateRegResult = self.data.getMostRecentApprovedCoverProstateRegistration()
    lastRigidTfm = self.data.getLastApprovedRigidTransformation()
    lastApprovedTfm = self.data.getMostRecentApprovedTransform()
    initialTransform = lastApprovedTfm if lastApprovedTfm else lastRigidTfm


    fixedLabel = self.volumesLogic.CreateAndAddLabelVolume(slicer.mrmlScene, self.currentSeriesVolume,
                                                           self.currentSeriesVolume.GetName() + '-label')

    self.runBRAINSResample(inputVolume=coverProstateRegResult.labels.fixed, referenceVolume=self.currentSeriesVolume,
                           outputVolume=fixedLabel, warpTransform=initialTransform)

    self.dilateMask(fixedLabel, dilateValue=self.segmentedLabelValue)
    self._runRegistration(self.currentSeriesVolume, fixedLabel, coverProstateRegResult.volumes.fixed,
                          coverProstateRegResult.labels.fixed, coverProstateRegResult.targets.approved, None,
                          progressCallback)

  def _runRegistration(self, fixedVolume, fixedLabel, movingVolume, movingLabel, targets, segmentationData,
                       progressCallback):
    result = self.generateNameAndCreateRegistrationResult(fixedVolume)
    result.receivedTime = self.seriesTimeStamps[result.name.replace(result.suffix, "")]
    if segmentationData:
      result.segmentationData = segmentationData
    parameterNode = slicer.vtkMRMLScriptedModuleNode()
    parameterNode.SetAttribute('FixedImageNodeID', fixedVolume.GetID())
    parameterNode.SetAttribute('FixedLabelNodeID', fixedLabel.GetID())
    parameterNode.SetAttribute('MovingImageNodeID', movingVolume.GetID())
    parameterNode.SetAttribute('MovingLabelNodeID', movingLabel.GetID())
    parameterNode.SetAttribute('TargetsNodeID', targets.GetID())
    result.startTime = self.getTime()
    self.registrationLogic.run(parameterNode, progressCallback=progressCallback)
    result.endTime = self.getTime()
    self.addTargetsToMRMLScene(result)
    if self.seriesTypeManager.isCoverProstate(self.currentSeries) and self.temporaryIntraopTargets:
      self.addTemporaryTargetsToResult(result)
    self.invokeEvent(self.InitiateEvaluationEvent)

  def addTargetsToMRMLScene(self, result):
    targetNodes = result.targets.asDict()
    for regType in RegistrationTypeData.RegistrationTypes:
      if targetNodes[regType]:
        slicer.mrmlScene.AddNode(targetNodes[regType])

  def addTemporaryTargetsToResult(self, result):
    length = self.temporaryIntraopTargets.GetNumberOfFiducials()
    targetNodes = result.targets.asDict()
    for targetList in [targetNodes[r] for r in RegistrationTypeData.RegistrationTypes if targetNodes[r]]:
      for i in range(length):
        targetList.AddFiducialFromArray(self.getTargetPosition(self.temporaryIntraopTargets, i),
                                        self.temporaryIntraopTargets.GetNthFiducialLabel(i))

  def onRegistrationResultStatusChanged(self, caller, event):
    self.skipAllUnregisteredPreviousSeries(self.currentResult.name)
    if self._busy:
      return
    self.save()
    self.invokeEvent(self.RegistrationStatusChangedEvent)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onNewRegistrationResultCreated(self, caller, event, callData):
    self.currentResult = callData

  def skipAllUnregisteredPreviousSeries(self, series):
    selectedSeriesNumber = RegistrationResult.getSeriesNumberFromString(series)
    for series in [series for series in self.seriesList if not self.seriesTypeManager.isCoverTemplate(series)]:
      currentSeriesNumber = RegistrationResult.getSeriesNumberFromString(series)
      if currentSeriesNumber < selectedSeriesNumber and self.isTrackingPossible(series):
        results = self.data.getResultsBySeriesNumber(currentSeriesNumber)
        if len(results) == 0:
          self.skipSeries(series)
      elif currentSeriesNumber >= selectedSeriesNumber:
        break

  def skipSeries(self, series):
    volume = self.getOrCreateVolumeForSeries(series)
    name, suffix = self.getRegistrationResultNameAndGeneratedSuffix(volume.GetName())
    result = self.data.createResult(name+suffix)
    result.volumes.fixed = volume
    result.receivedTime = self.seriesTimeStamps[result.name.replace(result.suffix, "")]
    result.skip()

  def skip(self, series):
    self.skipAllUnregisteredPreviousSeries(series)
    self.skipSeries(series)
    self.save()

  def _getConsent(self):
    return RadioButtonChoiceMessageBox("Who gave consent?", options=["Clinician", "Operator"]).exec_()
