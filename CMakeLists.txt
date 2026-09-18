# ESP-IDF component (e.g. components/multifirm). Native tests live in tests/CMakeLists.txt.
# Phase 1 provides only the IDF-independent layout header; guest/host sources come later.
idf_component_register(INCLUDE_DIRS "src")
