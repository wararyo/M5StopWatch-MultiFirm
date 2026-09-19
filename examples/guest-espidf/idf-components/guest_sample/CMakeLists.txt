# Native IDF entry point; the same two translation units as the PIO sample.
idf_component_register(SRCS "../../src/main.cpp" "../../src/second.cpp"
    INCLUDE_DIRS "../../src"
    REQUIRES multifirm M5Unified nvs_flash esp_timer)
