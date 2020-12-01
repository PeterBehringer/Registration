__author__ = 'Christian'

import sys, getopt, os, dicom
import time

class NotDirectoryError(Exception):
  pass

class DICOMDirectoryObserver(object):

  def __init__(self, directory, host, port):
    if not os.path.isdir(directory):
      raise NotDirectoryError("The directory is actually no directory")
    self.directory = directory
    self.host = host
    self.port = port
    self.files = set()

  def listdirRecursive(self, rootDir):
    files = []
    for root, subFolders, dirFiles in os.walk(rootDir):
      for f in dirFiles:
        try:
          fileName = os.path.join(root,f)
          dicom.read_file(fileName)
          files.append(fileName)
        except:
          pass
    return files

  def watch(self, secondsToWait=1):
    while True:
      currentFiles = self.listdirRecursive(self.directory)
      if len(self.files) < len(currentFiles):
        print("Number of files changed")
        for newFile in self.getNewFiles(currentFiles):
          self.storeSCU(newFile)
      time.sleep(secondsToWait)

  def getNewFiles(self, files):
    newFiles = []
    for currentFile in files:
      if currentFile not in self.files:
        newFiles.append(currentFile)
    return newFiles

  def storeSCU(self, fileName):
    cmd = ('storescu ' + self.host + ' ' + self.port + ' ' + fileName)
    print(cmd)
    os.system(cmd)
    self.files.add(fileName)

def main(argv):
   watchDirectory = ''
   host = 'localhost'
   port = '11112'
   interval = 1
   try:
      opts, args = getopt.getopt(argv,"i:d:h:p:?",["help","directory=","host=","port=","interval="])
   except getopt.GetoptError:
      print('watch.py -d <watchDirectory> -h <host> -p <port> -i <interval [in seconds]>')
      sys.exit(2)
   for opt, arg in opts:
      if opt in ("-?", "--help"):
         print('watch.py -d <watchDirectory> -h <host> -p <port>')
         sys.exit()
      elif opt in ("-d", "--directory"):
         watchDirectory = arg
      elif opt in ("-h", "--host"):
         host = arg
      elif opt in ("-p", "--port"):
         port = arg
      elif opt in ("-i", "--interval"):
         interval = int(arg)
   if watchDirectory and host and port:
     print('Directory to watch is: ', watchDirectory)
     print('Host to send DICOM files to is: ', host)
     print('Port to send DICOM files to is: ', port)

     watcher = DICOMDirectoryObserver(directory=watchDirectory, host=host, port=port)
     print("Will watch!")
     watcher.watch(interval)

if __name__ == "__main__":
   main(sys.argv[1:])


#client use:  $ sudo storescp -v -p 104
#python watch.py -d "/Users/Christian/Documents/TEST1" -h localhost -p 104 -i 1
