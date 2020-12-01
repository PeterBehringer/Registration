import vtk

from .base import SliceTrackerSegmentationPluginBase
from ....algorithms.automaticProstateSegmentation import AutomaticSegmentationLogic

class SliceTrackerAutomaticSegmentationPlugin(SliceTrackerSegmentationPluginBase):

  NAME = "AutomaticSegmentation"
  ALGORITHM_TYPE ="Automatic"
  LogicClass = AutomaticSegmentationLogic

  def __init__(self):
    super(SliceTrackerAutomaticSegmentationPlugin, self).__init__()
    self.logic.addEventObserver(self.logic.DeepLearningStartedEvent, self._onSegmentationStarted)
    self.logic.addEventObserver(self.logic.DeepLearningFinishedEvent, self._onSegmentationFinished)
    # self.logic.addEventObserver(self.logic.DeepLearningStatusChangedEvent, self.onStatusChanged)
    self.logic.addEventObserver(self.logic.DeepLearningFailedEvent, self._onSegmentationFailed)

  def cleanup(self):
    super(SliceTrackerAutomaticSegmentationPlugin, self).cleanup()
    self.logic.cleanup()

  def setup(self):
    super(SliceTrackerAutomaticSegmentationPlugin, self).setup()

  def onActivation(self):
    if str(self.getSetting("Use_Deep_Learning")).lower() == 'true':
      self.startSegmentation()

  def startSegmentation(self):
    self.logic.run(self.session.fixedVolume, domain='BWH_WITHOUT_ERC', colorNode=self.session.mpReviewColorNode)

  @vtk.calldata_type(vtk.VTK_OBJECT)
  def _onSegmentationFinished(self, caller, event, labelNode):
    # self.onStatusChanged(None, None, str({'text': "Labelmap prediction created", 'value': 100}))
    super(SliceTrackerAutomaticSegmentationPlugin, self)._onSegmentationFinished(caller, event, labelNode)

  # @vtk.calldata_type(vtk.VTK_STRING)
  # def onStatusChanged(self, caller, event, callData):
  #   from SlicerDevelopmentToolboxUtils.widgets import CustomStatusProgressbar
  #   statusBar = CustomStatusProgressbar()
  #   if not statusBar.visible:
  #     statusBar.show()
  #   import ast
  #   status = ast.literal_eval(str(callData))
  #   self.updateProgressBar(progress=statusBar, text=status["text"].replace("\n", ""), value=status["value"],
  #                          maximum = 100)
