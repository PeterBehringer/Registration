import ast
import ctk
import qt
import slicer
import vtk

from .base import SliceTrackerLogicBase, SliceTrackerStep
from .plugins.case import SliceTrackerCaseManagerPlugin
from .plugins.results import SliceTrackerRegistrationResultsPlugin
from .plugins.targets import SliceTrackerTargetTablePlugin
from .plugins.training import SliceTrackerTrainingPlugin
from .plugins.charts import SliceTrackerDisplacementChartPlugin
from ..constants import SliceTrackerConstants as constants
from ..sessionData import RegistrationResult
from ..helpers import IncomingDataMessageBox, SeriesTypeToolButton, SeriesTypeManager

from SlicerDevelopmentToolboxUtils.constants import COLOR
from SlicerDevelopmentToolboxUtils.widgets import CustomStatusProgressbar
from SlicerDevelopmentToolboxUtils.icons import Icons


class SliceTrackerOverViewStepLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerOverViewStepLogic, self).__init__()

  def applyBiasCorrection(self):
    outputVolume = slicer.vtkMRMLScalarVolumeNode()
    outputVolume.SetName('VOLUME-PREOP-N4')
    slicer.mrmlScene.AddNode(outputVolume)
    params = {'inputImageName': self.session.data.initialVolume.GetID(),
              'maskImageName': self.session.data.initialLabel.GetID(),
              'outputImageName': outputVolume.GetID(),
              'numberOfIterations': '500,400,300'}

    slicer.cli.run(slicer.modules.n4itkbiasfieldcorrection, None, params, wait_for_completion=True)
    self.session.data.initialVolume = outputVolume
    self.session.data.preopData.usedERC = True


