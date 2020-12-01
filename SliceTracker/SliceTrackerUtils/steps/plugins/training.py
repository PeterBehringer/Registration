import os
import ast
import shutil
import qt
import vtk
import ctk
import slicer

from ...constants import SliceTrackerConstants
from ..base import SliceTrackerPlugin

from SlicerDevelopmentToolboxUtils.helpers import SampleDataDownloader
from SlicerDevelopmentToolboxUtils.decorators import *


class SliceTrackerTrainingPlugin(SliceTrackerPlugin):

  NAME = "Training"

  def __init__(self):
    super(SliceTrackerTrainingPlugin, self).__init__()
    self.sampleDownloader = SampleDataDownloader(True)

  def setup(self):
    super(SliceTrackerTrainingPlugin, self).setup()
    self.collapsibleTrainingArea = ctk.ctkCollapsibleButton()
    self.collapsibleTrainingArea.collapsed = True
    self.collapsibleTrainingArea.text = "Training Incoming Data Simulation"

    self.simulatePreopPhaseButton = self.createButton("Simulate preop reception", enabled=False)
    self.simulateIntraopPhaseButton = self.createButton("Simulate intraop reception", enabled=False)

    self.trainingsAreaLayout = qt.QGridLayout(self.collapsibleTrainingArea)
    self.trainingsAreaLayout.addWidget(self.createHLayout([self.simulatePreopPhaseButton,
                                                           self.simulateIntraopPhaseButton]))
    self.layout().addWidget(self.collapsibleTrainingArea)

  def setupConnections(self):
    self.simulatePreopPhaseButton.clicked.connect(self.startPreopPhaseSimulation)
    self.simulateIntraopPhaseButton.clicked.connect(self.startIntraopPhaseSimulation)

  def addSessionObservers(self):
    super(SliceTrackerTrainingPlugin, self).addSessionObservers()
    self.session.addEventObserver(self.session.IncomingDataSkippedEvent, self.onIncomingDataSkipped)

  def removeSessionEventObservers(self):
    super(SliceTrackerTrainingPlugin, self).removeSessionEventObservers()
    self.session.removeEventObserver(self.session.IncomingDataSkippedEvent, self.onIncomingDataSkipped)

  def startPreopPhaseSimulation(self):
    self.session.trainingMode = True
    if self.session.preopDICOMReceiver.dicomReceiver.isRunning():
      self.session.preopDICOMReceiver.dicomReceiver.stopStoreSCP()
    self.simulatePreopPhaseButton.enabled = False
    preopZipFile = self.initiateSampleDataDownload(SliceTrackerConstants.PREOP_SAMPLE_DATA_URL)
    if not self.sampleDownloader.wasCanceled() and preopZipFile:
      self.unzipFileAndCopyToDirectory(preopZipFile, self.session.preopDICOMDirectory)

  def startIntraopPhaseSimulation(self):
    self.simulateIntraopPhaseButton.enabled = False
    intraopZipFile = self.initiateSampleDataDownload(SliceTrackerConstants.INTRAOP_SAMPLE_DATA_URL)
    if not self.sampleDownloader.wasCanceled() and intraopZipFile:
      self.unzipFileAndCopyToDirectory(intraopZipFile, self.session.intraopDICOMDirectory)

  def initiateSampleDataDownload(self, url):
    filename = os.path.basename(url)
    self.sampleDownloader.resetAndInitialize()
    self.sampleDownloader.addEventObserver(self.sampleDownloader.StatusChangedEvent, self.onDownloadProgressUpdated)
    # self.customStatusProgressBar.show()
    downloadedFile = self.sampleDownloader.downloadFileIntoCache(url, filename)
    # self.customStatusProgressBar.hide()
    return None if self.sampleDownloader.wasCanceled() else downloadedFile

  @onReturnProcessEvents
  @vtk.calldata_type(vtk.VTK_STRING)
  def onDownloadProgressUpdated(self, caller, event, callData):
    message, percent = ast.literal_eval(callData)
    logging.info("%s, %s" %(message, percent))
    # self.customStatusProgressBar.updateStatus(message, percent)

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
      if self.session.preopDICOMReceiver:
        self.session.preopDICOMReceiver.hide()
      slicer.util.errorDisplay("An error appeared while extracting %s. If the file is corrupt, please delete it and try "
                               "again." % filepath, detailedText=str(exc.message))
      self.clearData()

  def copyDirectory(self, source, destination, recursive=True):
    print(source)
    assert os.path.isdir(source)
    for listObject in os.listdir(source):
      current = os.path.join(source, listObject)
      if os.path.isdir(current) and recursive:
        self.copyDirectory(current, destination, recursive)
      else:
        shutil.copy(current, destination)

  @logmethod(logging.DEBUG)
  def onNewCaseStarted(self, caller, event):
    self.simulatePreopPhaseButton.enabled = True

  def onIncomingDataSkipped(self, caller, event):
    self.simulatePreopPhaseButton.enabled = False
    self.simulateIntraopPhaseButton.enabled = True

  @vtk.calldata_type(vtk.VTK_STRING)
  def onCaseClosed(self, caller, event, callData):
    self.simulatePreopPhaseButton.enabled = False
    self.simulateIntraopPhaseButton.enabled = False

  def onPreprocessingSuccessful(self, caller, event):
    self.simulateIntraopPhaseButton.enabled = self.session.trainingMode
