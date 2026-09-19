# ESP-IDF component (e.g. components/multifirm). Native tests live in tests/CMakeLists.txt.
# IDF 4.4 supplies partition APIs through spi_flash and GPIO through driver.
set(multifirm_requires app_update bootloader_support spi_flash mbedtls esp_system esp_timer)
if(IDF_VERSION_MAJOR GREATER_EQUAL 5)
    list(APPEND multifirm_requires esp_partition)
endif()
if(IDF_VERSION_MAJOR GREATER 5 OR (IDF_VERSION_MAJOR EQUAL 5 AND IDF_VERSION_MINOR GREATER_EQUAL 3))
    list(APPEND multifirm_requires esp_driver_gpio)
else()
    list(APPEND multifirm_requires driver)
endif()
idf_component_register(SRCS "${CMAKE_CURRENT_LIST_DIR}/src/multifirm_host.cpp"
    INCLUDE_DIRS "${CMAKE_CURRENT_LIST_DIR}/src" REQUIRES ${multifirm_requires})
