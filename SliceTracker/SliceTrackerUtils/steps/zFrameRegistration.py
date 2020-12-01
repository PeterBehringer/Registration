import sys, os
import qt, vtk
import csv, numpy
import slicer
import ast

import SimpleITK as sitk
import sitkUtils

from ..algorithms.zFrameRegistration import LineMarkerRegistration, OpenSourceZFrameRegistration
from ..constants import SliceTrackerConstants
from .base import SliceTrackerLogicBase, SliceTrackerStep

from SlicerDevelopmentToolboxUtils.decorators import onModuleSelected
from SlicerDevelopmentToolboxUtils.helpers import SliceAnnotation
from SlicerDevelopmentToolboxUtils.metaclasses import Singleton
from SlicerDevelopmentToolboxUtils.icons import Icons


class SliceTrackerZFrameRegistrationStepLogic(SliceTrackerLogicBase):

  __metaclass__ = Singleton

  ZFRAME_MODEL_PATH = 'zframe-model.vtk'
  ZFRAME_TEMPLATE_CONFIG_FILE_NAME = 'ProstateTemplate.csv'
  ZFRAME_MODEL_NAME = 'ZFrameModel'
  ZFRAME_TEMPLATE_NAME = 'NeedleGuideTemplate'
  ZFRAME_TEMPLATE_PATH_NAME = 'NeedleGuideNeedlePath'
  # COMPUTED_NEEDLE_MODEL_NAME = 'ComputedNeedleModel'

  @property
  def templateSuccessfulLoaded(self):
    return self.tempModelNode and self.pathModelNode

  @property
  def zFrameSuccessfulLoaded(self):
    return self.zFrameModelNode

  def __init__(self):
    super(SliceTrackerZFrameRegistrationStepLogic, self).__init__()
    self.setupSliceWidgets()
    self.resetAndInitializeData()

  def resetAndInitializeData(self):
    self.templateVolume = None

    self.zFrameModelNode = None
    self.zFrameTransform = None

    self.showTemplatePath = False
    self.showNeedlePath = False

    self.needleModelNode = None
    self.tempModelNode = None
    self.pathModelNode = None
    self.templateConfig = []
    self.templateMaxDepth = []
    self.pathOrigins = []  ## Origins of needle paths (after transformation by parent transform node)
    self.pathVectors = []  ## Normal vectors of needle paths (after transformation by parent transform node)

    self.clearOldNodes()
    self.loadZFrameModel()
    self.loadTemplateConfigFile()

  def cleanup(self):
    super(SliceTrackerZFrameRegistrationStepLogic, self).cleanup()

  @onModuleSelected(SliceTrackerStep.MODULE_NAME)
  def onMrmlSceneCleared(self, caller, event):
    self.resetAndInitializeData()

  def clearOldNodes(self):
    self.clearOldNodesByName(self.ZFRAME_TEMPLATE_NAME)
    self.clearOldNodesByName(self.ZFRAME_TEMPLATE_PATH_NAME)
    self.clearOldNodesByName(self.ZFRAME_MODEL_NAME)
    # self.clearOldNodesByName(self.COMPUTED_NEEDLE_MODEL_NAME)

  def loadZFrameModel(self):
    zFrameModelPath = os.path.join(self.resourcesPath, "zframe", self.ZFRAME_MODEL_PATH)
    if not self.zFrameModelNode:
      self.zFrameModelNode = slicer.util.loadModel(zFrameModelPath)
      self.zFrameModelNode.SetName(self.ZFRAME_MODEL_NAME)
      slicer.mrmlScene.AddNode(self.zFrameModelNode)
      modelDisplayNode = self.zFrameModelNode.GetDisplayNode()
      modelDisplayNode.SetColor(1, 1, 0)
    self.zFrameModelNode.SetDisplayVisibility(False)

  def clearOldNodesByName(self, name):
    collection = slicer.mrmlScene.GetNodesByName(name)
    for index in range(collection.GetNumberOfItems()):
      slicer.mrmlScene.RemoveNode(collection.GetItemAsObject(index))

  def setupSliceWidgets(self):
    self.redSliceWidget = slicer.app.layoutManager().sliceWidget("Red")
    self.redSliceView = self.redSliceWidget.sliceView()
    self.redSliceLogic = self.redSliceWidget.sliceLogic()

  def loadTemplateConfigFile(self):
    self.templateIndex = []
    self.templateConfig = []

    defaultTemplateFile = os.path.join(self.resourcesPath, "zframe", self.ZFRAME_TEMPLATE_CONFIG_FILE_NAME)

    reader = csv.reader(open(defaultTemplateFile, newline=''))
    try:
      next(reader)
      for row in reader:
        self.templateIndex.append(row[0:2])
        self.templateConfig.append([float(row[2]), float(row[3]), float(row[4]),
                                    float(row[5]), float(row[6]), float(row[7]),
                                    float(row[8])])
    except csv.Error as e:
      print('file %s, line %d: %s' % (defaultTemplateFile, reader.line_num, e))
      return

    self.createTemplateAndNeedlePathModel()
    self.setTemplateVisibility(0)
    self.setTemplatePathVisibility(0)
    self.setNeedlePathVisibility(0)
    self.updateTemplateVectors()

  def createTemplateAndNeedlePathModel(self):
    self.templatePathVectors = []
    self.templatePathOrigins = []

    self.checkAndCreateTemplateModelNode()
    self.checkAndCreatePathModelNode()

    pathModelAppend = vtk.vtkAppendPolyData()
    templateModelAppend = vtk.vtkAppendPolyData()

    for row in self.templateConfig:
      p, n = self.extractPointsAndNormalVectors(row)

      tempTubeFilter = self.createVTKTubeFilter(p[0], p[1], radius=1.0, numSides=18)
      templateModelAppend.AddInputData(tempTubeFilter.GetOutput())
      templateModelAppend.Update()

      pathTubeFilter = self.createVTKTubeFilter(p[0], p[2], radius=0.8, numSides=18)
      pathModelAppend.AddInputData(pathTubeFilter.GetOutput())
      pathModelAppend.Update()

      self.templatePathOrigins.append([row[0], row[1], row[2], 1.0])
      self.templatePathVectors.append([n[0], n[1], n[2], 1.0])
      self.templateMaxDepth.append(row[6])

    self.tempModelNode.SetAndObservePolyData(templateModelAppend.GetOutput())
    self.tempModelNode.GetDisplayNode().SetColor(0.5,0,1)

    self.pathModelNode.SetAndObservePolyData(pathModelAppend.GetOutput())
    self.pathModelNode.GetDisplayNode().SetColor(0.8,0.5,1)

  def extractPointsAndNormalVectors(self, row):
    p1 = numpy.array(row[0:3])
    p2 = numpy.array(row[3:6])
    v = p2-p1
    nl = numpy.linalg.norm(v)
    n = v/nl  # normal vector
    l = row[6]
    p3 = p1 + l * n
    return [p1, p2, p3], n

  def checkAndCreateTemplateModelNode(self):
    if self.tempModelNode is None:
      self.tempModelNode = self.createModelNode(self.ZFRAME_TEMPLATE_NAME)
      self.createAndObserveDisplayNode(self.tempModelNode, displayNodeClass=slicer.vtkMRMLModelDisplayNode)
      self.modelNodeTag = self.tempModelNode.AddObserver(slicer.vtkMRMLTransformableNode.TransformModifiedEvent,
                                                         self.updateTemplateVectors)

  def checkAndCreatePathModelNode(self):
    if self.pathModelNode is None:
      self.pathModelNode = self.createModelNode(self.ZFRAME_TEMPLATE_PATH_NAME)
      self.createAndObserveDisplayNode(self.pathModelNode, displayNodeClass=slicer.vtkMRMLModelDisplayNode)

  def updateTemplateVectors(self, observee=None, event=None):
    if self.tempModelNode is None:
      return

    trans = vtk.vtkMatrix4x4()
    transformNode = self.tempModelNode.GetParentTransformNode()
    if transformNode is not None:
      transformNode.GetMatrixTransformToWorld(trans)
    else:
      trans.Identity()

    # Calculate offset
    zero = [0.0, 0.0, 0.0, 1.0]
    offset = trans.MultiplyDoublePoint(zero)

    self.pathOrigins = []
    self.pathVectors = []

    for i, orig in enumerate(self.templatePathOrigins):
      torig = trans.MultiplyDoublePoint(orig)
      self.pathOrigins.append(numpy.array(torig[0:3]))
      vec = self.templatePathVectors[i]
      tvec = trans.MultiplyDoublePoint(vec)
      self.pathVectors.append(numpy.array([tvec[0] - offset[0], tvec[1] - offset[1], tvec[2] - offset[2]]))
      i += 1

  def setZFrameVisibility(self, visibility):
    self.setNodeVisibility(self.zFrameModelNode, visibility)
    self.setNodeSliceIntersectionVisibility(self.zFrameModelNode, visibility)

  def setTemplateVisibility(self, visibility):
    self.setNodeVisibility(self.tempModelNode, visibility)

  def setTemplatePathVisibility(self, visibility):
    self.showTemplatePath = visibility
    self.setNodeVisibility(self.pathModelNode, visibility)
    self.setNodeSliceIntersectionVisibility(self.pathModelNode, visibility)

  def setNeedlePathVisibility(self, visibility):
    self.showNeedlePath = visibility
    if self.needleModelNode:
      self.setNodeVisibility(self.needleModelNode, visibility)
      self.setNodeSliceIntersectionVisibility(self.needleModelNode, visibility)

  def runZFrameRegistration(self, inputVolume, algorithm, **kwargs):
    registration = algorithm(inputVolume)
    if isinstance(registration, OpenSourceZFrameRegistration):
      registration.runRegistration(start=kwargs.pop("startSlice"), end=kwargs.pop("endSlice"))
    elif isinstance(registration, LineMarkerRegistration):
      registration.runRegistration()
    zFrameRegistrationResult = self.session.data.createZFrameRegistrationResult(self.templateVolume.GetName())
    zFrameRegistrationResult.volume = inputVolume
    zFrameRegistrationResult.transform = registration.getOutputTransformation()
    return True

  def getROIMinCenterMaxSliceNumbers(self, coverTemplateROI):
    center = [0.0, 0.0, 0.0]
    coverTemplateROI.GetXYZ(center)
    bounds = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    coverTemplateROI.GetRASBounds(bounds)
    pMin = [bounds[0], bounds[2], bounds[4]]
    pMax = [bounds[1], bounds[3], bounds[5]]
    return [self.getIJKForXYZ(self.redSliceWidget, pMin)[2], self.getIJKForXYZ(self.redSliceWidget, center)[2],
            self.getIJKForXYZ(self.redSliceWidget, pMax)[2]]

  def getStartEndWithConnectedComponents(self, volume, center):
    address = sitkUtils.GetSlicerITKReadWriteAddress(volume.GetName())
    image = sitk.ReadImage(address)
    start = self.getStartSliceUsingConnectedComponents(center, image)
    end = self.getEndSliceUsingConnectedComponents(center, image)
    return start, end

  def getStartSliceUsingConnectedComponents(self, center, image):
    sliceIndex = start = center
    while sliceIndex > 0:
      if self.getIslandCount(image, sliceIndex) > 6:
        start = sliceIndex
        sliceIndex -= 1
        continue
      break
    return start

  def getEndSliceUsingConnectedComponents(self, center, image):
    imageSize = image.GetSize()
    sliceIndex = end = center
    while sliceIndex < imageSize[2]:
      if self.getIslandCount(image, sliceIndex) > 6:
        end = sliceIndex
        sliceIndex += 1
        continue
      break
    return end


