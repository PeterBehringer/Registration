import vtk
import qt
import ctk
import ast
import slicer
import logging

from ..base import SliceTrackerPlugin, SliceTrackerLogicBase, SliceTrackerStep
from ...session import SliceTrackerSession
from ...sessionData import RegistrationResult
from ...constants import SliceTrackerConstants as constants

from SlicerDevelopmentToolboxUtils.decorators import onModuleSelected


class SliceTrackerDisplacementChartLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerDisplacementChartLogic, self).__init__()
    self.session = SliceTrackerSession()

  def calculateTargetDisplacement(self, prevTargets, currTargets, targetIndex):
    prevPos = [0.0, 0.0, 0.0]
    currPos = [0.0, 0.0, 0.0]
    currTargets.GetNthFiducialPosition(targetIndex, currPos)
    prevTargets.GetNthFiducialPosition(targetIndex, prevPos)
    displacement = [currPos[0] - prevPos[0], currPos[1] - prevPos[1], currPos[2] - prevPos[2]]
    return displacement

  def isTargetDisplacementChartDisplayable(self, selectedSeries):
    if not selectedSeries or not (self.session.seriesTypeManager.isCoverProstate(selectedSeries) or
                                  self.session.seriesTypeManager.isGuidance(selectedSeries)) or \
                                  self.session.data.registrationResultWasRejected(selectedSeries):
      return False
    selectedSeriesNumber = RegistrationResult.getSeriesNumberFromString(selectedSeries)
    approvedResults = sorted([r for r in self.session.data.registrationResults.values() if r.approved],
                             key=lambda result: result.seriesNumber)
    nonSelectedApprovedResults = filter(lambda x: x.seriesNumber != selectedSeriesNumber, approvedResults)
    if len(list(nonSelectedApprovedResults)) == 0 or self.session.currentResult is None:
      return False
    return True


