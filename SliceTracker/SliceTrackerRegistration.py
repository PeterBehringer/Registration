import argparse, sys, os, logging
import qt, slicer
from slicer.ScriptedLoadableModule import *
from SlicerDevelopmentToolboxUtils.mixins import ModuleLogicMixin, ModuleWidgetMixin
from SliceTrackerUtils.sessionData import *
from SliceTrackerUtils.constants import SliceTrackerConstants
from SlicerDevelopmentToolboxUtils.decorators import onReturnProcessEvents


class SliceTrackerRegistration(ScriptedLoadableModule):

  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "SliceTracker Registration"
    self.parent.categories = ["Radiology"]
    self.parent.dependencies = ["SlicerDevelopmentToolbox"]
    self.parent.contributors = ["Peter Behringer (SPL), Christian Herz (SPL), Andriy Fedorov (SPL)"]
    self.parent.helpText = """ SliceTracker Registration facilitates support of MRI-guided targeted prostate biopsy. """
    self.parent.acknowledgementText = """Surgical Planning Laboratory, Brigham and Women's Hospital, Harvard
                                          Medical School, Boston, USA This work was supported in part by the National
                                          Institutes of Health through grants U24 CA180918,
                                          R01 CA111288 and P41 EB015898."""
    self.parent = parent

    try:
      slicer.selfTests
    except AttributeError:
      slicer.selfTests = {}
    slicer.selfTests['SliceTrackerRegistration'] = self.runTest

  def runTest(self):
    tester = SliceTrackerRegistration()
    tester.runTest()


