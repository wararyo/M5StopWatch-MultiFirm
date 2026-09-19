cmake_minimum_required(VERSION 3.16)
if(NOT MULTIFIRM_PLATFORMIO)
    # Native IDF uses a named component wrapper; PIO discovers lib_deps itself.
    list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/idf-components")
    set(M5_LIB_DIR "${CMAKE_CURRENT_LIST_DIR}/.pio/libdeps/guest-espidf" CACHE PATH
        "Directory containing M5Unified and M5GFX checkouts")
    list(APPEND EXTRA_COMPONENT_DIRS "${M5_LIB_DIR}/M5Unified" "${M5_LIB_DIR}/M5GFX")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(multifirm-guest)
if(NOT MULTIFIRM_PLATFORMIO)
    add_custom_target(multifirm_size_check ALL
        COMMAND "${PYTHON}" "${CMAKE_CURRENT_LIST_DIR}/../upload_guard.py"
            "${CMAKE_BINARY_DIR}/${PROJECT_NAME}.bin"
        DEPENDS gen_project_binary
        VERBATIM)
endif()
