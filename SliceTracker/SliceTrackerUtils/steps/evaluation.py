import os
import qt
import vtk
import slicer
from base import SliceTrackerLogicBase, SliceTrackerStep
from plugins.results import SliceTrackerRegistrationResultsPlugin
from plugins.targets import SliceTrackerTargetTablePlugin
from plugins.arduino import SliceTrackerArduinoPlugin

from SlicerDevelopmentToolboxUtils.icons import Icons


class SliceTrackerEvaluationStepLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerEvaluationStepLogic, self).__init__()


class SliceTrackerEvaluationStep(SliceTrackerStep):

  NAME = "Evaluation"
  LogicClass = SliceTrackerEvaluationStepLogic

  def __init__(self):
    self.modulePath = os.path.dirname(slicer.util.modulePath(self.MODULE_NAME)).replace(".py", "")
    super(SliceTrackerEvaluationStep, self).__init__()

  def setup(self):
    super(SliceTrackerEvaluationStep, self).setup()
    self.registrationEvaluationGroupBox = qt.QGroupBox()
    self.registrationEvaluationGroupBoxLayout = qt.QGridLayout()
    self.registrationEvaluationGroupBox.setLayout(self.registrationEvaluationGroupBoxLayout)
    self.setupRegistrationValidationButtons()

    self.arduinoPlugin = SliceTrackerArduinoPlugin()
    self.addPlugin(self.arduinoPlugin)

    self.regResultsPlugin = SliceTrackerRegistrationResultsPlugin()
    self.addPlugin(self.regResultsPlugin)
    self.regResultsPlugin.addEventObserver(self.regResultsPlugin.RegistrationTypeSelectedEvent,
                                            self.onRegistrationTypeSelected)
    self.regResultsPlugin.addEventObserver(self.regResultsPlugin.NoRegistrationResultsAvailable,
                                            self.onNoRegistrationResultsAvailable)
    self.regResultsPlugin.addEventObserver(self.regResultsPlugin.RegistrationResultsAvailable,
                                            self.onRegistrationResultsAvailable)

    self.targetTablePlugin = SliceTrackerTargetTablePlugin(movingEnabled=True)
    self.addPlugin(self.targetTablePlugin)

    self.registrationEvaluationGroupBoxLayout.addWidget(self.regResultsPlugin, 3, 0)
    self.registrationEvaluationGroupBoxLayout.addWidget(self.targetTablePlugin, 4, 0)
    self.registrationEvaluationGroupBoxLayout.addWidget(self.registrationEvaluationButtonsGroupBox, 5, 0)
    self.registrationEvaluationGroupBoxLayout.setRowStretch(6, 1)
    self.layout().addWidget(self.registrationEvaluationGroupBox)

  def setupRegistrationValidationButtons(self):
    iconSize = qt.QSize(36, 36)
    self.approveRegistrationResultButton = self.createButton("", icon=Icons.thumbs_up, iconSize=iconSize,
                                                             toolTip="Approve")
    self.retryRegistrationButton = self.createButton("", icon=Icons.retry, iconSize=iconSize, toolTip="Retry")
    self.rejectRegistrationResultButton = self.createButton("", icon=Icons.thumbs_down, iconSize=iconSize,
                                                            toolTip="Reject")
    self.registrationEvaluationButtonsGroupBox = self.createHLayout([self.retryRegistrationButton,
                                                                     self.approveRegistrationResultButton,
                                                                     self.rejectRegistrationResultButton])

  def setupConnections(self):
    self.retryRegistrationButton.clicked.connect(self.onRetryRegistrationButtonClicked)
    self.approveRegistrationResultButton.clicked.connect(self.onApproveRegistrationResultButtonClicked)
    self.rejectRegistrationResultButton.clicked.connect(self.onRejectRegistrationResultButtonClicked)
    # self.registrationDetailsButton.clicked.connect(self.onShowRegistrationDetails)

  def addSessionObservers(self):
    super(SliceTrackerEvaluationStep, self).addSessionObservers()
    self.session.addEventObserver(self.session.InitiateEvaluationEvent, self.onInitiateEvaluation)

  def removeSessionEventObservers(self):
    super(SliceTrackerEvaluationStep, self).removeSessionEventObservers()
    self.session.removeEventObserver(self.session.InitiateEvaluationEvent, self.onInitiateEvaluation)

  def onRetryRegistrationButtonClicked(self):
    self.session.retryRegistration()

  def onApproveRegistrationResultButtonClicked(self):
    results = self.session.data.getResultsBySeriesNumber(self.currentResult.seriesNumber)
    for result in [r for r in results if r is not self.currentResult]:
      result.reject()
    self.currentResult.approve(registrationType=self.regResultsPlugin.registrationButtonGroup.checkedButton().name)
    # if self.ratingWindow.isRatingEnabled():
    #   self.ratingWindow.show(disableWidget=self.parent)

  def onRejectRegistrationResultButtonClicked(self):
    results = self.session.data.getResultsBySeriesNumber(self.currentResult.seriesNumber)
    for result in [r for r in results if r is not self.currentResult]:
      result.reject()
    self.currentResult.reject()

  def onInitiateEvaluation(self, caller, event):
    self.active = True

  def onActivation(self):
    super(SliceTrackerEvaluationStep, self).onActivation()
    if not self.currentResult:
      return
    # self.redOnlyLayoutButton.enabled = False
    # self.sideBySideLayoutButton.enabled = True
    self.rejectRegistrationResultButton.enabled = not self.session.seriesTypeManager.isCoverProstate(self.currentResult.name)
    self.currentResult.save(self.session.outputDirectory)
    self.currentResult.printSummary()

  def onDeactivation(self):
    super(SliceTrackerEvaluationStep, self).onDeactivation()
    self.hideAllLabels()
    self.hideAllFiducialNodes()

  @vtk.calldata_type(vtk.VTK_STRING)
  def onRegistrationTypeSelected(self, caller, event, callData):
    self.targetTablePlugin.currentTargets = getattr(self.currentResult.targets, callData)

  def onNoRegistrationResultsAvailable(self, caller, event):
    self.targetTablePlugin.currentTargets = None
    self.approveRegistrationResultButton.enabled = False

  def onRegistrationResultsAvailable(self, caller, event):
    self.approveRegistrationResultButton.enabled = True
    self.targetTablePlugin.enabled = True

