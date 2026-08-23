# PC client for the arduino-hub command channel (C++ / hidapi).
#
# Target headers live in target/ and are (re)written by
# `arduino-hub patch` — run it once before building so that
# target/mouse_commands.h and target/command_channel.h exist.
# Use `arduino-hub patch --client-out <dir>` to export them elsewhere.
#
# Build (Windows, CMake >= 3.16):
#   cmake -B build
#   cmake --build build --config Release
#
# hidapi is fetched automatically from libusb/hidapi.

set(CMAKE_POLICY_VERSION_MINIMUM 3.5.0)
cmake_minimum_required(VERSION 3.16)
project(hubclient CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)
FetchContent_Declare(
  hidapi
  GIT_REPOSITORY https://github.com/libusb/hidapi.git
  GIT_TAG        hidapi-0.15.0
)
FetchContent_MakeAvailable(hidapi)

add_library(hubclient INTERFACE)
target_include_directories(hubclient INTERFACE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/target
  ${hidapi_SOURCE_DIR}
)
target_link_libraries(hubclient INTERFACE hidapi::hidapi)

add_executable(client_demo examples/client_demo.cpp)
target_link_libraries(client_demo PRIVATE hubclient)

# Copy the hidapi runtime next to the executable so it runs without
# touching PATH (Windows produces hidapi.dll).
add_custom_command(TARGET client_demo POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_if_different
          $<TARGET_FILE:hidapi::hidapi>
          $<TARGET_FILE_DIR:client_demo>
  COMMENT "Copying hidapi runtime next to client_demo"
  VERBATIM
)
