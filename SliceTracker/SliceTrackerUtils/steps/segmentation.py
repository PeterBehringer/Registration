import ast
import qt
import slicer
import vtk

from .base import SliceTrackerLogicBase, SliceTrackerStep
from ..constants import SliceTrackerConstants as constants
from ..sessionData import SegmentationData

from .plugins.targeting import SliceTrackerTargetingPlugin
from .plugins.segmentation.manual import SliceTrackerManualSegmentationPlugin
from .plugins.segmentation.automatic import SliceTrackerAutomaticSegmentationPlugin

from SlicerDevelopmentToolboxUtils.helpers import SliceAnnotation
from SlicerDevelopmentToolboxUtils.decorators import onModuleSelected
from SlicerDevelopmentToolboxUtils.icons import Icons


class SliceTrackerSegmentationStepLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerSegmentationStepLogic, self).__init__()

  def inputsAreSet(self):
    if self.session.data.usePreopData:
      return self.session.movingVolume and self.session.fixedVolume and self.session.movingLabel and \
             self.session.fixedLabel and self.session.movingTargets
    else:
      return self.session.fixedVolume and self.session.fixedLabel and self.session.movingTargets

  def loadInitialData(self):
    self.session.movingLabel = self.session.data.initialLabel
    self.session.movingVolume = self.session.data.initialVolume
    self.session.movingTargets = self.session.data.initialTargets


