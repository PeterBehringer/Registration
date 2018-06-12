cmake_minimum_required(VERSION 2.8.9)

#-----------------------------------------------------------------------------
# Extension meta-information
set(EXTENSION_NAME SliceTracker)
set(EXTENSION_HOMEPAGE "https://github.com/SlicerProstate/SliceTracker")
set(EXTENSION_CATEGORY "IGT")
set(EXTENSION_CONTRIBUTORS "Christian Herz (SPL), Peter Behringer (SPL), Kyle MacNeil (Med-i Lab, Queen's; SPL), Andrey Fedorov (SPL)")
set(EXTENSION_DESCRIPTION "This extension provides modules to support in-bore MRI-guided prostate biopsy.")
set(EXTENSION_ICONURL "https://www.slicer.org/w/images/b/b1/SliceTracker_Logo_1.1_128x128.png")
set(EXTENSION_SCREENSHOTURLS "http://wiki.slicer.org/slicerWiki/images/7/78/Slicetracker.gif")
set(EXTENSION_STATUS "beta")
set(EXTENSION_DEPENDS "SlicerDevelopmentToolbox mpReview SegmentEditorExtraEffects DeepInfer SlicerZFrameRegistration")

#-----------------------------------------------------------------------------
find_package(Slicer REQUIRED)
include(${Slicer_USE_FILE})

add_subdirectory(SliceTracker)


#-----------------------------------------------------------------------------
if(BUILD_TESTING)

  # Register the unittest subclass in the main script as a ctest.
  # Note that the test will also be available at runtime.
  # slicer_add_python_unittest(SCRIPT ${MODULE_NAME}.py)

  # Additional build-time testing
  # add_subdirectory(Testing)
endif()

#-----------------------------------------------------------------------------
include(${Slicer_EXTENSION_CPACK})
