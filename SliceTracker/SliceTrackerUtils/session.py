import os, logging
import sys
import vtk, ctk, ast
import qt
import xml
import getpass
import datetime

import slicer
from sessionData import SessionData, RegistrationResult, RegistrationTypeData, SegmentationData, PreopData
from constants import SliceTrackerConstants
from helpers import SeriesTypeManager
from algorithms.automaticProstateSegmentation import AutomaticSegmentationLogic

from SlicerDevelopmentToolboxUtils.constants import DICOMTAGS, FileExtension, STYLE
from SlicerDevelopmentToolboxUtils.events import SlicerDevelopmentToolboxEvents
from SlicerDevelopmentToolboxUtils.helpers import SmartDICOMReceiver
from SlicerDevelopmentToolboxUtils.mixins import ModuleWidgetMixin, ModuleLogicMixin
from SlicerDevelopmentToolboxUtils.exceptions import DICOMValueError, PreProcessedDataError, UnknownSeriesError
from SlicerDevelopmentToolboxUtils.widgets import IncomingDataWindow, CustomStatusProgressbar
from SlicerDevelopmentToolboxUtils.widgets import SliceWidgetConfirmYesNoDialog
from SlicerDevelopmentToolboxUtils.widgets import RadioButtonChoiceMessageBox
from SlicerDevelopmentToolboxUtils.decorators import singleton
from SlicerDevelopmentToolboxUtils.decorators import onExceptionReturnFalse, onReturnProcessEvents, onExceptionReturnNone
from SlicerDevelopmentToolboxUtils.module.session import StepBasedSession