class SliceTrackerOverviewStep(SliceTrackerStep):

  NAME = "Overview"
  LogicClass = SliceTrackerOverViewStepLogic
  LayoutClass = qt.QVBoxLayout

  def __init__(self):
    super(SliceTrackerOverviewStep, self).__init__()
    self.cleanup()

  def cleanup(self):
    self._seriesModel.clear()
    self.notifyUserAboutNewData = True
    self.changeSeriesTypeButton.enabled = False
    self.trackTargetsButton.enabled = False
    self.skipIntraopSeriesButton.enabled = False
    self.updateIntraopSeriesSelectorTable()

  def setupIcons(self):
    self.trackIcon = self.createIcon('icon-track.png')

  def setup(self):
    super(SliceTrackerOverviewStep, self).setup()
    iconSize = qt.QSize(24, 24)
    self.caseManagerPlugin = SliceTrackerCaseManagerPlugin()
    self.trainingPlugin = SliceTrackerTrainingPlugin()


    self.changeSeriesTypeButton = SeriesTypeToolButton()
    self.trackTargetsButton = self.createButton("", icon=self.trackIcon, iconSize=iconSize, toolTip="Track targets",
                                                enabled=False)
    self.skipIntraopSeriesButton = self.createButton("", icon=Icons.skip, iconSize=iconSize,
                                                     toolTip="Skip selected series", enabled=False)
    self.setupIntraopSeriesSelector()

    self.setupRegistrationResultsPlugin()

    self.targetTablePlugin = SliceTrackerTargetTablePlugin()
    self.addPlugin(self.targetTablePlugin)

    self.displacementChartPlugin = SliceTrackerDisplacementChartPlugin()
    self.addPlugin(self.displacementChartPlugin)

    self.displacementChartPlugin.addEventObserver(self.displacementChartPlugin.ShowEvent, self.onShowDisplacementChart)
    self.displacementChartPlugin.addEventObserver(self.displacementChartPlugin.HideEvent, self.onHideDisplacementChart)
    self.displacementChartPlugin.collapsibleButton.hide()

    self.layout().addWidget(self.caseManagerPlugin)
    self.layout().addWidget(self.trainingPlugin)
    self.layout().addWidget(self.targetTablePlugin)
    self.layout().addWidget(self.createHLayout([self.intraopSeriesSelector, self.changeSeriesTypeButton,
                                                self.trackTargetsButton, self.skipIntraopSeriesButton]))
    self.layout().addWidget(self.regResultsCollapsibleButton)
    self.layout().addWidget(self.displacementChartPlugin.collapsibleButton)
    self.layout().addStretch(1)

  def onShowDisplacementChart(self, caller, event):
    layout = slicer.app.layoutManager().layout
    if layout == constants.LAYOUT_SIDE_BY_SIDE:
      self.displacementChartPlugin.collapsibleButton.show()

  def onHideDisplacementChart(self, caller, event):
    self.displacementChartPlugin.collapsibleButton.hide()

  def setupRegistrationResultsPlugin(self):
    self.regResultsCollapsibleButton = ctk.ctkCollapsibleButton()
    self.regResultsCollapsibleButton.collapsed = True
    self.regResultsCollapsibleButton.text = "Registration Evaluation"
    self.regResultsCollapsibleButton.hide()
    self.regResultsCollapsibleLayout= qt.QGridLayout(self.regResultsCollapsibleButton)
    self.regResultsPlugin = SliceTrackerRegistrationResultsPlugin()
    self.regResultsPlugin.addEventObserver(self.regResultsPlugin.NoRegistrationResultsAvailable,
                                            self.onNoRegistrationResultsAvailable)
    self.regResultsPlugin.resultSelectorVisible = False
    self.regResultsPlugin.titleVisible = False
    self.regResultsPlugin.visualEffectsTitle = ""
    self.regResultsPlugin.registrationTypeButtonsVisible = False
    self.addPlugin(self.regResultsPlugin)
    self.regResultsCollapsibleLayout.addWidget(self.regResultsPlugin)

  def setupIntraopSeriesSelector(self):
    self.intraopSeriesSelector = qt.QComboBox()
    self.intraopSeriesSelector.setSizePolicy(qt.QSizePolicy.Expanding, qt.QSizePolicy.Minimum)
    self._seriesModel = qt.QStandardItemModel()
    self.intraopSeriesSelector.setModel(self._seriesModel)
    self.intraopSeriesSelector.setToolTip(constants.IntraopSeriesSelectorToolTip)

  def setupConnections(self):
    super(SliceTrackerOverviewStep, self).setupConnections()
    self.skipIntraopSeriesButton.clicked.connect(self.onSkipIntraopSeriesButtonClicked)
    self.trackTargetsButton.clicked.connect(self.onTrackTargetsButtonClicked)
    self.intraopSeriesSelector.connect('currentIndexChanged(QString)', self.onIntraopSeriesSelectionChanged)

  def addSessionObservers(self):
    super(SliceTrackerOverviewStep, self).addSessionObservers()
    self.session.addEventObserver(self.session.SeriesTypeManuallyAssignedEvent, self.onSeriesTypeManuallyAssigned)
    self.session.addEventObserver(self.session.RegistrationStatusChangedEvent, self.onRegistrationStatusChanged)
    self.session.addEventObserver(self.session.ZFrameRegistrationSuccessfulEvent, self.onZFrameRegistrationSuccessful)

  def removeSessionEventObservers(self):
    SliceTrackerStep.removeSessionEventObservers(self)
    self.session.removeEventObserver(self.session.SeriesTypeManuallyAssignedEvent, self.onSeriesTypeManuallyAssigned)
    self.session.removeEventObserver(self.session.RegistrationStatusChangedEvent, self.onRegistrationStatusChanged)
    self.session.removeEventObserver(self.session.ZFrameRegistrationSuccessfulEvent, self.onZFrameRegistrationSuccessful)

  def onSkipIntraopSeriesButtonClicked(self):
    if slicer.util.confirmYesNoDisplay("Do you really want to skip this series?", windowTitle="Skip series?"):
      self.session.skip(self.intraopSeriesSelector.currentText)
      self.updateIntraopSeriesSelectorTable()

  def onTrackTargetsButtonClicked(self):
    self.session.takeActionForCurrentSeries()

  def onIntraopSeriesSelectionChanged(self, selectedSeries=None):
    self.session.currentSeries = selectedSeries
    if selectedSeries:
      trackingPossible = self.session.isTrackingPossible(selectedSeries)
      self.setIntraopSeriesButtons(trackingPossible, selectedSeries)
      self.configureViewersForSelectedIntraopSeries(selectedSeries)
      self.changeSeriesTypeButton.setSeries(selectedSeries)
    colorStyle = self.session.getColorForSelectedSeries(self.intraopSeriesSelector.currentText)
    self.intraopSeriesSelector.setStyleSheet("QComboBox{%s} QToolTip{background-color: white;}" % colorStyle)

  def configureViewersForSelectedIntraopSeries(self, selectedSeries):
    if self.session.data.registrationResultWasApproved(selectedSeries) or \
            self.session.data.registrationResultWasRejected(selectedSeries):
      if self.session.seriesTypeManager.isCoverProstate(selectedSeries) and not self.session.data.usePreopData:
        result = self.session.data.getResult(selectedSeries)
        self.currentResult = result.name if result else None
        self.regResultsPlugin.onLayoutChanged()
        self.regResultsCollapsibleButton.hide()
      else:
        self.currentResult = self.session.data.getApprovedOrLastResultForSeries(selectedSeries).name
        self.regResultsCollapsibleButton.show()
        self.regResultsPlugin.setVisible(True)
        self.regResultsPlugin.onLayoutChanged()
      self.targetTablePlugin.currentTargets = self.currentResult.targets.approved if self.currentResult.approved \
        else self.currentResult.targets.bSpline
    else:
      result = self.session.data.getResult(selectedSeries)
      self.currentResult = result.name if result else None
      self.regResultsPlugin.onLayoutChanged()
      self.regResultsCollapsibleButton.hide()
      if not self.session.data.registrationResultWasSkipped(selectedSeries):
        self.regResultsPlugin.cleanup()

      if self.session.seriesTypeManager.isVibe(selectedSeries):
        volume = self.session.getOrCreateVolumeForSeries(selectedSeries)
        self.setupFourUpView(volume)
        result = self.session.data.getMostRecentApprovedResult(priorToSeriesNumber=RegistrationResult.getSeriesNumberFromString(selectedSeries))
        if result:
          self.targetTablePlugin.currentTargets = result.targets.approved
          self.regResultsPlugin.showIntraopTargets(result.targets.approved)
        else:
          self.targetTablePlugin.currentTargets = None
      else:
        self.targetTablePlugin.currentTargets = None

  def setIntraopSeriesButtons(self, trackingPossible, selectedSeries):
    trackingPossible = trackingPossible and not self.session.data.completed
    self.changeSeriesTypeButton.enabled = not self.session.data.exists(selectedSeries)
    if self.session.seriesTypeManager.isCoverTemplate(selectedSeries):
      self.changeSeriesTypeButton.enabled = self.session.isCoverTemplateTrackable(selectedSeries)
    self.trackTargetsButton.enabled = trackingPossible
    self.skipIntraopSeriesButton.enabled = trackingPossible and self.session.isEligibleForSkipping(selectedSeries)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onCurrentSeriesChanged(self, caller, event, callData=None):
    if callData:
      model = self.intraopSeriesSelector.model()
      index = next((i for i in range(model.rowCount()) if model.item(i).text() == callData), None)
      self.intraopSeriesSelector.currentIndex = index

  def onZFrameRegistrationSuccessful(self, caller, event):
    self.active = True

  def onRegistrationStatusChanged(self, caller, event):
    self.active = True

  def onLoadingMetadataSuccessful(self, caller, event):
    self.active = True

  @vtk.calldata_type(vtk.VTK_STRING)
  def onCaseClosed(self, caller, event, callData):
    if callData != "None":
      slicer.util.infoDisplay(callData, windowTitle="SliceTracker")
    self.cleanup()
    slicer.mrmlScene.Clear(0)

  def onNoRegistrationResultsAvailable(self, caller, event):
    self.targetTablePlugin.currentTargets = None

  def onPreprocessingSuccessful(self, caller, event):
    self.updateIntraopSeriesSelectorTable()
    self.configureRedSliceNodeForPreopData()
    self.promptUserAndApplyBiasCorrectionIfNeeded()
    if not self.session.isBusy():
      self.session.save()

  def onActivation(self):
    super(SliceTrackerOverviewStep, self).onActivation()
    self.updateIntraopSeriesSelectorTable()

  def onSeriesTypeManuallyAssigned(self, caller, event):
    self.updateIntraopSeriesSelectorTable()

  @vtk.calldata_type(vtk.VTK_STRING)
  def onNewImageSeriesReceived(self, caller, event, callData):
    if not self.session.isBusy():
      customStatusProgressBar = CustomStatusProgressbar()
      customStatusProgressBar.text = "New image data has been received."
      if self.session.intraopDICOMReceiver:
        self.session.intraopDICOMReceiver.forceStatusChangeEventUpdate()

    self.updateIntraopSeriesSelectorTable()

    if not self.active or self.session.isBusy():
      return

    selectedSeries = self.intraopSeriesSelector.currentText
    if selectedSeries != "" and self.session.isTrackingPossible(selectedSeries):
      selectedSeriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)

      newImageSeries = ast.literal_eval(callData)
      newImageSeriesNumbers = [RegistrationResult.getSeriesNumberFromString(s) for s in newImageSeries]
      if selectedSeriesNumber in newImageSeriesNumbers:
        self.takeActionOnSelectedSeries()

  def onCaseOpened(self, caller, event):
    if not self.active or self.session.isBusy():
      return
    self.selectMostRecentEligibleSeries()

  def takeActionOnSelectedSeries(self):
    selectedSeries = self.intraopSeriesSelector.currentText
    if self.session.seriesTypeManager.isCoverTemplate(selectedSeries) and not self.session.zFrameRegistrationSuccessful:
      self.onTrackTargetsButtonClicked()
      return

    if self.session.isTrackingPossible(self.intraopSeriesSelector.currentText):
      if self.notifyUserAboutNewData and not self.session.data.completed:
        dialog = IncomingDataMessageBox()
        self.notifyUserAboutNewDataAnswer, checked = dialog.exec_()
        self.notifyUserAboutNewData = not checked
      if hasattr(self, "notifyUserAboutNewDataAnswer") and self.notifyUserAboutNewDataAnswer == qt.QMessageBox.AcceptRole:
        self.onTrackTargetsButtonClicked()

  def updateIntraopSeriesSelectorTable(self):
    self.intraopSeriesSelector.blockSignals(True)
    currentIndex = self.intraopSeriesSelector.currentIndex
    self._seriesModel.clear()
    for series in self.session.seriesList:
      sItem = qt.QStandardItem(series)
      self._seriesModel.appendRow(sItem)
      color = COLOR.YELLOW
      if self.session.data.registrationResultWasApproved(series) or \
        (self.session.seriesTypeManager.isCoverTemplate(series) and not self.session.isCoverTemplateTrackable(series)):
        color = COLOR.GREEN
      elif self.session.data.registrationResultWasSkipped(series):
        color = COLOR.RED
      elif self.session.data.registrationResultWasRejected(series):
        color = COLOR.GRAY
      self._seriesModel.setData(sItem.index(), color, qt.Qt.BackgroundRole)
    self.intraopSeriesSelector.setCurrentIndex(currentIndex)
    self.intraopSeriesSelector.blockSignals(False)
    colorStyle = self.session.getColorForSelectedSeries(self.intraopSeriesSelector.currentText)
    self.intraopSeriesSelector.setStyleSheet("QComboBox{%s} QToolTip{background-color: white;}" % colorStyle)
    if self.active and not self.session.isBusy():
      self.selectMostRecentEligibleSeries()

  def selectMostRecentEligibleSeries(self):
    substring = self.getSetting("NEEDLE_IMAGE_PATTERN")
    seriesTypeManager = SeriesTypeManager()
    self.intraopSeriesSelector.blockSignals(True)
    self.intraopSeriesSelector.setCurrentIndex(-1)
    self.intraopSeriesSelector.blockSignals(False)
    index = -1
    if not self.session.data.getMostRecentApprovedCoverProstateRegistration():
      substring = self.getSetting("COVER_TEMPLATE_PATTERN") \
        if not self.session.zFrameRegistrationSuccessful else self.getSetting("COVER_PROSTATE_PATTERN")
    for item in list(reversed(range(len(self.session.seriesList)))):
      series = self._seriesModel.item(item).text()
      if substring in seriesTypeManager.getSeriesType(series):
        if index != -1:
          if self.session.data.registrationResultWasApprovedOrRejected(series) or \
            self.session.data.registrationResultWasSkipped(series):
            break
        index = self.intraopSeriesSelector.findText(series)
        break
      elif self.session.seriesTypeManager.isVibe(series) and index == -1:
        index = self.intraopSeriesSelector.findText(series)
    rowCount = self.intraopSeriesSelector.model().rowCount()

    self.intraopSeriesSelector.setCurrentIndex(index if index != -1 else (rowCount-1 if rowCount else -1))

  def configureRedSliceNodeForPreopData(self):
    if not self.session.data.initialVolume or not self.session.data.initialTargets:
      return
    self.layoutManager.setLayout(constants.LAYOUT_RED_SLICE_ONLY)
    self.setBackgroundToVolumeID(self.session.data.initialVolume, clearLabels=False)
    self.redCompositeNode.SetLabelOpacity(1)
    self.targetTablePlugin.currentTargets = self.session.data.initialTargets
    # self.logic.centerViewsToProstate()

  def promptUserAndApplyBiasCorrectionIfNeeded(self):
    if self.session.data.resumed or self.session.data.completed:
      return

    autoPreopSeg = self.session.data.usedAutomaticPreopSegmentation
    usedERC = (autoPreopSeg and self.session.data.preopData.usedERC is True) or \
              (not autoPreopSeg and slicer.util.confirmYesNoDisplay("Was an endorectal coil used for preop image "
                                                                    "acquisition?", windowTitle="SliceTracker"))
    if usedERC:
      customProgressbar = CustomStatusProgressbar()
      customProgressbar.busy = True
      currentModule = slicer.util.getModuleGui(self.MODULE_NAME)
      if currentModule:
        currentModule.parent().enabled = False
      try:
        customProgressbar.updateStatus("Bias correction running!")
        slicer.app.processEvents()
        self.logic.applyBiasCorrection()
      except AttributeError:
        pass
      finally:
        customProgressbar.busy = False
        if currentModule:
          currentModule.parent().enabled = True
      customProgressbar.updateStatus("Bias correction done!")

    self.session.data.preopData.usedERC = usedERC
