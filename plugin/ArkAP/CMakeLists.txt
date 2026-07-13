# ArkAP plugin - build scaffold.
# ArkServerApi plugins are normally built as a Visual Studio DLL against the
# ArkServerApi SDK. This CMake is a convenience; adjust paths to your SDK install.
cmake_minimum_required(VERSION 3.20)
project(ArkAP CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(ArkAP SHARED
    src/PluginMain.cpp
    src/Tables.cpp
)

# --- ArkServerApi SDK (vendored clone) ---------------------------------------
# Headers + ArkApi.lib both live in the cloned framework repo.
set(ARKAPI_SDK "${CMAKE_SOURCE_DIR}/../../vendor/Framework-ArkServerApi" CACHE PATH "ArkApi framework root")
target_include_directories(ArkAP PRIVATE
    "${ARKAPI_SDK}/version/Core/Public"
    "src"
)
target_link_libraries(ArkAP PRIVATE "${ARKAPI_SDK}/out_lib/ArkApi.lib")

# ArkServerApi plugins are x64 only.
set_target_properties(ArkAP PROPERTIES OUTPUT_NAME "ArkAP")
