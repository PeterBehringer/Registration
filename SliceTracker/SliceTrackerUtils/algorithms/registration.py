from abc import abstractmethod, ABCMeta
import logging
import slicer

from SlicerDevelopmentToolboxUtils.mixins import ModuleLogicMixin


class ImageRegistrationTool(ModuleLogicMixin):

  __metaclass__ = ABCMeta

  NAME = None

  @staticmethod
  def isToolAvailable():
    raise NotImplementedError

  def __init__(self):
    self.registrationResult = None
    self.progressCallback = None
    if not self.NAME:
      raise NotImplementedError("Concrete implementations of {} need to have a NAME assigned".format(self.__class__.__name__))

  @abstractmethod
  def run(self, parameterNode, registrationResult, progressCallback=None):
    pass

  def _processParameterNode(self, parameterNode):
    result = self.registrationResult
    result.volumes.fixed = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('FixedImageNodeID'))
    result.labels.fixed = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('FixedLabelNodeID'))
    result.labels.moving = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingLabelNodeID'))
    movingVolume = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingImageNodeID'))
    result.volumes.moving = self.volumesLogic.CloneVolume(slicer.mrmlScene, movingVolume,
                                                          "{}-temp-movingVolume".format(movingVolume.GetName()))

    logging.debug("Fixed Image Name: %s" % result.volumes.fixed.GetName())
    logging.debug("Fixed Label Name: %s" % result.labels.fixed.GetName())
    logging.debug("Moving Image Name: %s" % movingVolume.GetName())
    logging.debug("Moving Label Name: %s" % result.labels.moving.GetName())
    initialTransform = parameterNode.GetAttribute('InitialTransformNodeID')
    if initialTransform:
      initialTransform = slicer.mrmlScene.GetNodeByID(initialTransform)
      logging.debug("Initial Registration Name: %s" % initialTransform.GetName())

  def createVolumeAndTransformNodes(self, registrationTypes, prefix, suffix="",
                                    deformableRegistrationNodeClass=slicer.vtkMRMLBSplineTransformNode):
    for regType in registrationTypes:
      self.registrationResult.setVolume(regType, self.createScalarVolumeNode(prefix + '-VOLUME-' + regType + suffix))
      transformName = prefix + '-TRANSFORM-' + regType + suffix
      transform = self.createNode(deformableRegistrationNodeClass, transformName) if regType == 'bSpline' \
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

  def updateProgress(self, **kwargs):
    if self.progressCallback:
      self.progressCallback(**kwargs)


class BRAINSRegistration(ImageRegistrationTool):

  NAME = "BRAINSFit"

  @staticmethod
  def isToolAvailable():
    return hasattr(slicer.modules, "brainsfit")

  def __init__(self):
    super(BRAINSRegistration, self).__init__()

  def run(self, parameterNode, result, progressCallback=None):
    self.progressCallback = progressCallback
    self.registrationResult = result
    self._processParameterNode(parameterNode)

    registrationTypes = ['rigid', 'affine', 'bSpline']
    self.createVolumeAndTransformNodes(registrationTypes, prefix=str(result.seriesNumber), suffix=result.suffix)

    self.__runRigidRegistration()
    self.__runAffineRegistration()
    self.__runBSplineRegistration()

    targetsNodeID = parameterNode.GetAttribute('TargetsNodeID')
    if targetsNodeID:
      result.targets.original = slicer.mrmlScene.GetNodeByID(targetsNodeID)
      self.transformTargets(registrationTypes, result.targets.original, str(result.seriesNumber), suffix=result.suffix)
    slicer.mrmlScene.RemoveNode(result.volumes.moving)
    result.volumes.moving = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingImageNodeID'))
    return result

  def __runRigidRegistration(self):
    self.updateProgress(labelText='\nRigid registration', value=2)
    reg = BRAINSRigidRegistration(self.registrationResult.volumes.fixed, self.registrationResult.volumes.moving,
                                  self.registrationResult.labels.fixed, self.registrationResult.labels.moving,
                                  self.registrationResult.transforms.rigid, self.registrationResult.volumes.rigid)
    reg.run()
    self.registrationResult.cmdArguments += reg.paramsInfo

  def __runAffineRegistration(self):
    self.updateProgress(labelText='\nAffine registration', value=2)
    reg = BRAINSAffineRegistration(self.registrationResult.volumes.fixed, self.registrationResult.volumes.moving,
                                   self.registrationResult.labels.fixed, self.registrationResult.labels.moving,
                                   self.registrationResult.transforms.affine, self.registrationResult.volumes.affine,
                                   self.registrationResult.transforms.rigid)
    reg.run()
    self.registrationResult.cmdArguments += reg.paramsInfo

  def __runBSplineRegistration(self):
    self.updateProgress(labelText='\nBSpline registration', value=3)

    reg = BRAINSBSplineRegistration(self.registrationResult.volumes.fixed, self.registrationResult.volumes.moving,
                                    self.registrationResult.labels.fixed, self.registrationResult.labels.moving,
                                    self.registrationResult.transforms.bSpline, self.registrationResult.volumes.bSpline,
                                    self.registrationResult.transforms.affine)
    reg.run()
    self.registrationResult.cmdArguments += reg.paramsInfo

    self.updateProgress(labelText='\nCompleted registration', value=4)


