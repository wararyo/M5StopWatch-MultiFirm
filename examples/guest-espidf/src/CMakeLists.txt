# PlatformIO supplies the lib_deps include paths and libraries.
idf_component_register(SRCS "main.cpp" "second.cpp" INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_timer)