class SliceTrackerDisplacementChartPlugin(SliceTrackerPlugin):

  ShowEvent = vtk.vtkCommand.UserEvent + 561
  HideEvent = vtk.vtkCommand.UserEvent + 562

  NAME = "DisplacementChart"
  LogicClass = SliceTrackerDisplacementChartLogic

  PLOT_COLOR_LR = [213/255.0, 94/255.0, 0]
  PLOT_COLOR_PA = [0, 114/255.0, 178/255.0]
  PLOT_COLOR_IS = [204/255.0, 121/255.0, 167/255.0]
  PLOT_COLOR_3D = [0, 0, 0]
  PLOT_NAMES = ["L/R Displacement", "P/A Displacement", "I/S Displacement", "3-D Distance"]

  @property
  def plotWidgetViewNode(self):
    if not self._plotWidget:
      return None
    return self._plotWidget.mrmlPlotViewNode()

  def __init__(self):
    super(SliceTrackerDisplacementChartPlugin, self).__init__()

  @onModuleSelected(SliceTrackerStep.MODULE_NAME)
  def onMrmlSceneCleared(self, caller=None, event=None, callData=None):
    self.resetAndInitializeData()

  def resetChart(self):
    for arr in [self.arrX, self.arrXD, self.arrYD, self.arrZD, self.arrD]:
      arr.Initialize()

  def setup(self):
    super(SliceTrackerDisplacementChartPlugin, self).setup()

    self.collapsibleButton = ctk.ctkCollapsibleButton()
    self.collapsibleButton.text = "Plotting"
    self.collapsibleButton.collapsed = 0
    self.collapsibleButton.setLayout(qt.QGridLayout())

    self.resetAndInitializeData()

    self.showLegendCheckBox = qt.QCheckBox('Show legend')
    self.showLegendCheckBox.setChecked(1)

    self.collapsibleButton.layout().addWidget(self.showLegendCheckBox, 0, 0)
    self.layout().addWidget(self.collapsibleButton)

  def resetAndInitializeData(self):
    self._plotView = None
    self._plotWidget = None
    self._plotViewNode = None
    self._plotSeriesNodes = []

    self._initializeChartTable()
    self._initializePlotChartNode()
    self._initializePlotView()
    self._initializePlotWidgets()

  def _initializeChartTable(self):
    self._chartTable = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTableNode", "Target Displacement")

  def _initializePlotChartNode(self):
    self._plotChartNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLPlotChartNode")
    self._plotChartNode.SetXAxisTitle('Series Number')
    self._plotChartNode.SetYAxisTitle('Displacement')

    def createFloatArray(name):
      floatArray = vtk.vtkFloatArray()
      floatArray.SetName(name)
      self._chartTable.AddColumn(floatArray)
      return floatArray

    self.arrX = createFloatArray('X Axis')
    self.arrXD = createFloatArray('L/R Displ')
    self.arrYD = createFloatArray('P/A Displ')
    self.arrZD = createFloatArray('I/S Displ')
    self.arrD = createFloatArray('3-D Dis')

  def _initializePlotView(self):
    if self._plotView:
      self.collapsibleButton.layout().removeWidget(self._plotView)
    self._plotView = slicer.qMRMLPlotView()
    self._plotView.setMinimumSize(400, 200)
    self._plotView.setMRMLScene(slicer.mrmlScene)
    self._plotView.show()
    self.collapsibleButton.layout().addWidget(self._plotView, 1, 0)

  def _initializePlotWidgets(self):
    if self.layoutManager.layout == constants.LAYOUT_FOUR_UP_QUANTITATIVE:
      self._plotWidget = self.layoutManager.plotWidget(0) \
        if self.layoutManager.plotWidget(0).isVisible() else self.layoutManager.plotWidget(1)
    self._updatePlotViewNodes()

  def _updatePlotViewNodes(self):
    self._initializePlotViewNode()
    if self.plotWidgetViewNode:
      self.plotWidgetViewNode.SetPlotChartNodeID(self._plotChartNode.GetID())

  def _initializePlotViewNode(self):
    if self._plotViewNode is None:
      self._plotViewNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLPlotViewNode")
    self._plotView.setMRMLPlotViewNode(self._plotViewNode)
    self._plotViewNode.SetPlotChartNodeID(self._plotChartNode.GetID())

  def _initializeChart(self, coverProstateSeriesNumber):
    self.arrX.InsertNextValue(coverProstateSeriesNumber)
    for arr in [self.arrXD, self.arrYD, self.arrZD, self.arrD]:
      arr.InsertNextValue(0)

  def setupConnections(self):
    self.showLegendCheckBox.connect('stateChanged(int)', self.onShowLegendChanged)

  def onActivation(self):
    super(SliceTrackerDisplacementChartPlugin, self).onActivation()
    defaultLayout = getattr(constants, self.getSetting("DEFAULT_EVALUATION_LAYOUT"), constants.LAYOUT_SIDE_BY_SIDE)
    if defaultLayout != self.layoutManager.layout:
      self.layoutManager.setLayout(defaultLayout)
    else:
      self.onLayoutChanged(defaultLayout)

  def onDeactivation(self):
    super(SliceTrackerDisplacementChartPlugin, self).onDeactivation()
    if self.plotWidgetViewNode:
      self.plotWidgetViewNode.SetPlotChartNodeID(None)

  def onShowLegendChanged(self, checked):
    self._plotChartNode.SetLegendVisibility(True if checked == 2 else False)

  def addSessionObservers(self):
    super(SliceTrackerDisplacementChartPlugin, self).addSessionObservers()
    self.session.addEventObserver(self.session.TargetSelectionEvent, self.onTargetSelectionChanged)

  def removeSessionEventObservers(self):
    super(SliceTrackerDisplacementChartPlugin, self).removeSessionEventObservers()
    self.session.removeEventObserver(self.session.TargetSelectionEvent, self.onTargetSelectionChanged)

  @onModuleSelected(SliceTrackerPlugin.MODULE_NAME)
  def onLayoutChanged(self, layout=None):
    self.collapsibleButton.visible = layout != constants.LAYOUT_FOUR_UP_QUANTITATIVE
    if not self.collapsibleButton.visible:
      self._initializePlotWidgets()

  @vtk.calldata_type(vtk.VTK_STRING)
  def onTargetSelectionChanged(self, caller, event, callData):
    info = ast.literal_eval(callData)
    targetsAvailable = info['nodeId'] and info['index'] != -1
    if targetsAvailable:
      self.targetIndex = info['index']
      self.currResultTargets = slicer.mrmlScene.GetNodeByID(info['nodeId'])
      logging.debug(info)
    self.updateTargetDisplacementChart(targetsAvailable)

  def updateTargetDisplacementChart(self, targetsAvailable):
    if self.logic.isTargetDisplacementChartDisplayable(self.session.currentSeries) and targetsAvailable:
      self.resetChart()
      results = sorted([r for r in self.session.data.registrationResults.values() if r.approved],
                       key=lambda result: result.seriesNumber)
      if not self.session.currentResult.wasEvaluated():
        results.append(self.session.currentResult)
      for currIndex, currResult in enumerate(results[1:], 1):
        prevTargets = results[currIndex - 1].targets.approved
        if not self.session.currentResult.wasEvaluated() and currIndex == len(results[1:]):
          currTargets = self.currResultTargets
        else:
          currTargets = currResult.targets.approved
        displacement = self.logic.calculateTargetDisplacement(prevTargets, currTargets, self.targetIndex)
        self.invokeEvent(self.ShowEvent)
        self.addPlotPoints([displacement], currResult.seriesNumber)
      self.invokeEvent(self.ShowEvent)
    else:
      self.invokeEvent(self.HideEvent)

  def addPlotPoints(self, displacement, seriesNumber):
    numCurrentRows = self._chartTable.GetNumberOfRows()
    self._plotViewNode.SetPlotChartNodeID(self._plotChartNode.GetID())

    for i in range(len(displacement)):
      if numCurrentRows == 0:
        self._initializeChart(self.session.data.getMostRecentApprovedCoverProstateRegistration().seriesNumber)
      self.arrX.InsertNextValue(seriesNumber)
      for component, arr in enumerate([self.arrXD, self.arrYD, self.arrZD]):
        arr.InsertNextValue(displacement[i][component])
      distance = (displacement[i][0] ** 2 + displacement[i][1] ** 2 + displacement[i][2] ** 2) ** 0.5
      self.arrD.InsertNextValue(distance)
    if self._plotChartNode.GetNumberOfPlotSeriesNodes() == 0:
      for index, plot in enumerate([self.PLOT_COLOR_LR, self.PLOT_COLOR_PA, self.PLOT_COLOR_IS, self.PLOT_COLOR_3D], start=1):
        self.createPlot(plot, index)
    else:
      self._plotChartNode.RemoveAllPlotSeriesNodeIDs()
      for plotSeriesNode in self._plotSeriesNodes:
        self._plotChartNode.AddAndObservePlotSeriesNodeID(plotSeriesNode.GetID())

  def createPlot(self, color, plotNumber):
    plotSeriesNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLPlotSeriesNode", self.PLOT_NAMES[plotNumber - 1])
    plotSeriesNode.SetAndObserveTableNodeID(self._chartTable.GetID())
    plotSeriesNode.SetXColumnName(self._chartTable.GetColumnName(0))
    plotSeriesNode.SetYColumnName(self._chartTable.GetColumnName(plotNumber))
    plotSeriesNode.SetColor(color)
    plotSeriesNode.SetPlotType(slicer.vtkMRMLPlotSeriesNode.PlotTypeScatter)
    plotSeriesNode.SetMarkerStyle(4)
    plotSeriesNode.SetMarkerSize(3 * plotSeriesNode.GetLineWidth())

    self._plotSeriesNodes.append(plotSeriesNode)
    self._plotChartNode.AddAndObservePlotSeriesNodeID(plotSeriesNode.GetID())