from SliceTrackerRegistration import SliceTrackerRegistrationLogic
from steps.plugins.segmentationValidator import SliceTrackerSegmentationValidatorPlugin
from .algorithms.registration import *


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
    self.seriesTypeManager = SeriesTypeManager()
    self.seriesTypeManager.addEventObserver(self.seriesTypeManager.SeriesTypeManuallyAssignedEvent,
                                            lambda caller, event: self.invokeEvent(self.SeriesTypeManuallyAssignedEvent))
    self.resetAndInitializeMembers()

  def resetAndInitializeMembers(self):
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

  def isPreProcessing(self):
    return slicer.util.selectedModule() != self.MODULE_NAME

  def isCaseDirectoryValid(self):
    return os.path.exists(self.preopDICOMDirectory) and os.path.exists(self.intraopDICOMDirectory)

  def isRunning(self):
    return not self.directory in [None, '']

  def processDirectory(self):
    self.newCaseCreated = getattr(self, "newCaseCreated", False)
    if self.newCaseCreated:
      return
    if not self.directory or not self.isCaseDirectoryValid():
      slicer.util.warningDisplay("The selected case directory seems not to be valid", windowTitle="SliceTracker")
      self.close(save=False)
    else:
      self.loadCaseData()
      self.invokeEvent(self.CaseOpenedEvent)

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
      self._loading = True
      self.data.load(filename)
      self.postProcessLoadedSessionData()
      self._loading = False
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
    self.startIntraopDICOMReceiver()

  def resetPreopDICOMReceiver(self):
    self.preopDICOMReceiver = getattr(self, "preopDICOMReceiver", None)
    if self.preopDICOMReceiver:
      self.preopDICOMReceiver.hide()
      self.preopDICOMReceiver.removeEventObservers()
      self.preopDICOMReceiver = None

  def startIntraopDICOMReceiver(self):
    self.resetPreopDICOMReceiver()
    logging.info("Starting DICOM Receiver for intra-procedural data")
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
    for seriesNumber, receivedInfo in seriesNumberPatientID.iteritems():
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
      keylist = self.loadableList.keys()
      keylist.sort(key=lambda x: RegistrationResult.getSeriesNumberFromString(x))
      return self.loadableList[keylist[0]][0]
    else:
      return None

  def getOrCreateVolumeForSeries(self, series):
    try:
      volume = self.alreadyLoadedSeries[series]
    except KeyError:
      logging.info("Need to load volume")
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
    if not os.path.exists(os.path.join(self.outputDirectory, SliceTrackerConstants.JSON_FILENAME)):
      if PreopDataHandler.wasDirectoryPreprocessed(self.preprocessedDirectory):
        preopDataManager = self.createPreopHandler()
        preopDataManager.loadPreProcessedData()
      else:
        if len(os.listdir(self.preopDICOMDirectory)):
          self.onPreopDataReceptionFinished()
        elif len(os.listdir(self.intraopDICOMDirectory)):
          self.data.usePreopData = False
          self.startIntraopDICOMReceiver()
        else:
          self.startPreopDICOMReceiver()
    else:
      self.openSavedSession()

  def onPreprocessingSuccessful(self, caller, event):
    self.setupPreopLoadedTargets()
    self.invokeEvent(self.PreprocessingSuccessfulEvent)
    self.startIntraopDICOMReceiver()

  def openSavedSession(self):
    self.load()

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
    return self.isAnyListItemInString(seriesType, [self.getSetting("COVER_TEMPLATE"), self.getSetting("COVER_PROSTATE"),
                                                   self.getSetting("NEEDLE_IMAGE")])

  def resultHasNotBeenProcessed(self, series):
    return not (self.data.registrationResultWasApproved(series) or
                self.data.registrationResultWasSkipped(series) or
                self.data.registrationResultWasRejected(series))

  def isEligibleForSkipping(self, series):
    seriesType = self.seriesTypeManager.getSeriesType(series)
    return not self.isAnyListItemInString(seriesType,[self.getSetting("COVER_PROSTATE"), self.getSetting("COVER_TEMPLATE")])

  def isLoading(self):
    self._loading = getattr(self, "_loading", False)
    return self._loading

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
      for key, value in kwargs.iteritems():
        if hasattr(self.progress, key):
          setattr(self.progress, key, value)

  def generateNameAndCreateRegistrationResult(self, fixedVolume):
    name, suffix = self.getRegistrationResultNameAndGeneratedSuffix(fixedVolume.GetName())
    result = self.data.createResult(name + suffix)
    result.suffix = suffix
    return result

  def applyInitialRegistration(self, retryMode, segmentationData, progressCallback=None):
    if not retryMode:
      self.data.initializeRegistrationResults()

    self.runBRAINSResample(inputVolume=self.fixedLabel, referenceVolume=self.fixedVolume,
                           outputVolume=self.fixedLabel)
    parameterNode = SliceTrackerRegistrationLogic.initializeParameterNode(self.fixedVolume, self.fixedLabel,
                                                                          self.movingVolume, self.movingLabel,
                                                                          self.movingTargets)
    self._runRegistration(parameterNode, segmentationData, progressCallback)

  def applyRegistration(self, progressCallback=None):

    coverProstateRegResult = self.data.getMostRecentApprovedCoverProstateRegistration()
    lastApprovedTfm = self.data.getMostRecentApprovedTransform()
    initialTransform = lastApprovedTfm if lastApprovedTfm else self.data.getLastApprovedRigidTransformation()

    fixedLabel = self.volumesLogic.CreateAndAddLabelVolume(slicer.mrmlScene, self.currentSeriesVolume,
                                                           self.currentSeriesVolume.GetName() + '-label')

    self.runBRAINSResample(inputVolume=coverProstateRegResult.labels.fixed, referenceVolume=self.currentSeriesVolume,
                           outputVolume=fixedLabel, warpTransform=initialTransform)

    self.dilateMask(fixedLabel, dilateValue=self.segmentedLabelValue)
    parameterNode = SliceTrackerRegistrationLogic.initializeParameterNode(self.currentSeriesVolume, fixedLabel,
                                                                          coverProstateRegResult.volumes.fixed,
                                                                          coverProstateRegResult.labels.fixed,
                                                                          coverProstateRegResult.targets.approved)
    self._runRegistration(parameterNode, None, progressCallback)

  def _runRegistration(self, parameterNode, segmentationData, progressCallback):
    fixedVolume = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('FixedImageNodeID'))
    result = self.generateNameAndCreateRegistrationResult(fixedVolume)
    result.receivedTime = self.seriesTimeStamps[result.name.replace(result.suffix, "")]
    if segmentationData:
      result.segmentationData = segmentationData
    result.startTime = self.getTime()
    registrationLogic = self.getRegistrationLogic()
    registrationLogic.run(parameterNode, result, progressCallback=progressCallback)
    result.endTime = self.getTime()

    self.addTargetsToMRMLScene(result)
    if self.seriesTypeManager.isCoverProstate(self.currentSeries) and self.temporaryIntraopTargets:
      self.addTemporaryTargetsToResult(result)
    self.invokeEvent(self.InitiateEvaluationEvent)

  def getRegistrationLogic(self):
    preferred = self.getSetting("Prostate_Registration_Tool")
    fallback = self.getSetting("Prostate_Registration_Fallback")
    registrationToolClass = getattr(sys.modules[__name__], preferred)
    if not registrationToolClass.isToolAvailable():
      registrationToolClass = getattr(sys.modules[__name__], fallback)
      if not registrationToolClass.isToolAvailable():
        raise RuntimeError("Neither preferred registration algorithm: {}, nor fallback registration algorithm {}"
                           "are available. Make sure to install required dependencies".format(preferred, fallback))
      logging.info("Preferred registration algorithm: {} not available. Falling back to {}. ".format(preferred, fallback))
    return SliceTrackerRegistrationLogic(registrationToolClass())

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
    self._loading = getattr(self, "_loading", None)
    if self._loading:
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