class SliceTrackerZFrameRegistrationStep(SliceTrackerStep):

  NAME = "ZFrame Registration"
  LogicClass = SliceTrackerZFrameRegistrationStepLogic
  LayoutClass = qt.QVBoxLayout

  def __init__(self):
    self.annotationLogic = slicer.modules.annotations.logic()
    self.zFrameRegistrationClass = getattr(sys.modules[__name__], self.getSetting("ZFrame_Registration_Class_Name"))

    self.roiObserverTag = None
    self.coverTemplateROI = None
    self.zFrameCroppedVolume = None
    self.zFrameLabelVolume = None
    self.zFrameMaskedVolume = None

    self.zFrameClickObserver = None
    self.zFrameInstructionAnnotation = None

    super(SliceTrackerZFrameRegistrationStep, self).__init__()
    self.logic.templateVolume = None

  def setupIcons(self):
    self.zFrameIcon = self.createIcon('icon-zframe.png')
    self.needleIcon = self.createIcon('icon-needle.png')
    self.templateIcon = self.createIcon('icon-template.png')

  def setup(self):
    super(SliceTrackerZFrameRegistrationStep, self).setup()
    self.setupManualIndexesGroupBox()
    self.setupActionButtons()

    self.layout().addWidget(self.zFrameRegistrationManualIndexesGroupBox)
    self.layout().addWidget(self.createHLayout([self.runZFrameRegistrationButton, self.retryZFrameRegistrationButton,
                                                self.approveZFrameRegistrationButton]))
    self.layout().addStretch(1)

  def setupManualIndexesGroupBox(self):
    self.zFrameRegistrationManualIndexesGroupBox = qt.QGroupBox("Use manual start/end indexes")
    self.zFrameRegistrationManualIndexesGroupBox.setCheckable(True)
    self.zFrameRegistrationManualIndexesGroupBoxLayout = qt.QGridLayout()
    self.zFrameRegistrationManualIndexesGroupBox.setLayout(self.zFrameRegistrationManualIndexesGroupBoxLayout)
    self.zFrameRegistrationManualIndexesGroupBox.checked = False
    self.zFrameRegistrationStartIndex = qt.QSpinBox()
    self.zFrameRegistrationEndIndex = qt.QSpinBox()
    hBox = self.createHLayout([qt.QLabel("start"), self.zFrameRegistrationStartIndex,
                               qt.QLabel("end"), self.zFrameRegistrationEndIndex])
    self.zFrameRegistrationManualIndexesGroupBoxLayout.addWidget(hBox, 1, 1, qt.Qt.AlignRight)

  def setupActionButtons(self):
    iconSize = qt.QSize(36, 36)
    self.runZFrameRegistrationButton = self.createButton("", icon=Icons.start, iconSize=iconSize, enabled=False,
                                                         toolTip="Run ZFrame Registration")
    self.approveZFrameRegistrationButton = self.createButton("", icon=Icons.apply, iconSize=iconSize,
                                                             enabled=self.zFrameRegistrationClass is LineMarkerRegistration,
                                                             toolTip="Confirm registration accuracy", )
    self.retryZFrameRegistrationButton = self.createButton("", icon=Icons.retry, iconSize=iconSize, enabled=False,
                                                           visible=self.zFrameRegistrationClass is OpenSourceZFrameRegistration,
                                                           toolTip="Reset")

  def setupAdditionalViewSettingButtons(self):
    iconSize = qt.QSize(24, 24)
    self.showZFrameModelButton = self.createButton("", icon=self.zFrameIcon, iconSize=iconSize, checkable=True, toolTip="Display zFrame model")
    self.showTemplateButton = self.createButton("", icon=self.templateIcon, iconSize=iconSize, checkable=True, toolTip="Display template")
    # self.showNeedlePathButton = self.createButton("", icon=self.needleIcon, iconSize=iconSize, checkable=True, toolTip="Display needle path")
    self.showTemplatePathButton = self.createButton("", icon=self.templateIcon, iconSize=iconSize, checkable=True, toolTip="Display template paths")
    self.viewSettingButtons = [self.showZFrameModelButton, self.showTemplateButton]

  def setupConnections(self):
    self.retryZFrameRegistrationButton.clicked.connect(self.onRetryZFrameRegistrationButtonClicked)
    self.approveZFrameRegistrationButton.clicked.connect(self.onApproveZFrameRegistrationButtonClicked)
    self.runZFrameRegistrationButton.clicked.connect(self.onApplyZFrameRegistrationButtonClicked)

    self.showZFrameModelButton.connect('toggled(bool)', self.onShowZFrameModelToggled)
    self.showTemplateButton.connect('toggled(bool)', self.onShowZFrameTemplateToggled)
    self.showTemplatePathButton.connect('toggled(bool)', self.onShowTemplatePathToggled)
    # self.showNeedlePathButton.connect('toggled(bool)', self.onShowNeedlePathToggled)

  def addSessionObservers(self):
    super(SliceTrackerZFrameRegistrationStep, self).addSessionObservers()
    self.session.addEventObserver(self.session.InitiateZFrameCalibrationEvent, self.onInitiateZFrameCalibration)

  def removeSessionEventObservers(self):
    super(SliceTrackerZFrameRegistrationStep, self).removeSessionEventObservers()
    self.session.removeEventObserver(self.session.InitiateZFrameCalibrationEvent, self.onInitiateZFrameCalibration)

  def onShowZFrameModelToggled(self, checked):
    self.logic.setZFrameVisibility(checked)

  def onShowZFrameTemplateToggled(self, checked):
    self.logic.setTemplateVisibility(checked)
    self.logic.setTemplatePathVisibility(checked)

  def onShowTemplatePathToggled(self, checked):
    self.logic.setTemplatePathVisibility(checked)

  def onShowNeedlePathToggled(self, checked):
    self.logic.setNeedlePathVisibility(checked)

  def resetViewSettingButtons(self):
    self.showTemplateButton.enabled = self.logic.templateSuccessfulLoaded
    self.showTemplatePathButton.enabled = self.logic.templateSuccessfulLoaded
    self.showZFrameModelButton.enabled = self.logic.zFrameSuccessfulLoaded

  def onInitiateZFrameCalibration(self, caller, event):
    self.logic.templateVolume = self.session.currentSeriesVolume
    self.active = True

  @vtk.calldata_type(vtk.VTK_STRING)
  def onNewImageSeriesReceived(self, caller, event, callData):
    # TODO: control here to automatically activate the step
    if not self.active:
      return
    newImageSeries = ast.literal_eval(callData)
    for series in reversed(newImageSeries):
      if self.session.seriesTypeManager.isCoverTemplate(series):
        if self.logic.templateVolume and series != self.logic.templateVolume.GetName():
          if not slicer.util.confirmYesNoDisplay("Another %s was received. Do you want to use this one for "
                                                 "calibration?" % self.getSetting("COVER_TEMPLATE_PATTERN")):
            return
        self.session.currentSeries = series
        self.removeZFrameInstructionAnnotation()
        self.logic.templateVolume = self.session.currentSeriesVolume
        self.initiateZFrameRegistrationStep()
        return

  def onLoadingMetadataSuccessful(self, caller, event):
    if self.session.zFrameRegistrationSuccessful:
      self.applyZFrameTransform()

  def onActivation(self):
    super(SliceTrackerZFrameRegistrationStep, self).onActivation()
    self.layoutManager.setLayout(SliceTrackerConstants.LAYOUT_FOUR_UP)
    self.showZFrameModelButton.checked = True
    self.showTemplateButton.checked = True
    self.showTemplatePathButton.checked = True
    self.zFrameRegistrationManualIndexesGroupBox.checked = False
    if self.logic.templateVolume:
      self.initiateZFrameRegistrationStep()

  def onDeactivation(self):
    super(SliceTrackerZFrameRegistrationStep, self).onDeactivation()
    self.showZFrameModelButton.checked = False
    self.showTemplateButton.checked = False
    self.showTemplatePathButton.checked = False
    self.logic.templateVolume = None

  def initiateZFrameRegistrationStep(self):
    self.resetZFrameRegistration()
    self.setupFourUpView(self.logic.templateVolume)
    self.redSliceNode.SetSliceVisible(True)
    if self.zFrameRegistrationClass is OpenSourceZFrameRegistration:
      self.addROIObserver()
      self.activateCreateROIMode()
      self.addZFrameInstructions()

  def resetZFrameRegistration(self):
    self.runZFrameRegistrationButton.enabled = False
    self.approveZFrameRegistrationButton.enabled = False
    self.retryZFrameRegistrationButton.enabled = False

    self.removeNodeFromMRMLScene(self.coverTemplateROI)
    self.removeNodeFromMRMLScene(self.zFrameCroppedVolume)
    self.removeNodeFromMRMLScene(self.zFrameLabelVolume)
    self.removeNodeFromMRMLScene(self.zFrameMaskedVolume)
    if self.session.data.zFrameRegistrationResult:
      self.removeNodeFromMRMLScene(self.session.data.zFrameRegistrationResult.transform)
      self.session.data.zFrameRegistrationResult = None

  def addROIObserver(self):
    @vtk.calldata_type(vtk.VTK_OBJECT)
    def onNodeAdded(caller, event, calldata):
      node = calldata
      if isinstance(node, slicer.vtkMRMLAnnotationROINode):
        self.removeROIObserver()
        self.coverTemplateROI = node
        self.runZFrameRegistrationButton.enabled = self.isRegistrationPossible()

    if self.roiObserverTag:
      self.removeROIObserver()
    self.roiObserverTag = slicer.mrmlScene.AddObserver(slicer.vtkMRMLScene.NodeAddedEvent, onNodeAdded)

  def isRegistrationPossible(self):
    return self.coverTemplateROI is not None

  def removeROIObserver(self):
    if self.roiObserverTag:
      self.roiObserverTag = slicer.mrmlScene.RemoveObserver(self.roiObserverTag)

  def activateCreateROIMode(self):
    mrmlScene = self.annotationLogic.GetMRMLScene()
    selectionNode = mrmlScene.GetNthNodeByClass(0, "vtkMRMLSelectionNode")
    selectionNode.SetReferenceActivePlaceNodeClassName("vtkMRMLAnnotationROINode")
    # self.annotationLogic.StopPlaceMode(False) # BUG: http://na-mic.org/Mantis/view.php?id=4355
    self.annotationLogic.StartPlaceMode(False)

  def addZFrameInstructions(self, step=1):
    self.removeZFrameInstructionAnnotation()
    self.zFrameStep = step
    text = SliceTrackerConstants.ZFrame_INSTRUCTION_STEPS[self.zFrameStep]
    self.zFrameInstructionAnnotation = SliceAnnotation(self.redWidget, text, yPos=55, horizontalAlign="center",
                                                       opacity=0.6, color=(0,0.6,0))
    self.zFrameClickObserver = self.redSliceViewInteractor.AddObserver(vtk.vtkCommand.LeftButtonReleaseEvent,
                                                      self.onZFrameStepAccomplished)
    # TODO
    # self.onShowAnnotationsToggled(self.showAnnotationsButton.checked)

  def onZFrameStepAccomplished(self, observee, event):
    self.removeZFrameInstructionAnnotation()
    nextStep = self.zFrameStep + 1
    if nextStep in SliceTrackerConstants.ZFrame_INSTRUCTION_STEPS.keys():
      self.addZFrameInstructions(nextStep)

  def removeZFrameInstructionAnnotation(self):
    if hasattr(self, "zFrameInstructionAnnotation") and self.zFrameInstructionAnnotation:
      self.zFrameInstructionAnnotation.remove()
      self.zFrameInstructionAnnotation = None
    if self.zFrameClickObserver :
      self.redSliceViewInteractor.RemoveObserver(self.zFrameClickObserver)
      self.zFrameClickObserver = None

  def onApplyZFrameRegistrationButtonClicked(self):
    zFrameTemplateVolume = self.logic.templateVolume
    try:
      if self.zFrameRegistrationClass is OpenSourceZFrameRegistration:
        self.annotationLogic.SetAnnotationLockedUnlocked(self.coverTemplateROI.GetID())
        self.zFrameCroppedVolume = self.logic.createCroppedVolume(zFrameTemplateVolume, self.coverTemplateROI)
        self.zFrameLabelVolume = self.logic.createLabelMapFromCroppedVolume(self.zFrameCroppedVolume, "labelmap")
        self.zFrameMaskedVolume = self.logic.createMaskedVolume(zFrameTemplateVolume, self.zFrameLabelVolume,
                                                                outputVolumeName="maskedTemplateVolume")
        self.zFrameMaskedVolume.SetName(zFrameTemplateVolume.GetName() + "-label")

        if not self.zFrameRegistrationManualIndexesGroupBox.checked:
          start, center, end = self.logic.getROIMinCenterMaxSliceNumbers(self.coverTemplateROI)
          otsuOutputVolume = self.logic.applyOtsuFilter(self.zFrameMaskedVolume)
          self.logic.dilateMask(otsuOutputVolume)
          start, end = self.logic.getStartEndWithConnectedComponents(otsuOutputVolume, center)
          self.zFrameRegistrationStartIndex.value = start
          self.zFrameRegistrationEndIndex.value = end
        else:
          start = self.zFrameRegistrationStartIndex.value
          end = self.zFrameRegistrationEndIndex.value
        self.logic.runZFrameRegistration(self.zFrameMaskedVolume, self.zFrameRegistrationClass,
                                         startSlice=start, endSlice=end)
      else:
        self.logic.runZFrameRegistration(zFrameTemplateVolume, self.zFrameRegistrationClass)
      self.applyZFrameTransform()

    except AttributeError as exc:
      slicer.util.errorDisplay("An error occurred. For further information click 'Show Details...'",
                   windowTitle=self.__class__.__name__, detailedText=str(exc.message))
    else:
      self.setBackgroundToVolumeID(zFrameTemplateVolume)
      self.approveZFrameRegistrationButton.enabled = True
      self.retryZFrameRegistrationButton.enabled = True

  def applyZFrameTransform(self):
    for node in [node for node in
                 [self.logic.pathModelNode, self.logic.tempModelNode,
                  self.logic.zFrameModelNode, self.logic.needleModelNode] if node]:
      node.SetAndObserveTransformNodeID(self.session.data.zFrameRegistrationResult.transform.GetID())

  def onApproveZFrameRegistrationButtonClicked(self):
    self.redSliceNode.SetSliceVisible(False)
    if self.zFrameRegistrationClass is OpenSourceZFrameRegistration:
      self.annotationLogic.SetAnnotationVisibility(self.coverTemplateROI.GetID())
    self.session.approvedCoverTemplate = self.logic.templateVolume

  def onRetryZFrameRegistrationButtonClicked(self):
    self.removeZFrameInstructionAnnotation()
    self.annotationLogic.SetAnnotationVisibility(self.coverTemplateROI.GetID())
    self.initiateZFrameRegistrationStep()