class SliceTrackerSegmentationStep(SliceTrackerStep):

  NAME = "Segmentation"
  LogicClass = SliceTrackerSegmentationStepLogic
  LayoutClass = qt.QVBoxLayout

  def __init__(self):
    super(SliceTrackerSegmentationStep, self).__init__()
    self.session.retryMode = False
    self.segmentationData = None

  def setup(self):
    super(SliceTrackerSegmentationStep, self).setup()
    self._setupTargetingPlugin()
    self._setupManualSegmentationPlugin()
    self._setupAutomaticSegmentationPlugin()
    self._setupNavigationButtons()
    self.layout().addWidget(self.manualSegmentationPlugin)
    self.layout().addWidget(self.createHLayout([self.backButton, self.finishStepButton]))
    self.layout().addWidget(self.targetingPlugin)
    self.layout().addStretch(1)

  def _setupTargetingPlugin(self):
    self.targetingPlugin = SliceTrackerTargetingPlugin()
    self.targetingPlugin.addEventObserver(self.targetingPlugin.TargetingStartedEvent, self._onTargetingStarted)
    self.targetingPlugin.addEventObserver(self.targetingPlugin.TargetingFinishedEvent, self._onTargetingFinished)
    self.addPlugin(self.targetingPlugin)

  def _setupManualSegmentationPlugin(self):
    self.manualSegmentationPlugin = SliceTrackerManualSegmentationPlugin()
    self.manualSegmentationPlugin.addEventObserver(self.manualSegmentationPlugin.SegmentationStartedEvent,
                                                   self._onSegmentationStarted)
    self.manualSegmentationPlugin.addEventObserver(self.manualSegmentationPlugin.SegmentationCanceledEvent,
                                                   self._onSegmentationCanceled)
    self.manualSegmentationPlugin.addEventObserver(self.manualSegmentationPlugin.SegmentationFinishedEvent,
                                                   self._onManualSegmentationFinished)
    self.manualSegmentationPlugin.surfaceCutToLabelWidget.segmentEditorButtonVisible = False
    self.addPlugin(self.manualSegmentationPlugin)

  def _setupAutomaticSegmentationPlugin(self):
    self.automaticSegmentationPlugin = SliceTrackerAutomaticSegmentationPlugin()
    self.automaticSegmentationPlugin.addEventObserver(self.automaticSegmentationPlugin.SegmentationStartedEvent,
                                                      self._onAutomaticSegmentationStarted)
    self.automaticSegmentationPlugin.addEventObserver(self.automaticSegmentationPlugin.SegmentationFailedEvent,
                                                   self._onSegmentationFailed)
    self.automaticSegmentationPlugin.addEventObserver(self.automaticSegmentationPlugin.SegmentationFinishedEvent,
                                                      self._onAutomaticSegmentationFinished)
    self.addPlugin(self.automaticSegmentationPlugin)

  def _setupNavigationButtons(self):
    iconSize = qt.QSize(36, 36)
    self.backButton = self.createButton("", icon=Icons.back, iconSize=iconSize,
                                        toolTip="Return to last step")
    self.finishStepButton = self.createButton("", icon=Icons.start, iconSize=iconSize,
                                              toolTip="Run Registration/Finish Step")

  def setupConnections(self):
    super(SliceTrackerSegmentationStep, self).setupConnections()
    self.backButton.clicked.connect(self._onBackButtonClicked)
    self.finishStepButton.clicked.connect(self._onFinishStepButtonClicked)

  def addSessionObservers(self):
    super(SliceTrackerSegmentationStep, self).addSessionObservers()
    self.session.addEventObserver(self.session.InitiateSegmentationEvent, self.onInitiateSegmentation)

  def removeSessionEventObservers(self):
    super(SliceTrackerSegmentationStep, self).removeSessionEventObservers()
    self.session.removeEventObserver(self.session.InitiateSegmentationEvent, self.onInitiateSegmentation)

  def onActivation(self):
    self.segmentationData = None
    self.finishStepButton.enabled = False
    self.session.fixedVolume = self.session.currentSeriesVolume
    if not self.session.fixedVolume:
      return
    self._updateAvailableLayouts()
    super(SliceTrackerSegmentationStep, self).onActivation()

  def onDeactivation(self):
    super(SliceTrackerSegmentationStep, self).onDeactivation()
    self._removeMissingPreopDataAnnotation()

  @onModuleSelected(SliceTrackerStep.MODULE_NAME)
  def onLayoutChanged(self, layout=None):
    if self.layoutManager.layout == constants.LAYOUT_SIDE_BY_SIDE:
      self._setupSideBySideSegmentationView()
    elif self.layoutManager.layout in [constants.LAYOUT_FOUR_UP, constants.LAYOUT_RED_SLICE_ONLY]:
      self._removeMissingPreopDataAnnotation()
      self.setBackgroundToVolumeID(self.session.currentSeriesVolume, clearLabels=False)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onInitiateSegmentation(self, caller, event, callData):
    self._initiateSegmentation(ast.literal_eval(callData))

  def _initiateSegmentation(self, retryMode=False):
    self.session.retryMode = retryMode
    self.finishStepButton.setEnabled(True if self.logic.inputsAreSet() else False)
    if self.session.seriesTypeManager.isCoverProstate(self.session.currentSeries):
      self.initializeCoverProstate()
    else:
      self._loadLatestCoverProstateResultData()
    self.active = True

  def initializeCoverProstate(self):
    if self.session.data.usePreopData:
      if self.session.retryMode:
        if not self._loadLatestCoverProstateResultData():
          self.logic.loadInitialData()
      else:
        self.logic.loadInitialData()
    else:
      self.session.movingVolume = self.session.currentSeriesVolume

  def _loadLatestCoverProstateResultData(self):
    coverProstate = self.session.data.getMostRecentApprovedCoverProstateRegistration()
    if coverProstate:
      self.session.movingVolume = coverProstate.volumes.fixed
      self.session.movingLabel = coverProstate.labels.fixed
      self.session.movingTargets = coverProstate.targets.approved
      return True
    return False

  def _updateAvailableLayouts(self):
    layouts = [constants.LAYOUT_RED_SLICE_ONLY, constants.LAYOUT_FOUR_UP]
    if self.session.data.usePreopData or self.session.retryMode:
      layouts.append(constants.LAYOUT_SIDE_BY_SIDE)
    self.setAvailableLayouts(layouts)

  def _setupSideBySideSegmentationView(self):
    # TODO: red slice view should not be possible to set target
    coverProstate = self.session.data.getMostRecentApprovedCoverProstateRegistration()
    redVolume = coverProstate.volumes.fixed if coverProstate and self.session.retryMode else self.session.data.initialVolume
    redLabel = coverProstate.labels.fixed if coverProstate and self.session.retryMode else self.session.data.initialLabel

    if redVolume and redLabel:
      self.redCompositeNode.SetBackgroundVolumeID(redVolume.GetID())
      self.redCompositeNode.SetLabelVolumeID(redLabel.GetID())
      self.redCompositeNode.SetLabelOpacity(1)
    else:
      self.redCompositeNode.SetBackgroundVolumeID(None)
      self.redCompositeNode.SetLabelVolumeID(None)
      self._addMissingPreopDataAnnotation(self.redWidget)
    self.yellowCompositeNode.SetBackgroundVolumeID(self.session.currentSeriesVolume.GetID())
    self.setAxialOrientation()

    if redVolume and redLabel:
      self.redSliceNode.SetUseLabelOutline(True)
      self.redSliceNode.RotateToVolumePlane(redVolume)

  def _addMissingPreopDataAnnotation(self, widget):
    self._removeMissingPreopDataAnnotation()
    self.noPreopSegmentationAnnotation = SliceAnnotation(widget, constants.MISSING_PREOP_ANNOTATION_TEXT,
                                                         opacity=0.7, color=(1, 0, 0))

  def _removeMissingPreopDataAnnotation(self):
    self.noPreopSegmentationAnnotation = getattr(self, "noPreopSegmentationAnnotation", None)
    if self.noPreopSegmentationAnnotation:
      self.noPreopSegmentationAnnotation.remove()
      self.noPreopSegmentationAnnotation = None

  def _onBackButtonClicked(self):
    if self.session.retryMode:
      self.session.retryMode = False
    if self.session.previousStep:
      self.session.previousStep.active = True

  def _onFinishStepButtonClicked(self):
    self.session.data.segmentModelNode = self.manualSegmentationPlugin.segmentModelNode
    self.session.data.inputMarkupNode = self.manualSegmentationPlugin.inputMarkupNode
    if not self.session.data.usePreopData and not self.session.retryMode:
      self._createCoverProstateRegistrationResultManually()
    else:
      self.session.onInvokeRegistration(initial=True, retryMode=self.session.retryMode,
                                        segmentationData=self.segmentationData)

  def _createCoverProstateRegistrationResultManually(self):
    fixedVolume = self.session.currentSeriesVolume
    result = self.session.generateNameAndCreateRegistrationResult(fixedVolume)
    approvedRegistrationType = "bSpline"
    result.targets.original = self.session.movingTargets
    targetName = str(result.seriesNumber) + '-TARGETS-' + approvedRegistrationType + result.suffix
    clone = self.logic.cloneFiducials(self.session.movingTargets, targetName)
    self.session.applyDefaultTargetDisplayNode(clone)
    result.setTargets(approvedRegistrationType, clone)
    result.volumes.fixed = fixedVolume
    result.labels.fixed = self.session.fixedLabel
    result.receivedTime = self.session.seriesTimeStamps[result.name.replace(result.suffix, "")]
    result.segmentationData = self.segmentationData

    if self.session.seriesTypeManager.isCoverProstate(self.session.currentResult.name) and \
    self.session.data.getMostRecentApprovedCoverProstateRegistration() is not None:
      self.session.data.getMostRecentApprovedCoverProstateRegistration().skip()

    result.approve(approvedRegistrationType, consentedBy="Clinician")

  def _onAutomaticSegmentationStarted(self, caller, event):
    self.manualSegmentationPlugin.enabled = False
    self._onSegmentationStarted(caller, event)

  def _onSegmentationStarted(self, caller, event):
    # self.setAvailableLayouts([constants.LAYOUT_RED_SLICE_ONLY, constants.LAYOUT_SIDE_BY_SIDE, constants.LAYOUT_FOUR_UP])
    self.targetingPlugin.enabled = False
    self.backButton.enabled = False
    self.finishStepButton.enabled = False

  def _onSegmentationCanceled(self, caller, event):
    # self.setAvailableLayouts([constants.LAYOUT_FOUR_UP])
    self.layoutManager.setLayout(constants.LAYOUT_FOUR_UP)
    self.backButton.enabled = True
    self.targetingPlugin.enabled = True
    if self.logic.inputsAreSet():
      self._displaySegmentationComparison()
    self.finishStepButton.setEnabled(1 if self.logic.inputsAreSet() else 0) # TODO: need to revise that

  def _onSegmentationFailed(self, caller, event):
    import logging
    logging.debug("Segmentation failed")
    self.setAvailableLayouts([constants.LAYOUT_FOUR_UP])
    self.layoutManager.setLayout(constants.LAYOUT_FOUR_UP)
    self.backButton.enabled = True
    self.finishStepButton.setEnabled(False)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onNewImageSeriesReceived(self, caller, event, callData):
    if not self.active:
      return
    newImageSeries = ast.literal_eval(callData)
    for series in reversed(newImageSeries):
      if self.session.seriesTypeManager.isCoverProstate(series):
        if series != self.session.currentSeries:
          if slicer.util.confirmYesNoDisplay("Another %s was received. Do you want to use this one?"
                                              % self.getSetting("COVER_PROSTATE_PATTERN")):
            self.session.currentSeries = series
            self.active = False
            self._initiateSegmentation()

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def _onAutomaticSegmentationFinished(self, caller, event, labelNode):
    self.manualSegmentationPlugin.enabled = True
    surfaceCutToLabelWidget = self.manualSegmentationPlugin.surfaceCutToLabelWidget

    # segmentationsLogic = slicer.modules.segmentations.logic()
    #
    # segmentationNode = surfaceCutToLabelWidget.segmentationNode
    # map(lambda x: segmentationNode.RemoveSegment(x), surfaceCutToLabelWidget.getSegmentIDs())
    # segmentationsLogic.ImportLabelmapToSegmentationNode(labelNode, segmentationNode)
    # surfaceCutToLabelWidget.configureSegmentVisibility()
    # surfaceCutToLabelWidget.segmentEditorWidgetButton.enabled = True

    surfaceCutToLabelWidget.imageVolume = self.session.currentSeriesVolume
    surfaceCutToLabelWidget.labelVolume = labelNode

    self.createSegmentationDataOrSetModified(self.automaticSegmentationPlugin, labelNode)
    self._onSegmentationFinished(caller, event, labelNode)

  def createSegmentationDataOrSetModified(self, plugin, labelNode):
    if not self.segmentationData or self.segmentationData.algorithm=="Manual":
      self.segmentationData = SegmentationData(segmentationType="Prostate",algorithm=plugin.ALGORITHM_TYPE,
                                               startTime=plugin.startTime, endTime=plugin.endTime, label=labelNode)
    else:
      self.segmentationData.setModified(startTime=plugin.startTime, endTime=plugin.endTime, label=labelNode)

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def _onManualSegmentationFinished(self, caller, event, labelNode):
    self.createSegmentationDataOrSetModified(self.manualSegmentationPlugin, labelNode)
    self._onSegmentationFinished(caller, event, labelNode)

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def _onSegmentationFinished(self, caller, event, labelNode):
    _, suffix = self.session.getRegistrationResultNameAndGeneratedSuffix(self.session.currentSeries)
    if labelNode.GetName().find(suffix) == -1 and not labelNode.GetName().endswith("_modified"):
      labelNode.SetName(labelNode.GetName() + suffix)
    self.session.fixedLabel = labelNode
    self.finishStepButton.setEnabled(1 if self.logic.inputsAreSet() else 0)
    self.backButton.enabled = True
    self._displaySegmentationComparison()

  def _displaySegmentationComparison(self):
    self.setAvailableLayouts([constants.LAYOUT_SIDE_BY_SIDE])
    if self.session.data.usePreopData or self.session.retryMode:
      self.setAxialOrientation()
    self._removeMissingPreopDataAnnotation()
    self.targetingPlugin.enabled = True
    if self.session.data.usePreopData or self.session.retryMode:
      self.layoutManager.setLayout(constants.LAYOUT_SIDE_BY_SIDE)
      self._setBackgroundAndLabel("red", self.session.movingVolume, self.session.movingLabel)
      self._setBackgroundAndLabel("yellow", self.session.fixedVolume, self.session.fixedLabel)
      self._centerLabelsOnVisibleSliceWidgets()
    elif not self.session.movingTargets:
      self.targetingPlugin.startTargeting()
    else:
      for compositeNode, sliceNode in zip(self._compositeNodes, self._sliceNodes):
        compositeNode.SetLabelVolumeID(self.session.fixedLabel.GetID())
        compositeNode.SetLabelOpacity(1)
        sliceNode.SetUseLabelOutline(True)

  def _setBackgroundAndLabel(self, viewName, volume, label):
    compositeNode = getattr(self, viewName+"CompositeNode")
    compositeNode.SetReferenceBackgroundVolumeID(volume.GetID())
    compositeNode.SetLabelVolumeID(label.GetID())
    compositeNode.SetLabelOpacity(1)
    sliceNode = getattr(self, viewName+"SliceNode")
    sliceNode.SetOrientationToAxial()
    sliceNode.RotateToVolumePlane(volume)
    sliceNode.SetUseLabelOutline(True)

  def _centerLabelsOnVisibleSliceWidgets(self):
    for widget in self.getAllVisibleWidgets():
      compositeNode = widget.mrmlSliceCompositeNode()
      sliceNode = widget.sliceLogic().GetSliceNode()
      labelID = compositeNode.GetLabelVolumeID()
      if labelID:
        label = slicer.mrmlScene.GetNodeByID(labelID)
        centroid = self.logic.getCentroidForLabel(label, self.session.segmentedLabelValue)
        if centroid:
          sliceNode.JumpSliceByCentering(centroid[0], centroid[1], centroid[2])

  def _onTargetingStarted(self, caller, event):
    self.manualSegmentationPlugin.enabled = False
    self.backButton.enabled = False

  def _onTargetingFinished(self, caller, event):
    self.finishStepButton.setEnabled(1 if self.logic.inputsAreSet() else 0)
    self.manualSegmentationPlugin.enabled = True
    self.backButton.enabled = True
