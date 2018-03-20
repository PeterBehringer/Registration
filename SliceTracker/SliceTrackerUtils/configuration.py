import ConfigParser
import inspect, os
from SlicerDevelopmentToolboxUtils.mixins import ModuleWidgetMixin


class SliceTrackerConfiguration(ModuleWidgetMixin):

  def __init__(self, moduleName, configFile):
    self.moduleName = moduleName
    self.configFile = configFile
    self.loadConfiguration()

  def loadConfiguration(self):

    config = ConfigParser.RawConfigParser()
    config.read(self.configFile)

    if not self.getSetting("ZFrame_Registration_Class_Name"):
      self.setSetting("ZFrame_Registration_Class_Name", config.get('ZFrame Registration', 'class'))

    if not self.getSetting("Prostate_Registration_Tool"):
      self.setSetting("Prostate_Registration_Tool", config.get('Prostate Registration', 'Tool'))

    if not self.getSetting("Prostate_Registration_Fallback"):
      self.setSetting("Prostate_Registration_Fallback", config.get('Prostate Registration', 'Fallback'))

    if not self.getSetting("PLANNING_IMAGE"):
      self.setSetting("PLANNING_IMAGE", config.get('Series Descriptions', 'PLANNING_IMAGE'))
    if not self.getSetting("COVER_PROSTATE"):
      self.setSetting("COVER_PROSTATE", config.get('Series Descriptions', 'COVER_PROSTATE'))
    if not self.getSetting("COVER_TEMPLATE"):
      self.setSetting("COVER_TEMPLATE", config.get('Series Descriptions', 'COVER_TEMPLATE'))
    if not self.getSetting("NEEDLE_IMAGE"):
      self.setSetting("NEEDLE_IMAGE", config.get('Series Descriptions', 'NEEDLE_IMAGE'))
    if not self.getSetting("VIBE_IMAGE"):
      self.setSetting("VIBE_IMAGE", config.get('Series Descriptions', 'VIBE_IMAGE'))
    if not self.getSetting("OTHER_IMAGE"):
      self.setSetting("OTHER_IMAGE", config.get('Series Descriptions', 'OTHER_IMAGE'))

    if not self.getSetting("SERIES_TYPES"):
      seriesTypes = [config.get('Series Descriptions',x) for x in ['COVER_TEMPLATE', 'COVER_PROSTATE', 'NEEDLE_IMAGE',
                                                                   'VIBE_IMAGE', 'OTHER_IMAGE']]
      self.setSetting("SERIES_TYPES", seriesTypes)

    if not self.getSetting("Rating_Enabled"):
      self.setSetting("Rating_Enabled", config.getboolean('Rating', 'Enabled'))
    if not self.getSetting("Maximum_Rating_Score"):
      self.setSetting("Maximum_Rating_Score", config.getint('Rating', 'Maximum_Score'))

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
