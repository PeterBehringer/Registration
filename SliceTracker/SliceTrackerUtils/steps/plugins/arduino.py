import vtk
import ast
import sys
import os
import time
import logging

sys.path.append(os.path.join(os.path.dirname(__file__),'../../../..','externalPackages/pyserial-2.7'))
import serial

from ..base import SliceTrackerPlugin, SliceTrackerLogicBase


class SliceTrackerArduinoLogic(SliceTrackerLogicBase):

  def __init__(self):
    super(SliceTrackerArduinoLogic, self).__init__()


class SliceTrackerArduinoPlugin(SliceTrackerPlugin):

  LogicClass = SliceTrackerArduinoLogic
  NAME = "CaseManager"

  def __init__(self):
    super(SliceTrackerArduinoPlugin, self).__init__()

  def clearData(self):
    self.update()

  def addSessionObservers(self):
    super(SliceTrackerArduinoPlugin, self).addSessionObservers()
    self.session.addEventObserver(self.session.TargetSelectionEvent, self.onTargetSelectionChanged)

  def removeSessionEventObservers(self):
    super(SliceTrackerArduinoPlugin, self).removeSessionObservers()
    self.session.removeEventObserver(self.session.TargetSelectionEvent, self.onTargetSelectionChanged)

  @vtk.calldata_type(vtk.VTK_STRING)
  def onTargetSelectionChanged(self, caller, event, callData):
    if not self.active:
      return

    info = ast.literal_eval(callData)
    if not info['nodeId'] or info['index'] == -1:
      # hide guidance
      return

    hole = info['hole'].replace("(", "").replace(")", "")

    port = self.getSetting("Arduino_Port")

    try:
      ard = serial.Serial(port, 9600, timeout=5)

      ard.flush()
      # setTemp1 = str(rowstring + ", " + columnstring + "X")
      print ("Python value sent: ")
      print (hole + "X")
      ard.write(hole + "X")
      time.sleep(3)  # I shortened this to match the new value in your Arduino code

      # Serial read section
      msg = ard.read(ard.inWaiting())  # read all characters in buffer
      print ("Message from Arduino: ")
      print (msg)
      print ("Exiting")
    except OSError:
      logging.debug("No Arduino connection available. Check port")