class SliceTrackerRegistrationWidget(ScriptedLoadableModuleWidget, ModuleWidgetMixin):

  def __init__(self, parent=None):
    ScriptedLoadableModuleWidget.__init__(self, parent)
    self.logic = SliceTrackerRegistrationLogic()

  def setup(self):
    ScriptedLoadableModuleWidget.setup(self)
    self.createSliceWidgetClassMembers("Red")
    self.createSliceWidgetClassMembers("Yellow")
    self.registrationGroupBox = qt.QGroupBox()
    self.registrationGroupBoxLayout = qt.QFormLayout()
    self.registrationGroupBox.setLayout(self.registrationGroupBoxLayout)
    self.movingVolumeSelector = self.createComboBox(nodeTypes=["vtkMRMLScalarVolumeNode", ""], showChildNodeTypes=False,
                                                    selectNodeUponCreation=True, toolTip="Pick algorithm input.")
    self.movingLabelSelector = self.createComboBox(nodeTypes=["vtkMRMLLabelMapVolumeNode", ""], showChildNodeTypes=False,
                                                   selectNodeUponCreation=False, toolTip="Pick algorithm input.")
    self.fixedVolumeSelector = self.createComboBox(nodeTypes=["vtkMRMLScalarVolumeNode", ""], noneEnabled=True,
                                                   showChildNodeTypes=False, selectNodeUponCreation=True,
                                                   toolTip="Pick algorithm input.")
    self.fixedLabelSelector = self.createComboBox(nodeTypes=["vtkMRMLLabelMapVolumeNode", ""],
                                                  showChildNodeTypes=False,
                                                  selectNodeUponCreation=True, toolTip="Pick algorithm input.")
    self.fiducialSelector = self.createComboBox(nodeTypes=["vtkMRMLMarkupsFiducialNode", ""], noneEnabled=True,
                                                showChildNodeTypes=False, selectNodeUponCreation=False,
                                                toolTip="Select the Targets")
    self.initialTransformSelector = self.createComboBox(nodeTypes=["vtkMRMLTransformNode", "vtkMRMLBSplineTransformNode",
                                                                   "vtkMRMLLinearTransformNode", ""],
                                                        noneEnabled=True,
                                                        showChildNodeTypes=False, selectNodeUponCreation=False,
                                                        toolTip="Select the initial transform")
    self.applyRegistrationButton = self.createButton("Run Registration")
    self.registrationGroupBoxLayout.addRow("Moving Image Volume: ", self.movingVolumeSelector)
    self.registrationGroupBoxLayout.addRow("Moving Label Volume: ", self.movingLabelSelector)
    self.registrationGroupBoxLayout.addRow("Fixed Image Volume: ", self.fixedVolumeSelector)
    self.registrationGroupBoxLayout.addRow("Fixed Label Volume: ", self.fixedLabelSelector)
    self.registrationGroupBoxLayout.addRow("Initial Transform: ", self.initialTransformSelector)
    self.registrationGroupBoxLayout.addRow("Targets: ", self.fiducialSelector)
    self.registrationGroupBoxLayout.addRow(self.applyRegistrationButton)
    self.layout.addWidget(self.registrationGroupBox)
    self.layout.addStretch()
    self.setupConnections()
    self.updateButton()

  def setupConnections(self):
    self.applyRegistrationButton.clicked.connect(self.runRegistration)
    self.movingVolumeSelector.connect('currentNodeChanged(bool)', self.updateButton)
    self.movingVolumeSelector.connect('currentNodeChanged(bool)', self.updateButton)
    self.fixedVolumeSelector.connect('currentNodeChanged(bool)', self.updateButton)
    self.fixedLabelSelector.connect('currentNodeChanged(bool)', self.updateButton)
    self.movingLabelSelector.connect('currentNodeChanged(bool)', self.updateButton)
    self.fiducialSelector.connect('currentNodeChanged(bool)', self.updateButton)

  def updateButton(self):
    if not self.layoutManager.layout == SliceTrackerConstants.LAYOUT_SIDE_BY_SIDE:
      self.layoutManager.setLayout(SliceTrackerConstants.LAYOUT_SIDE_BY_SIDE)
    if self.movingVolumeSelector.currentNode():
      self.redCompositeNode.SetForegroundVolumeID(None)
      self.redCompositeNode.SetBackgroundVolumeID(self.movingVolumeSelector.currentNode().GetID())
    if self.movingLabelSelector.currentNode():
      self.redCompositeNode.SetLabelVolumeID(self.movingLabelSelector.currentNode().GetID())
    if self.fixedVolumeSelector.currentNode():
      self.yellowCompositeNode.SetForegroundVolumeID(None)
      self.yellowCompositeNode.SetBackgroundVolumeID(self.fixedVolumeSelector.currentNode().GetID())
    if self.fixedLabelSelector.currentNode():
      self.yellowCompositeNode.SetLabelVolumeID(self.fixedLabelSelector.currentNode().GetID())
    self.applyRegistrationButton.enabled = self.isRegistrationPossible()

  def isRegistrationPossible(self):
    return self.movingVolumeSelector.currentNode() and self.fixedVolumeSelector.currentNode() and \
           self.fixedLabelSelector.currentNode() and self.movingLabelSelector.currentNode()

  def runRegistration(self):
    logging.debug("Starting Registration")
    self.progress = self.createProgressDialog(value=1, maximum=4)
    parameterNode = slicer.vtkMRMLScriptedModuleNode()
    parameterNode.SetAttribute('FixedImageNodeID', self.fixedVolumeSelector.currentNode().GetID())
    parameterNode.SetAttribute('FixedLabelNodeID', self.fixedLabelSelector.currentNode().GetID())
    parameterNode.SetAttribute('MovingImageNodeID', self.movingVolumeSelector.currentNode().GetID())
    parameterNode.SetAttribute('MovingLabelNodeID', self.movingLabelSelector.currentNode().GetID())
    if self.fiducialSelector.currentNode():
      parameterNode.SetAttribute('TargetsNodeID', self.fiducialSelector.currentNode().GetID())
    if self.initialTransformSelector.currentNode():
      parameterNode.SetAttribute('InitialTransformNodeID', self.initialTransformSelector.currentNode().GetID())
      self.logic.runReRegistration(parameterNode, progressCallback=self.updateProgressBar)
    else:
      self.logic.run(parameterNode, progressCallback=self.updateProgressBar)
    self.progress.close()

  @onReturnProcessEvents
  def updateProgressBar(self, **kwargs):
    if self.progress:
      for key, value in kwargs.items():
        if hasattr(self.progress, key):
          setattr(self.progress, key, value)


