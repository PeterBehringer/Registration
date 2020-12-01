import configparser
import inspect, os
from SlicerDevelopmentToolboxUtils.mixins import ModuleWidgetMixin
from .constants import SliceTrackerConstants as constants


class SliceTrackerConfiguration(ModuleWidgetMixin):

  def __init__(self, moduleName, configFile):
    self.moduleName = moduleName
    self.configFile = configFile
    self.loadConfiguration()

  def loadConfiguration(self):

    config = configparser.RawConfigParser()
    config.read(self.configFile)

    if not self.getSetting("ZFrame_Registration_Class_Name"):
      self.setSetting("ZFrame_Registration_Class_Name", config.get('ZFrame Registration', 'class'))

    if not self.getSetting("PLANNING_IMAGE_PATTERN"):
      self.setSetting("PLANNING_IMAGE_PATTERN", config.get('Series Descriptions', 'PLANNING_IMAGE_PATTERN'))
    if not self.getSetting("COVER_PROSTATE_PATTERN"):
      self.setSetting("COVER_PROSTATE_PATTERN", config.get('Series Descriptions', 'COVER_PROSTATE_PATTERN'))
    if not self.getSetting("COVER_TEMPLATE_PATTERN"):
      self.setSetting("COVER_TEMPLATE_PATTERN", config.get('Series Descriptions', 'COVER_TEMPLATE_PATTERN'))
    if not self.getSetting("NEEDLE_IMAGE_PATTERN"):
      self.setSetting("NEEDLE_IMAGE_PATTERN", config.get('Series Descriptions', 'NEEDLE_IMAGE_PATTERN'))
    if not self.getSetting("VIBE_IMAGE_PATTERN"):
      self.setSetting("VIBE_IMAGE_PATTERN", config.get('Series Descriptions', 'VIBE_IMAGE_PATTERN'))

    seriesTypes = [constants.COVER_TEMPLATE, constants.COVER_PROSTATE, constants.GUIDANCE_IMAGE,
                   constants.VIBE_IMAGE, constants.OTHER_IMAGE]

    self.setSetting("SERIES_TYPES", seriesTypes)

    if not self.getSetting("Color_File_Name") or not os.path.exists(self.getSetting("Color_File_Name")):
      colorFilename = config.get('Color File', 'FileName')
      self.setSetting("Color_File_Name", os.path.join(os.path.dirname(inspect.getfile(self.__class__)),
                                                      '../Resources/Colors', colorFilename))

    if not self.getSetting("Segmentation_Color_Name"):
      segmentedColorName = config.get('Color File', 'SegmentedColorName')
      self.setSetting("Segmentation_Color_Name", segmentedColorName)

    if not self.getSetting("DEFAULT_EVALUATION_LAYOUT"):
      self.setSetting("DEFAULT_EVALUATION_LAYOUT", config.get('Evaluation', 'Default_Layout'))

    if not self.getSetting("Demo_Mode"):
      self.setSetting("Demo_Mode", config.get('Modes', 'Demo_Mode'))

    if not self.getSetting("Use_Deep_Learning"):
      self.setSetting("Use_Deep_Learning", config.get('Segmentation', 'Use_Deep_Learning'))

    if not self.getSetting("Incoming_DICOM_Port"):
      self.setSetting("Incoming_DICOM_Port", config.get('DICOM', 'Incoming_Port'))

    if not self.getSetting("CASE_NUMBER_OF_DIGITS"):
      self.setSetting("CASE_NUMBER_OF_DIGITS", config.get('General', 'CASE_NUMBER_OF_DIGITS'))

    self.replaceOldValues()

  def replaceOldValues(self):
    for setting in ['PLANNING_IMAGE', 'COVER_TEMPLATE', 'COVER_PROSTATE', 'NEEDLE_IMAGE', 'VIBE_IMAGE']:
      if self.getSetting(setting):
        self.setSetting(setting+"_PATTERN", self.getSetting(setting))
        self.removeSetting(setting)

    for other in ['OTHER_IMAGE', 'Rating_Enabled', 'Maximum_Rating_Score']:
      if self.getSetting(other):
        self.removeSetting(other)