class IBRAINSRegistrationType(object):

  __metaclass__ = ABCMeta

  def __init__(self, fixedVolume, movingVolume, fixedLabel, movingLabel, outputTransform, outputVolume=None,
               initialTransform=None):
    self.params = dict(fixedVolume=fixedVolume,
                       movingVolume=movingVolume,
                       fixedBinaryVolume=fixedLabel,
                       movingBinaryVolume=movingLabel,
                       outputTransform=outputTransform.GetID())
    if outputVolume:
      self.params['outputVolume'] = outputVolume.GetID()
    if initialTransform:
      self.params['initialTransform'] = initialTransform

  @abstractmethod
  def run(self):
    pass


class BRAINSRigidRegistration(IBRAINSRegistrationType):

  def run(self):
    self.params.update({'maskProcessingMode': "ROI",
                        'initializeTransformMode': "useCenterOfROIAlign",
                        'useRigid': True})
    slicer.cli.run(slicer.modules.brainsfit, None, self.params, wait_for_completion=True)
    self.paramsInfo = "Rigid Registration Parameters: %s" % str(self.params) + "\n\n"


class BRAINSROIInitializer(IBRAINSRegistrationType):

  def run(self):
    self.params.update({'maskProcessingMode': "ROI",
                        'initializeTransformMode': "useCenterOfROIAlign"})
    slicer.cli.run(slicer.modules.brainsfit, None, self.params, wait_for_completion=True)
    self.paramsInfo = "ROI Initializer Parameters: %s" % str(self.params) + "\n\n"


class BRAINSAffineRegistration(IBRAINSRegistrationType):

  def run(self):
    self.params.update({'maskProcessingMode': "ROI",
                        'useAffine': True})
    slicer.cli.run(slicer.modules.brainsfit, None, self.params, wait_for_completion=True)
    self.paramsInfo = "Affine Registration Parameters: %s" % str(self.params) + "\n\n"


class BRAINSBSplineRegistration(IBRAINSRegistrationType):

  def run(self):
    self.params.update({'useROIBSpline': True,
                        'useBSpline': True,
                        'splineGridSize': "3,3,3",
                        'maskProcessing': "ROI",
                        'minimumStepLength': "0.005",
                        'maximumStepLength': "0.2",
                        'costFunctionConvergenceFactor': "1.00E+09",
                        'maskProcessingMode': "ROI"})
    slicer.cli.run(slicer.modules.brainsfit, None, self.params, wait_for_completion=True)
    self.paramsInfo = "BSpline Registration Parameters: %s" % str(self.params) + "\n\n"