class SliceTrackerRegistrationLogic(ScriptedLoadableModuleLogic, ModuleLogicMixin):

  counter = 1

  def __init__(self):
    ScriptedLoadableModuleLogic.__init__(self)
    self.registrationResult = None

  def _processParameterNode(self, parameterNode):
    if not self.registrationResult:
      self.registrationResult = RegistrationResult("01: RegistrationResult")
    result = self.registrationResult
    result.volumes.fixed = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('FixedImageNodeID'))
    result.labels.fixed = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('FixedLabelNodeID'))
    result.labels.moving = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingLabelNodeID'))
    movingVolume = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingImageNodeID'))
    result.volumes.moving = self.volumesLogic.CloneVolume(slicer.mrmlScene, movingVolume,
                                                          "temp-movingVolume_" + str(self.counter))
    self.counter += 1

    logging.debug("Fixed Image Name: %s" % result.volumes.fixed.GetName())
    logging.debug("Fixed Label Name: %s" % result.labels.fixed.GetName())
    logging.debug("Moving Image Name: %s" % movingVolume.GetName())
    logging.debug("Moving Label Name: %s" % result.labels.moving.GetName())
    initialTransform = parameterNode.GetAttribute('InitialTransformNodeID')
    if initialTransform:
      initialTransform = slicer.mrmlScene.GetNodeByID(initialTransform)
      logging.debug("Initial Registration Name: %s" % initialTransform.GetName())
    return result

  def run(self, parameterNode, progressCallback=None):
    self.progressCallback = progressCallback
    result = self._processParameterNode(parameterNode)

    registrationTypes = ['rigid', 'affine', 'bSpline']
    self.createVolumeAndTransformNodes(registrationTypes, prefix=str(result.seriesNumber), suffix=result.suffix)

    self.doRigidRegistration(movingBinaryVolume=result.labels.moving, initializeTransformMode="useCenterOfROIAlign")
    self.doAffineRegistration()
    self.doBSplineRegistration(initialTransform=result.transforms.affine)

    targetsNodeID = parameterNode.GetAttribute('TargetsNodeID')
    if targetsNodeID:
      result.targets.original = slicer.mrmlScene.GetNodeByID(targetsNodeID)
      self.transformTargets(registrationTypes, result.targets.original, str(result.seriesNumber), suffix=result.suffix)
    result.volumes.moving = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingImageNodeID'))

  def runReRegistration(self, parameterNode, progressCallback=None):
    logging.debug("Starting Re-Registration")

    self.progressCallback = progressCallback

    self._processParameterNode(parameterNode)
    result = self.registrationResult

    registrationTypes = ['rigid', 'bSpline']
    self.createVolumeAndTransformNodes(registrationTypes, prefix=str(result.seriesNumber), suffix=result.suffix)
    initialTransform = parameterNode.GetAttribute('InitialTransformNodeID')

    if initialTransform:
      initialTransform = slicer.mrmlScene.GetNodeByID(initialTransform)

    # TODO: label value should be delivered by parameterNode
    self.dilateMask(result.labels.fixed, dilateValue=1)
    self.doRigidRegistration(movingBinaryVolume=result.labels.moving,
                             initialTransform=initialTransform if initialTransform else None)
    self.doBSplineRegistration(initialTransform=result.transforms.rigid, useScaleVersor3D=True, useScaleSkewVersor3D=True,
                               useAffine=True)

    targetsNodeID = parameterNode.GetAttribute('TargetsNodeID')
    if targetsNodeID:
      result.targets.original = slicer.mrmlScene.GetNodeByID(targetsNodeID)
      self.transformTargets(registrationTypes, result.originalTargets, str(result.seriesNumber), suffix=result.suffix)
    result.movingVolume = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingImageNodeID'))

  def createVolumeAndTransformNodes(self, registrationTypes, prefix, suffix=""):
    for regType in registrationTypes:
      self.registrationResult.setVolume(regType, self.createScalarVolumeNode(prefix + '-VOLUME-' + regType + suffix))
      transformName = prefix + '-TRANSFORM-' + regType + suffix
      transform = self.createBSplineTransformNode(transformName) if regType == 'bSpline' \
        else self.createLinearTransformNode(transformName)
      self.registrationResult.setTransform(regType, transform)

  def transformTargets(self, registrations, targets, prefix, suffix=""):
    if targets:
      for registration in registrations:
        name = prefix + '-TARGETS-' + registration + suffix
        clone = self.cloneFiducialAndTransform(name, targets, self.registrationResult.getTransform(registration))
        clone.SetLocked(True)
        self.registrationResult.setTargets(registration, clone)

  def cloneFiducialAndTransform(self, cloneName, originalTargets, transformNode):
    tfmLogic = slicer.modules.transforms.logic()
    clonedTargets = self.cloneFiducials(originalTargets, cloneName)
    clonedTargets.SetAndObserveTransformNodeID(transformNode.GetID())
    tfmLogic.hardenTransform(clonedTargets)
    return clonedTargets

  def doRigidRegistration(self, **kwargs):
    self.updateProgress(labelText='\nRigid registration', value=2)
    paramsRigid = {'fixedVolume': self.registrationResult.volumes.fixed,
                   'movingVolume': self.registrationResult.volumes.moving,
                   'fixedBinaryVolume': self.registrationResult.labels.fixed,
                   'outputTransform': self.registrationResult.transforms.rigid.GetID(),
                   'outputVolume': self.registrationResult.volumes.rigid.GetID(),
                   'maskProcessingMode': "ROI",
                   'useRigid': True}
    for key, value in kwargs.items():
      paramsRigid[key] = value
    slicer.cli.run(slicer.modules.brainsfit, None, paramsRigid, wait_for_completion=True)
    self.registrationResult.cmdArguments += "Rigid Registration Parameters: %s" % str(paramsRigid) + "\n\n"

  def doAffineRegistration(self):
    self.updateProgress(labelText='\nAffine registration', value=2)
    paramsAffine = {'fixedVolume': self.registrationResult.volumes.fixed,
                    'movingVolume': self.registrationResult.volumes.moving,
                    'fixedBinaryVolume': self.registrationResult.labels.fixed,
                    'movingBinaryVolume': self.registrationResult.labels.moving,
                    'outputTransform': self.registrationResult.transforms.affine.GetID(),
                    'outputVolume': self.registrationResult.volumes.affine.GetID(),
                    'maskProcessingMode': "ROI",
                    'useAffine': True,
                    'initialTransform': self.registrationResult.transforms.rigid}
    slicer.cli.run(slicer.modules.brainsfit, None, paramsAffine, wait_for_completion=True)
    self.registrationResult.cmdArguments += "Affine Registration Parameters: %s" % str(paramsAffine) + "\n\n"

  def doBSplineRegistration(self, initialTransform, **kwargs):
    self.updateProgress(labelText='\nBSpline registration', value=3)
    paramsBSpline = {'fixedVolume': self.registrationResult.volumes.fixed,
                     'movingVolume': self.registrationResult.volumes.moving,
                     'outputVolume': self.registrationResult.volumes.bSpline.GetID(),
                     'bsplineTransform': self.registrationResult.transforms.bSpline.GetID(),
                     'fixedBinaryVolume': self.registrationResult.labels.fixed,
                     'movingBinaryVolume': self.registrationResult.labels.moving,
                     'useROIBSpline': True,
                     'useBSpline': True,
                     'splineGridSize': "3,3,3",
                     'maskProcessing': "ROI",
                     'minimumStepLength': "0.005",
                     'maximumStepLength': "0.2",
                     'costFunctionConvergenceFactor': "1.00E+09",
                     'maskProcessingMode': "ROI",
                     'initialTransform': initialTransform}
    for key, value in kwargs.items():
      paramsBSpline[key] = value

    slicer.cli.run(slicer.modules.brainsfit, None, paramsBSpline, wait_for_completion=True)
    self.registrationResult.cmdArguments += "BSpline Registration Parameters: %s" % str(paramsBSpline) + "\n\n"

    self.updateProgress(labelText='\nCompleted registration', value=4)

  def updateProgress(self, **kwargs):
    if self.progressCallback:
      self.progressCallback(**kwargs)


