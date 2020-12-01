import qt
import vtk
import slicer

from ...base import SliceTrackerPlugin, SliceTrackerLogicBase
from ....constants import SliceTrackerConstants as constants
from .base import SliceTrackerSegmentationPluginBase
from SurfaceCutToLabel import SurfaceCutToLabelWidget

from SlicerDevelopmentToolboxUtils.decorators import onModuleSelected


class SliceTrackerManualSegmentationLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerManualSegmentationLogic, self).__init__()


class SliceTrackerManualSegmentationPlugin(SliceTrackerSegmentationPluginBase):

  NAME = "ManualSegmentation"
  ALGORITHM_TYPE ="Manual"
  LogicClass = SliceTrackerManualSegmentationLogic

  SegmentationCanceledEvent = SliceTrackerSegmentationPluginBase.SegmentationFailedEvent

  @property
  def segmentModelNode(self):
    return self.surfaceCutToLabelWidget.logic.segmentModelNode

  @property
  def inputMarkupNode(self):
    return self.surfaceCutToLabelWidget.logic.inputMarkupNode

  def __init__(self):
    super(SliceTrackerManualSegmentationPlugin, self).__init__()

  def setup(self):
    super(SliceTrackerManualSegmentationPlugin, self).setup()

    self.surfaceCutGroupBox = qt.QWidget()
    self.surfaceCutGroupBox.setLayout(qt.QVBoxLayout())
    self.surfaceCutToLabelWidget = SurfaceCutToLabelWidget(self.surfaceCutGroupBox)
    self.surfaceCutToLabelWidget.setup()
    self.surfaceCutToLabelWidget.selectorsGroupBoxVisible = False
    self.surfaceCutToLabelWidget.colorGroupBoxVisible = False

    if str(self.getSetting('DeveloperMode', 'Developer')).lower() == 'true':
      self.surfaceCutToLabelWidget.reloadCollapsibleButton.hide()

    self.segmentationGroupBox = qt.QGroupBox("SurfaceCut Segmentation")
    self.segmentationGroupBoxLayout = qt.QGridLayout()
    self.segmentationGroupBox.setLayout(self.segmentationGroupBoxLayout)
    self.segmentationGroupBoxLayout.addWidget(self.surfaceCutGroupBox, 0, 0)
    self.layout().addWidget(self.segmentationGroupBox)

  @onModuleSelected(SliceTrackerPlugin.MODULE_NAME)
  def onLayoutChanged(self, layout=None):
    self._refreshSegmentModelViewNodes()

  def _refreshSegmentModelViewNodes(self):
    sliceNodes = [self.yellowSliceNode] if self.layoutManager.layout == constants.LAYOUT_SIDE_BY_SIDE else \
      [self.redSliceNode, self.yellowSliceNode, self.greenSliceNode]
    nodes = [self.surfaceCutToLabelWidget.logic.segmentModelNode, self.surfaceCutToLabelWidget.logic.inputMarkupNode]
    for node in [n for n in nodes if n]:
      self.refreshViewNodeIDs(node, sliceNodes)

  def onActivation(self):
    super(SliceTrackerManualSegmentationPlugin, self).onActivation()
    self.surfaceCutToLabelWidget.logic.colorNode = self.session.mpReviewColorNode
    self.surfaceCutToLabelWidget.colorSpin.setValue(self.session.segmentedLabelValue)
    self.surfaceCutToLabelWidget.imageVolumeSelector.setCurrentNode(self.session.fixedVolume)
    self._addSurfaceCutEventObservers()
    if str(self.getSetting("Use_Deep_Learning")).lower() == 'false':
      self.surfaceCutToLabelWidget.quickSegmentationButton.checked = True

  def onDeactivation(self):
    super(SliceTrackerManualSegmentationPlugin, self).onDeactivation()
    self._removeSurfaceCutEventObservers()

  def _addSurfaceCutEventObservers(self):
    self.surfaceCutToLabelWidget.addEventObserver(self.surfaceCutToLabelWidget.SegmentationStartedEvent,
                                                  self._onSegmentationStarted)
    self.surfaceCutToLabelWidget.addEventObserver(self.surfaceCutToLabelWidget.SegmentationCanceledEvent,
                                                  self._onSegmentationFailed)
    self.surfaceCutToLabelWidget.addEventObserver(self.surfaceCutToLabelWidget.SegmentationFinishedEvent,
                                                  self._onSegmentationFinished)

  def _removeSurfaceCutEventObservers(self):
    self.surfaceCutToLabelWidget.removeEventObserver(self.surfaceCutToLabelWidget.SegmentationStartedEvent,
                                                     self._onSegmentationStarted)
    self.surfaceCutToLabelWidget.removeEventObserver(self.surfaceCutToLabelWidget.SegmentationCanceledEvent,
                                                     self._onSegmentationFailed)
    self.surfaceCutToLabelWidget.removeEventObserver(self.surfaceCutToLabelWidget.SegmentationFinishedEvent,
                                                     self._onSegmentationFinished)

  def _onSegmentationStarted(self, caller, event):
    if str(self.getSetting("Use_Deep_Learning")).lower() == 'true':
      if not self._preCheckExistingSegmentation():
        return
      else:
        labelVolume = self.surfaceCutToLabelWidget.labelVolume
        if labelVolume and not labelVolume.GetName().endswith("_modified"):
          clonedLabelNode = self.logic.volumesLogic.CloneVolume(slicer.mrmlScene, labelVolume,
                                                                labelVolume.GetName()+"_modified")
          self.surfaceCutToLabelWidget.labelVolume = clonedLabelNode
    self.setupFourUpView(self.session.fixedVolume)
    self.setDefaultOrientation()
    super(SliceTrackerManualSegmentationPlugin, self)._onSegmentationStarted(caller, event)

  def _preCheckExistingSegmentation(self):
    if slicer.util.confirmYesNoDisplay("The automatic segmentation will be overwritten. Do you want to proceed?",
                                       windowTitle="SliceTracker"):
      return True
    self.surfaceCutToLabelWidget.deactivateQuickSegmentationMode(cancelled=True)
    return False

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def _onSegmentationFinished(self, caller, event, labelNode):
    displayNode = labelNode.GetDisplayNode()
    displayNode.SetAndObserveColorNodeID(self.session.mpReviewColorNode.GetID())
    self.surfaceCutToLabelWidget.colorSpin.setValue(self.session.segmentedLabelValue)
    super(SliceTrackerManualSegmentationPlugin, self)._onSegmentationFinished(caller, event, labelNode)
