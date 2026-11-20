# BeamcamCamera.dll: the virtual camera's media source (loaded by Windows' Frame Server) and the
# decoding the app needs (called from C#). Build with Visual Studio 2022's C++ tools:
#   cmake -S native -B native/build -A x64 && cmake --build native/build --config Release
cmake_minimum_required(VERSION 3.21)
project(Beamcam LANGUAGES CXX RC)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")  # no VC++ redistributable needed

add_library(convert STATIC src/convert.cpp)

add_library(BeamcamCamera SHARED
    src/camera.cpp
    src/dllmain.cpp
    src/media_source.cpp
    src/trace.cpp
    src/video.cpp
    src/beamcam_camera.def
    src/version.rc)
target_compile_definitions(BeamcamCamera PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_compile_options(BeamcamCamera PRIVATE /W4 /WX /permissive- /EHsc)
target_link_libraries(BeamcamCamera PRIVATE convert mfplat mfuuid mf mfsensorgroup wmcodecdspuuid strmiids propsys advapi32 ole32)

enable_testing()
add_executable(convert_tests tests/convert_tests.cpp)
target_link_libraries(convert_tests PRIVATE convert)
add_test(NAME convert_tests COMMAND convert_tests)

# A small video app for tests: opens the "Beamcam" camera like any other app and saves a frame.
add_executable(beamcam-capture tests/capture.cpp)
target_compile_definitions(beamcam-capture PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(beamcam-capture PRIVATE mfplat mfuuid mf mfreadwrite ole32)