class ElastixRegistration(ImageRegistrationTool):

  NAME = "Elastix"

  @staticmethod
  def isToolAvailable():
    try:
      from Elastix import ElastixLogic
    except ImportError:
      return False
    return True

  def __init__(self):
    super(ElastixRegistration, self).__init__()
    import os, sys
    self.parametersDirectory = os.path.join(os.path.dirname(sys.modules[self.__module__].__file__), '..', '..',
                                            'Resources', 'ElastixRegistrationParameters')

  def hardenTransform(self, nodes, transform):
    tfmLogic = slicer.modules.transforms.logic()
    for node in nodes:
      node.SetAndObserveTransformNodeID(transform.GetID())
      tfmLogic.hardenTransform(node)

  def run(self, parameterNode, result, progressCallback=None):
    self.progressCallback = progressCallback
    self.registrationResult = result
    self._processParameterNode(parameterNode)

    registrationTypes = ['bSpline']
    self.createVolumeAndTransformNodes(registrationTypes, prefix=str(result.seriesNumber), suffix=result.suffix,
                                       deformableRegistrationNodeClass=slicer.vtkMRMLTransformNode)

    initTransform = self.__runBRAINSROIInitializer()
    self.hardenTransform(nodes=[self.registrationResult.volumes.moving, self.registrationResult.labels.moving],
                         transform=initTransform)
    self.__runElastixRegistration()

    initTransform.Inverse()
    self.hardenTransform(nodes=[self.registrationResult.labels.moving], transform=initTransform)
    initTransform.Inverse()
    self.hardenTransform(nodes=[initTransform], transform=self.registrationResult.transforms.bSpline)

    initTransform.SetName(self.registrationResult.transforms.bSpline.GetName())
    slicer.mrmlScene.RemoveNode(self.registrationResult.transforms.bSpline)
    self.registrationResult.transforms.bSpline = initTransform

    targetsNodeID = parameterNode.GetAttribute('TargetsNodeID')
    if targetsNodeID:
      result.targets.original = slicer.mrmlScene.GetNodeByID(targetsNodeID)
      self.transformTargets(registrationTypes, result.targets.original, str(result.seriesNumber), suffix=result.suffix)

    slicer.mrmlScene.RemoveNode(result.volumes.moving)
    result.volumes.moving = slicer.mrmlScene.GetNodeByID(parameterNode.GetAttribute('MovingImageNodeID'))

  def __runBRAINSROIInitializer(self):
    self.updateProgress(labelText='\nROI initialization', value=2)

    transform = self.createLinearTransformNode(self.registrationResult.volumes.moving.GetName()+"_temp_transform")
    slicer.mrmlScene.AddNode(transform)
    rigidRegistration = BRAINSROIInitializer(self.registrationResult.volumes.fixed,
                                             self.registrationResult.volumes.moving,
                                             self.registrationResult.labels.fixed,
                                             self.registrationResult.labels.moving,
                                             outputTransform=transform)
    rigidRegistration.run()
    self.registrationResult.cmdArguments += rigidRegistration.paramsInfo
    return transform

  def __runElastixRegistration(self):
    self.updateProgress(labelText='\nElastix registration', value=3)
    from Elastix import ElastixLogic
    import os
    logic = ElastixLogic()
    pFileNames = ["Parameters_Rigid.txt", "Parameters_BSpline.txt"]
    parameterFileNames = [os.path.join(self.parametersDirectory, pFile) for pFile in pFileNames]
    logic.registerVolumes(self.registrationResult.volumes.fixed, self.registrationResult.volumes.moving,
                          parameterFileNames, self.registrationResult.volumes.bSpline,
                          self.registrationResult.transforms.bSpline,
                          self.registrationResult.labels.fixed, self.registrationResult.labels.moving)
    self.updateProgress(labelText='\nCompleted registration', value=4)


__tools__ = {'BRAINS':BRAINSRegistration, 'Elastix':ElastixRegistration}