class PreprocessedDataHandlerBase(ModuleWidgetMixin, ModuleLogicMixin):

  PreprocessingStartedEvent = vtk.vtkCommand.UserEvent + 434
  PreprocessingFinishedEvent = vtk.vtkCommand.UserEvent + 435
  PreprocessedDataErrorEvent = vtk.vtkCommand.UserEvent + 436

  @staticmethod
  def wasDirectoryPreprocessed(directory):
    raise NotImplementedError

  @property
  def outputDirectory(self):
    self._outputDirectory = getattr(self, "_outputDirectory", None)
    return self._outputDirectory

  @outputDirectory.setter
  def outputDirectory(self, directory):
    if directory and not os.path.exists(directory):
      self.createDirectory(directory)
    self._outputDirectory = directory

  def __init__(self, inputDirectory, outputDirectory):
    self._inputDirectory = inputDirectory
    self.outputDirectory = outputDirectory

  def cleanup(self):
    pass

  def handle(self):
    raise NotImplementedError


class PreopDataHandler(PreprocessedDataHandlerBase):

  MODULE_NAME = SliceTrackerConstants.MODULE_NAME

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

  def __init__(self, inputDirectory, outputDirectory, data):
    super(PreopDataHandler, self).__init__(inputDirectory, outputDirectory)
    self.data = data

  def __del__(self):
    super(PreopDataHandler, self).__del__()

  def handle(self):
    self.invokeEvent(self.PreprocessingStartedEvent)
    if self._runPreProcessing():
      self.runModule(invokeEvent=False)
    else:
      slicer.util.infoDisplay("No DICOM data could be processed. Please select another directory.",
                              windowTitle="SliceTracker")

  def _runPreProcessing(self):
    from mpReviewPreprocessor import mpReviewPreprocessorLogic
    mpReviewPreprocessorLogic = mpReviewPreprocessorLogic()
    progress = self.createProgressDialog()
    progress.canceled.connect(lambda : mpReviewPreprocessorLogic.cancelProcess())

    @onReturnProcessEvents
    def updateProgressBar(**kwargs):
      for key, value in kwargs.iteritems():
        if hasattr(progress, key):
          setattr(progress, key, value)

    success = mpReviewPreprocessorLogic.importAndProcessData(self._inputDirectory, outputDir=self.outputDirectory,
                                                             copyDICOM=False,
                                                             progressCallback=updateProgressBar)
    progress.canceled.disconnect(lambda : mpReviewPreprocessorLogic.cancelProcess())
    progress.close()
    return success

  def runModule(self, invokeEvent=True):

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
    if invokeEvent:
      self.invokeEvent(self.PreprocessingStartedEvent)

  def loadPreProcessedData(self):
    try:
      self.loadMpReviewProcessedData()
    except PreProcessedDataError:
      self.invokeEvent(self.PreprocessedDataErrorEvent)

  def loadMpReviewProcessedData(self):
    studyDir = self.getFirstMpReviewPreprocessedStudy(self.outputDirectory)
    resourcesDir = os.path.join(studyDir, 'RESOURCES')
    if not self.isMpReviewStudyDirectoryValid(resourcesDir):
      self.invokeEvent(self.PreprocessedDataErrorEvent)
      return

    self.findPreopImageAndSegmentationPaths(resourcesDir)

    message = None
    if not self.preopSegmentationPath:
      slicer.util.errorDisplay("No eligible series found for preop AX T2 segmentation. MpReview might not have "
                               "processed the right series or series names are different from BWH internally used ones")
      self.invokeEvent(self.PreprocessedDataErrorEvent)
      return

    self.data.initialTargetsPath = os.path.join(studyDir, 'Targets')

    loadedPreopVolume = self.loadPreopVolume()
    loadedPreopTargets = self.loadPreopTargets()
    loadedPreopT2Label = self.loadT2Label() if os.path.exists(self.preopSegmentationPath) else False

    if message or not (loadedPreopT2Label and loadedPreopVolume and loadedPreopTargets):
      if loadedPreopTargets and loadedPreopVolume and \
          self.getSetting("Use_Deep_Learning", moduleName=self.MODULE_NAME).lower() == "true":
        if slicer.util.confirmYesNoDisplay("No WholeGland segmentation found in preop data. Automatic segmentation is "
                                           "available. Would you like to proceed with the automatic segmentation?",
                                           windowTitle="SliceTracker"):
          self._createPreopData(algorithm="Automatic")
          self.runAutomaticSegmentation()
        else:
          self.onPreopLoadingFailed()
      else:
        self.onPreopLoadingFailed()
    else:
      if not self.data.usePreopData:
        self._createPreopData(algorithm="Manual")
        self.segmentationData.note = "mpReview preprocessed"
        self.segmentationData._label = self.data.initialLabel
      self.invokeEvent(self.PreprocessingFinishedEvent)

  def _createPreopData(self, algorithm, segmentationType="Prostate"):
    self.preopData = PreopData()
    self.segmentationData = SegmentationData(segmentationType=segmentationType, algorithm=algorithm)

  def runAutomaticSegmentation(self):
    logic = AutomaticSegmentationLogic()
    logic.addEventObserver(logic.DeepLearningFinishedEvent, self.onSegmentationFinished)
    logic.addEventObserver(logic.DeepLearningFailedEvent, self.onPreopLoadingFailed)
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
      self.invokeEvent(self.PreprocessedDataErrorEvent)
      return

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
    segmentationValidator.addEventObserver(segmentationValidator.ModifiedEvent, self.onSegmentationModificationStarted)
    segmentationValidator.addEventObserver(segmentationValidator.FinishedEvent, self.onSegmentationValidated)
    segmentationValidator.addEventObserver(segmentationValidator.CanceledEvent, self.onSegmentationValidationFailed)
    segmentationValidator.run()

  def onSegmentationModificationStarted(self, caller, event):
    self.segmentationData.setModified(startTime=self.getTime())

  def onSegmentationValidationFailed(self, caller=None, event=None):
    message = "Segmentation validation failed.\nWholeGland segmentation is require to proceed." \
              "\n\nDo you want to " \
              "open/revisit pre-processing for the current case?"
    if slicer.util.confirmYesNoDisplay(message, windowTitle="SliceTracker"):
      # TODO: start intraop without listening to signals
      self.runModule()
    else:
      self.invokeEvent(self.PreprocessedDataErrorEvent)

  def isMpReviewStudyDirectoryValid(self, resourcesDir):
    logging.debug(resourcesDir)
    if not os.path.exists(resourcesDir):
      logging.info("The selected directory does not fit the mpReview directory structure. Make sure that you select "
                   "the study root directory which includes directories RESOURCES")
      return False
    return True

  def onPreopLoadingFailed(self, caller=None, event=None):
    message = "Loading preop data failed.\nMake sure that the correct mpReview directory structure is used." \
              "\n\nSliceTracker expects a T2 volume, WholeGland segmentation and target(s). Do you want to " \
              "open/revisit pre-processing for the current case?"
    if slicer.util.confirmYesNoDisplay(message, windowTitle="SliceTracker"):
      # TODO: start intraop without listening to signals
      self.runModule()
    else:
      self.invokeEvent(self.PreprocessedDataErrorEvent)

  def findPreopImageAndSegmentationPaths(self, resourcesDir):
    from mpReview import mpReviewLogic
    seriesMap, metaFile = mpReviewLogic.loadMpReviewProcessedData(resourcesDir)

    self.preopImagePath = None
    self.preopSegmentationPath = None
    # segmentedColorName = self.getSetting("Segmentation_Color_Name", moduleName=self.MODULE_NAME) # TODO: correct?

    for series in seriesMap:
      seriesName = str(seriesMap[series]['LongName'])
      logging.debug('series Number ' + series + ' ' + seriesName)

      imagePath = os.path.join(seriesMap[series]['NRRDLocation'])

      xmlFile = imagePath.replace(".nrrd", ".xml")
      dom = xml.dom.minidom.parse(xmlFile)
      seriesDescription = self.findElement(dom, "SeriesDescription")

      segmentationsPath = os.path.join(os.path.dirname(os.path.dirname(imagePath)), 'Segmentations')

      import re
      regex = self.getSetting("PLANNING_IMAGE", moduleName=self.MODULE_NAME)

      if re.match(regex, seriesDescription) or seriesDescription == regex:
        logging.debug(' FOUND THE SERIES OF INTEREST, ITS ' + seriesName)
        logging.debug(' LOCATION OF VOLUME : ' + str(seriesMap[series]['NRRDLocation']))
        logging.debug(' LOCATION OF IMAGE path : ' + str(imagePath))
        logging.debug(' EXPECTED LOCATION OF SEGMENTATION path : ' + segmentationsPath)

        self.preopImagePath = seriesMap[series]['NRRDLocation']
        self.preopSegmentationPath = segmentationsPath
        break

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