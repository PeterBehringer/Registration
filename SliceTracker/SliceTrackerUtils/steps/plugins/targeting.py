import qt
from ...constants import SliceTrackerConstants as constants
from ..base import SliceTrackerPlugin, SliceTrackerLogicBase
from .targets import SliceTrackerTargetTablePlugin

from SlicerDevelopmentToolboxUtils.helpers import SliceAnnotation
from SlicerDevelopmentToolboxUtils.widgets import TargetCreationWidget


class SliceTrackerTargetingLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerTargetingLogic, self).__init__()


class SliceTrackerTargetingPlugin(SliceTrackerPlugin):

  LogicClass = SliceTrackerTargetingLogic

  NAME = "Targeting"
  TargetingStartedEvent = TargetCreationWidget.StartedEvent
  TargetingFinishedEvent = TargetCreationWidget.FinishedEvent

  @property
  def title(self):
    return self.targetingGroupBox.title

  @title.setter
  def title(self, value):
    self.targetingGroupBox.setTitle(value)

  def __init__(self, **kwargs):
    super(SliceTrackerTargetingPlugin, self).__init__()
    self._processKwargs(**kwargs)

  def setup(self):
    super(SliceTrackerTargetingPlugin, self).setup()

    self._setupTargetCreationWidget()

    self.preopTargetTableGroupBox, self.preopTargetTablePlugin = \
      self._createTargetTableGroupBox("Pre-operative Targets")
    self.intraopTargetTableGroupBox, self.intraopTargetTablePlugin = \
      self._createTargetTableGroupBox("Intra-operative Targets",
                                      additionalComponents=[self.targetCreationWidget,
                                                            self.targetCreationWidget.buttons])

    self.targetingGroupBox = qt.QGroupBox("Target Placement")
    self.targetingGroupBox.setLayout(qt.QFormLayout())
    self.targetingGroupBox.layout().addRow(self.preopTargetTableGroupBox)
    self.targetingGroupBox.layout().addRow(self.intraopTargetTableGroupBox)
    self.layout().addWidget(self.targetingGroupBox, 1, 0, 2, 2)

  def _createTargetTableGroupBox(self, title, additionalComponents=None):
    additionalComponents = additionalComponents if additionalComponents else []
    groupbox = qt.QGroupBox(title)
    groupbox.setAlignment(qt.Qt.AlignCenter)
    groupbox.setLayout(qt.QFormLayout())
    groupbox.setAlignment(qt.Qt.AlignCenter)
    targetTablePlugin = SliceTrackerTargetTablePlugin()
    self.addPlugin(targetTablePlugin)
    groupbox.layout().addRow(targetTablePlugin)
    for c in additionalComponents:
      groupbox.layout().addRow(c)
    return groupbox, targetTablePlugin

  def _setupTargetCreationWidget(self):
    self.targetCreationWidget = TargetCreationWidget(DEFAULT_FIDUCIAL_LIST_NAME="IntraopTargets",
                                                     ICON_SIZE=qt.QSize(36, 36))
    self.targetCreationWidget.addEventObserver(self.TargetingStartedEvent, self._onTargetingStarted)
    self.targetCreationWidget.addEventObserver(self.TargetingFinishedEvent, self._onTargetingFinished)

  def preopAvailableAndTargetsDefined(self):
    return self.session.data.usePreopData and self.session.movingTargets and not self.session.retryMode

  def onActivation(self):
    super(SliceTrackerTargetingPlugin, self).onActivation()
    self._setFiducialWidgetVisible(True)

    if self.preopAvailableAndTargetsDefined():
      self._setFiducialWidgetVisible(False)
      self.targetCreationWidget.currentNode = None
      self.preopTargetTablePlugin.currentTargets = self.session.movingTargets
    else:
      approvedCoverProstate = self.session.data.getMostRecentApprovedCoverProstateRegistration()
      if (approvedCoverProstate is not None
          and self.session.seriesTypeManager.isCoverProstate(self.session.currentSeries)):
        clone = self.logic.cloneFiducials(approvedCoverProstate.targets.approved, "IntraopTargets")
        self.targetCreationWidget.currentNode = clone
        self.intraopTargetTablePlugin.currentTargets = self.session.movingTargets
        self.session.movingTargets = clone
        self.setFiducialNodeVisibility(clone, True)
        self.session.applyDefaultTargetDisplayNode(clone)
      self.preopTargetTableGroupBox.visible = False

    self.targetingGroupBox.visible = not self.session.retryMode

  def onDeactivation(self):
    super(SliceTrackerTargetingPlugin, self).onDeactivation()
    self.targetCreationWidget.reset()
    self._removeSliceAnnotations()

  def startTargeting(self):
    self.targetCreationWidget.startPlacing()

  def _setFiducialWidgetVisible(self, visible):
    self.targetCreationWidget.visible = visible
    self.preopTargetTableGroupBox.visible = not visible and self.preopAvailableAndTargetsDefined()
    self.intraopTargetTablePlugin.visible = not visible

  def _onTargetingStarted(self, caller, event):
    self._addSliceAnnotations()
    self.targetCreationWidget.show()
    self.intraopTargetTablePlugin.visible = False
    self.setupFourUpView(self.session.currentSeriesVolume, clearLabels=False)
    self.invokeEvent(self.TargetingStartedEvent)

  def _addSliceAnnotations(self):
    self._removeSliceAnnotations()
    widgets = [self.yellowWidget] if self.layoutManager.layout == constants.LAYOUT_SIDE_BY_SIDE else \
      [self.redWidget, self.yellowWidget, self.greenWidget]
    for widget in widgets:
      self.sliceAnnotations.append(SliceAnnotation(widget, "Targeting Mode", opacity=0.5,
                                                   verticalAlign="top", horizontalAlign="center"))

  def _removeSliceAnnotations(self):
    self.sliceAnnotations = getattr(self, "sliceAnnotations", [])
    for annotation in self.sliceAnnotations:
      annotation.remove()
    self.sliceAnnotations = []

  def _onTargetingFinished(self, caller, event):
    self._removeSliceAnnotations()
    if self.targetCreationWidget.hasTargetListAtLeastOneTarget():
      if not self.preopAvailableAndTargetsDefined():
        self.session.movingTargets = self.targetCreationWidget.currentNode
        self.session.setupPreopLoadedTargets()
      else:
        self.session.temporaryIntraopTargets = self.targetCreationWidget.currentNode
      self._setFiducialWidgetVisible(False)
      self.intraopTargetTablePlugin.currentTargets = self.targetCreationWidget.currentNode
    else:
      if not self.preopAvailableAndTargetsDefined():
        self.session.movingTargets = None

    self.invokeEvent(self.TargetingFinishedEvent)