def main(argv):
  try:
    parser = argparse.ArgumentParser(description="Slicetracker Registration")
    parser.add_argument("-fl", "--fixed-label", dest="fixed_label", metavar="PATH", default="-", required=True,
                        help="Fixed label to be used for registration")
    parser.add_argument("-ml", "--moving-label", dest="moving_label", metavar="PATH", default="-", required=True,
                        help="Moving label to be used for registration")
    parser.add_argument("-fv", "--fixed-volume", dest="fixed_volume", metavar="PATH", default="-", required=True,
                        help="Fixed volume to be used for registration")
    parser.add_argument("-mv", "--moving-volume", dest="moving_volume", metavar="PATH", default="-", required=True,
                        help="Moving volume to be used for registration")
    parser.add_argument("-it", "--initial-transform", dest="initial_transform", metavar="PATH", default="-",
                        required=False, help="Initial rigid transform for re-registration")
    parser.add_argument("-o", "--output-directory", dest="output_directory", metavar="PATH", default="-",
                        required=False, help="Output directory for registration result")

    args = parser.parse_args(argv)

    for inputFile in [args.fixed_label, args.moving_label, args.fixed_volume, args.moving_volume]:
      if not os.path.isfile(inputFile):
        raise AttributeError("File not found: %s" % inputFile)

    success, fixedLabel = slicer.util.loadLabelVolume(args.fixed_label, returnNode=True)
    success, movingLabel = slicer.util.loadLabelVolume(args.moving_label, returnNode=True)
    success, fixedVolume = slicer.util.loadVolume(args.fixed_volume, returnNode=True)
    success, movingVolume = slicer.util.loadVolume(args.moving_volume, returnNode=True)

    parameterNode = slicer.vtkMRMLScriptedModuleNode()
    parameterNode.SetAttribute('FixedImageNodeID', fixedVolume.GetID())
    parameterNode.SetAttribute('FixedLabelNodeID', fixedLabel.GetID())
    parameterNode.SetAttribute('MovingImageNodeID', movingVolume.GetID())
    parameterNode.SetAttribute('MovingLabelNodeID', movingLabel.GetID())

    logic = SliceTrackerRegistrationLogic()
    logic.run(parameterNode)

    if args.output_directory != "-":
      logic.registrationResult.save(args.output_directory)

  except Exception as e:
    print(e)
  sys.exit(0)

if __name__ == "__main__":
  main(sys.argv[1:])
