import argparse, sys, os, logging
import qt, slicer
from slicer.ScriptedLoadableModule import *

from SlicerDevelopmentToolboxUtils.mixins import ModuleWidgetMixin
from SlicerDevelopmentToolboxUtils.decorators import onReturnProcessEvents

from SliceTrackerUtils.constants import SliceTrackerConstants
from SliceTrackerUtils.sessionData import RegistrationResult
import SliceTrackerUtils.algorithms.registration as registration


class SliceTrackerRegistration(ScriptedLoadableModule):

  def __init__(self, parent):
    ScriptedLoadableModule.__init__(self, parent)
    self.parent.title = "SliceTracker Registration"
    self.parent.categories = ["Radiology"]
    self.parent.dependencies = ["SlicerDevelopmentToolbox"]
    self.parent.contributors = ["Christian Herz (SPL), Peter Behringer (SPL), Andriy Fedorov (SPL)"]
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
    self.imageRegistrationTool = None
    self.counter = 1

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

    self.imageRegistrationToolSelector = qt.QComboBox()
    self.imageRegistrationToolSelector.addItems(registration.__tools__.keys())

    self.applyRegistrationButton = self.createButton("Run Registration")
    self.registrationGroupBoxLayout.addRow("Moving Image Volume: ", self.movingVolumeSelector)
    self.registrationGroupBoxLayout.addRow("Moving Label Volume: ", self.movingLabelSelector)
    self.registrationGroupBoxLayout.addRow("Fixed Image Volume: ", self.fixedVolumeSelector)
    self.registrationGroupBoxLayout.addRow("Fixed Label Volume: ", self.fixedLabelSelector)
    self.registrationGroupBoxLayout.addRow("Targets: ", self.fiducialSelector)
    self.registrationGroupBoxLayout.addRow("Image Registration Tool:", self.imageRegistrationToolSelector)
    self.registrationGroupBoxLayout.addRow(self.applyRegistrationButton)
    self.layout.addWidget(self.registrationGroupBox)
    self.layout.addStretch()
    self.setupConnections()
    self.onImageRegistrationToolSelected(0)

  def setupConnections(self):
    self.applyRegistrationButton.clicked.connect(self.runRegistration)
    self.imageRegistrationToolSelector.currentIndexChanged.connect(self.onImageRegistrationToolSelected)
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
    self.applyRegistrationButton.enabled = self.isRegistrationPossible() and self.imageRegistrationTool is not None

  def onImageRegistrationToolSelected(self, index):
    text = self.imageRegistrationToolSelector.itemText(index)
    imageRegistrationTool = registration.__tools__[text]
    if imageRegistrationTool.isToolAvailable():
      self.imageRegistrationTool = imageRegistrationTool
    else:
      logging.info("Selected algorithm {} seems not to be available due to missing dependencies".format(text))
      self.imageRegistrationTool = None
    self.updateButton()

  def isRegistrationPossible(self):
    return self.movingVolumeSelector.currentNode() and self.fixedVolumeSelector.currentNode() and \
           self.fixedLabelSelector.currentNode() and self.movingLabelSelector.currentNode()

  def runRegistration(self):
    logging.debug("Starting Registration")
    self.progress = self.createProgressDialog(value=1, maximum=4)

    logic = SliceTrackerRegistrationLogic(self.imageRegistrationTool())

    parameterNode = logic.initializeParameterNode(self.fixedVolumeSelector.currentNode(),
                                                  self.fixedLabelSelector.currentNode(),
                                                  self.movingVolumeSelector.currentNode(),
                                                  self.movingLabelSelector.currentNode(),
                                                  self.fiducialSelector.currentNode())

    logic.run(parameterNode, result=RegistrationResult("{}: RegistrationResult".format(str(self.counter))),
                   progressCallback=self.updateProgressBar)
    self.progress.close()
    self.counter += 1

  @onReturnProcessEvents
  def updateProgressBar(self, **kwargs):
    if self.progress:
      for key, value in kwargs.iteritems():
        if hasattr(self.progress, key):
          setattr(self.progress, key, value)


class SliceTrackerRegistrationLogic(ScriptedLoadableModuleLogic):

  @staticmethod
  def initializeParameterNode(fixedVolume, fixedLabel, movingVolume, movingLabel, targets=None):
    parameterNode = slicer.vtkMRMLScriptedModuleNode()
    parameterNode.SetAttribute('FixedImageNodeID', fixedVolume.GetID())
    parameterNode.SetAttribute('FixedLabelNodeID', fixedLabel.GetID())
    parameterNode.SetAttribute('MovingImageNodeID', movingVolume.GetID())
    parameterNode.SetAttribute('MovingLabelNodeID', movingLabel.GetID())
    if targets:
      parameterNode.SetAttribute('TargetsNodeID', targets.GetID())
    return parameterNode

  def __init__(self, algorithm):
    ScriptedLoadableModuleLogic.__init__(self)
    self.imageRegistrationTool = algorithm

  def run(self, parameterNode, result, progressCallback=None):
    self.imageRegistrationTool.run(parameterNode, result, progressCallback)

  def getResult(self):
    return self.imageRegistrationTool.registrationResult


def main(argv):
  try:
    parser = argparse.ArgumentParser(description="SliceTracker Registration")
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
    parser.add_argument("-t", "--tool", dest="tool", metavar="PATH", default="BRAINS",
                        choices=registration.__tools__.keys(), required=False,
                        help="Image Registration Tool to be used for registration (default: %(default)s)")

    args = parser.parse_args(argv)

    for inputFile in [args.fixed_label, args.moving_label, args.fixed_volume, args.moving_volume]:
      if not os.path.isfile(inputFile):
        raise AttributeError, "File not found: %s" % inputFile

    success, fixedLabel = slicer.util.loadLabelVolume(args.fixed_label, returnNode=True)
    success, movingLabel = slicer.util.loadLabelVolume(args.moving_label, returnNode=True)
    success, fixedVolume = slicer.util.loadVolume(args.fixed_volume, returnNode=True)
    success, movingVolume = slicer.util.loadVolume(args.moving_volume, returnNode=True)

    imageRegistrationTool = registration.__tools__[args.tool]

    if not imageRegistrationTool.isToolAvailable():
      raise RuntimeError("Image Registration Tool {} cannot be executed due to missing dependencies.".format(args.tool))

    logic = SliceTrackerRegistrationLogic(imageRegistrationTool())
    parameterNode = logic.initializeParameterNode(fixedVolume, fixedLabel, movingVolume, movingLabel)
    logic.run(parameterNode, result=RegistrationResult("01: RegistrationResult"))

    if args.output_directory != "-":
      logic.getResult().save(args.output_directory)

  except Exception, e:
    print e
  sys.exit(0)

if __name__ == "__main__":
  main(sys.argv[1:])
